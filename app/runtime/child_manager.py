"""
Per-child process owner (Phases 1–3).

Owns exactly one multiprocessing.Process. Never reuses Process objects.
Auto-restart is driven by RuntimeSupervisor.request_restart() — not HealthMonitor.
"""

from __future__ import annotations

import logging
import multiprocessing
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.runtime.events import ChildFailed, ChildStarted, ChildStopped, RuntimeEventBus
from app.runtime.exit_codes import EXPLICIT_LOCK_BUSY_EXITCODE
from app.runtime.job_object import JobObjectBase, JobObjectError
from app.runtime.lifecycle import ChildState
from app.runtime.process_descriptor import ProcessDescriptor
from app.runtime.restart_backoff import BackoffConfig, BackoffState, monotonic_now
from app.runtime.restart_policy import ExitClass

logger = logging.getLogger("lutron_runtime.child_manager")


@dataclass(frozen=True)
class ChildStatus:
    """Public snapshot — never exposes the Process object."""

    name: str
    state: ChildState
    enabled: bool
    pid: Optional[int]
    exitcode: Optional[int]
    alive: bool
    error: Optional[str] = None
    lock_aware: bool = False
    restart_policy: str = "on_failure"
    generation: int = 0
    exit_class: Optional[str] = None
    consecutive_failures: int = 0
    last_backoff_seconds: float = 0.0
    reconcile_attempts: int = 0
    last_reconcile_decision: Optional[str] = None
    reconcile_reason: Optional[str] = None
    terminate_reason: Optional[str] = None
    proof_method: Optional[str] = None
    foreign_mutex_holder: bool = False
    process_create_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "enabled": self.enabled,
            "pid": self.pid,
            "exitcode": self.exitcode,
            "alive": self.alive,
            "error": self.error,
            "lock_aware": self.lock_aware,
            "restart_policy": self.restart_policy,
            "generation": self.generation,
            "exit_class": self.exit_class,
            "consecutive_failures": self.consecutive_failures,
            "last_backoff_seconds": self.last_backoff_seconds,
            "reconcile_attempts": self.reconcile_attempts,
            "last_reconcile_decision": self.last_reconcile_decision,
            "reconcile_reason": self.reconcile_reason,
            "terminate_reason": self.terminate_reason,
            "proof_method": self.proof_method,
            "foreign_mutex_holder": self.foreign_mutex_holder,
            "process_create_time": self.process_create_time,
        }


class ChildManager:
    """Owns Process lifecycle for one descriptor."""

    def __init__(
        self,
        descriptor: ProcessDescriptor,
        *,
        backoff_config: Optional[BackoffConfig] = None,
        event_bus: Optional[RuntimeEventBus] = None,
        job: Optional[JobObjectBase] = None,
    ) -> None:
        self._descriptor = descriptor
        self._backoff_config = backoff_config or BackoffConfig.from_env()
        self._backoff = BackoffState()
        self._event_bus = event_bus
        self._job = job
        self._process: Optional[multiprocessing.Process] = None
        self._state = ChildState.CREATED
        self._error: Optional[str] = None
        self._last_exitcode: Optional[int] = None
        self._last_exit_class: Optional[ExitClass] = None
        self._intentional_stop = False
        self._generation = 0
        self._restart_requested = False
        self._spawned_at: Optional[float] = None
        self._startup_succeeded = False
        self._job_assigned = False
        self._reconcile_attempts = 0
        self._last_reconcile_decision: Optional[str] = None
        self._adopted_pid: Optional[int] = None
        self._reconcile_in_progress = False
        self._process_identity = None  # Optional[ProcessIdentity]
        self._last_terminate_reason: Optional[str] = None
        self._last_proof_method: Optional[str] = None
        self._last_reconcile_reason: Optional[str] = None
        self._foreign_mutex_holder: bool = False

    def set_job(self, job: Optional[JobObjectBase]) -> None:
        """Attach Supervisor Job Object (called when job is created at start)."""
        self._job = job

    @property
    def name(self) -> str:
        return self._descriptor.name

    @property
    def descriptor(self) -> ProcessDescriptor:
        return self._descriptor

    @property
    def backoff_config(self) -> BackoffConfig:
        return self._backoff_config

    @property
    def backoff_state(self) -> BackoffState:
        return self._backoff

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_exit_class(self) -> Optional[ExitClass]:
        return self._last_exit_class

    @property
    def intentional_stop(self) -> bool:
        return self._intentional_stop

    @property
    def state(self) -> ChildState:
        self._refresh_state()
        return self._state

    def mark_registered(self) -> None:
        if self._state == ChildState.CREATED:
            self._state = ChildState.REGISTERED

    @property
    def process_identity(self):
        return self._process_identity

    def is_alive(self) -> bool:
        self._refresh_state()
        if self._adopted_pid is not None:
            from app.runtime.process_inquiry import pid_alive

            return pid_alive(int(self._adopted_pid))
        proc = self._process
        if proc is None:
            return False
        try:
            return bool(proc.is_alive())
        except Exception:
            return False

    def exitcode(self) -> Optional[int]:
        self._refresh_state()
        proc = self._process
        if proc is not None:
            code = proc.exitcode
            if code is not None:
                self._last_exitcode = code
                return code
        return self._last_exitcode

    def observe(self, now: Optional[float] = None) -> None:
        """Refresh process state and track stable runtime (monitor tick)."""
        now = now if now is not None else monotonic_now()
        self._refresh_state()
        if self._state == ChildState.RUNNING and self.is_alive():
            self._backoff.note_running(now)
            if self._backoff.maybe_reset_after_stable(
                now, self._backoff_config.reset_after_stable_seconds
            ):
                logger.info(
                    "[runtime] failure counters reset after stable run name=%s",
                    self.name,
                )

    def needs_restart_evaluation(self) -> bool:
        """True when FAILED/STOPPED may need supervisor.request_restart()."""
        if self._intentional_stop:
            return False
        if self._state in (
            ChildState.RECONCILING,
            ChildState.ADOPTING,
        ):
            return False
        if self._state == ChildState.FAILED:
            return not self._restart_requested
        if self._state == ChildState.STOPPED and not self._restart_requested:
            # Always policy may want clean exits restarted.
            # LOCK_BUSY uses RECONCILING (M2B) — never permanent STOPPED deny.
            if self._last_exit_class == ExitClass.LOCK_BUSY:
                return False
            return True
        return False

    def needs_reconcile(self) -> bool:
        """True when M2B ownership reconciliation should run."""
        if self._intentional_stop or not self._descriptor.lock_aware:
            return False
        if self._reconcile_in_progress:
            return False
        return self._state == ChildState.RECONCILING

    def begin_reconcile_attempt(self) -> int:
        self._reconcile_in_progress = True
        self._reconcile_attempts += 1
        self._state = ChildState.RECONCILING
        self._error = "reconciling"
        return self._reconcile_attempts

    def end_reconcile_attempt(self, decision: str, *, success: bool) -> None:
        self._reconcile_in_progress = False
        self._last_reconcile_decision = decision
        if success:
            self._reconcile_attempts = 0
            self._error = None
            # Successful ownership recovery clears crash-loop counters so
            # intentional kill→LOCK_BUSY→reconcile cycles do not ABANDON.
            self._backoff.reset_all()
            self._backoff.note_running(monotonic_now())
            self._backoff.clear_abandon()

    def mark_reconciling(self, reason: str = "reconciling") -> None:
        """Return to RECONCILING so the next monitor tick retries M2B."""
        self._reconcile_in_progress = False
        self._restart_requested = False
        self._state = ChildState.RECONCILING
        self._error = reason

    @property
    def reconcile_attempts(self) -> int:
        return self._reconcile_attempts

    @property
    def last_reconcile_decision(self) -> Optional[str]:
        return self._last_reconcile_decision

    def mark_restart_requested(self) -> None:
        self._restart_requested = True

    def enter_backoff(self, delay_seconds: float, now: Optional[float] = None) -> None:
        now = now if now is not None else monotonic_now()
        self._backoff.schedule_backoff(now, delay_seconds)
        self._state = ChildState.BACKOFF
        self._error = f"backoff:{delay_seconds:.3f}s"
        logger.info(
            "[runtime] child backoff name=%s delay=%.3fs until=%.3f",
            self.name,
            delay_seconds,
            self._backoff.backoff_until or 0.0,
        )

    def backoff_due(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else monotonic_now()
        return self._state == ChildState.BACKOFF and self._backoff.backoff_due(now)

    def mark_abandoned(self, reason: str, now: Optional[float] = None) -> None:
        now = now if now is not None else monotonic_now()
        self._backoff.mark_abandoned(now, self._backoff_config.cooldown_seconds)
        self._state = ChildState.ABANDONED
        self._error = reason
        self._restart_requested = True
        logger.warning(
            "[runtime] child abandoned name=%s reason=%s",
            self.name,
            reason,
        )

    def record_failure_for_policy(self, now: Optional[float] = None) -> int:
        now = now if now is not None else monotonic_now()
        return self._backoff.record_failure(
            now, self._backoff_config.failure_window_seconds
        )

    def failures_in_window(self, now: Optional[float] = None) -> int:
        now = now if now is not None else monotonic_now()
        return self._backoff.failures_in_window(
            now, self._backoff_config.failure_window_seconds
        )

    def is_abandoned(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else monotonic_now()
        if self._state == ChildState.ABANDONED:
            if self._backoff.is_abandoned(now):
                return True
            # Cooldown cleared — leave ABANDONED until explicit start/request
            return True
        return self._backoff.is_abandoned(now)

    def start(self) -> bool:
        try:
            self._refresh_state()
            if not self._descriptor.enabled:
                self._state = ChildState.STOPPED
                self._error = "disabled"
                return False
            if self.is_alive():
                return True
            self._intentional_stop = False
            self._restart_requested = False
            self._backoff.clear_abandon()
            self._backoff.clear_backoff()
            return self._spawn(from_restart=False)
        except Exception as exc:
            self._state = ChildState.FAILED
            self._error = str(exc)
            self._process = None
            logger.warning(
                "[runtime] child start failed name=%s error=%s",
                self.name,
                exc,
            )
            return False

    def restart(self) -> bool:
        """
        Dispose old Process and start a new one.

        Called only by Supervisor after policy/backoff approval.
        """
        try:
            if not self._descriptor.enabled:
                self._state = ChildState.STOPPED
                self._error = "disabled"
                return False

            self._state = ChildState.RESTARTING
            self._intentional_stop = False
            self._restart_requested = False
            self._adopted_pid = None
            self._process_identity = None
            self._backoff.clear_backoff()
            self._dispose_process()
            ok = self._spawn(from_restart=True)
            if ok:
                self._reconcile_attempts = 0
                self._last_reconcile_decision = None
                logger.info(
                    "[runtime] child restarted name=%s generation=%s pid=%s",
                    self.name,
                    self._generation,
                    self._process.pid if self._process else None,
                )
            return ok
        except Exception as exc:
            self._state = ChildState.FAILED
            self._error = str(exc)
            self._process = None
            logger.warning(
                "[runtime] child restart failed name=%s error=%s",
                self.name,
                exc,
            )
            return False

    def record_reconcile_meta(
        self,
        *,
        reason: Optional[str] = None,
        terminate_reason: Optional[str] = None,
        proof_method: Optional[str] = None,
        foreign_mutex_holder: bool = False,
    ) -> None:
        self._last_reconcile_reason = reason
        self._last_terminate_reason = terminate_reason
        self._last_proof_method = proof_method
        self._foreign_mutex_holder = bool(foreign_mutex_holder)

    def adopt_os_pid(self, pid: int) -> bool:
        """
        Adopt a proven Runtime-owned OS process (Phase M2.5).

        Ownership must already be proven by the reconciler (live Job membership
        + create_time / handle identity). No heartbeat identity. No cmdline.
        """
        from app.runtime.process_identity import build_identity_at_spawn
        from app.runtime.process_inquiry import pid_alive

        if not self._descriptor.enabled:
            return False
        if pid is None or int(pid) <= 0 or not pid_alive(int(pid)):
            return False
        if self._job is None:
            self._error = "job_object_missing"
            return False
        # Must already be a live Job member — do not assign strangers.
        try:
            if not self._job.is_pid_in_job(int(pid)):
                self._error = "adopt_rejected_not_in_job"
                logger.warning(
                    "[runtime] adopt rejected name=%s pid=%s reason=not_in_live_job",
                    self.name,
                    pid,
                )
                return False
        except Exception as exc:
            self._error = f"adopt_job_check_failed:{exc}"
            return False

        self._state = ChildState.ADOPTING
        self._error = "adopting"
        try:
            self._dispose_process()
            self._process = None
            self._adopted_pid = int(pid)
            self._process_identity = build_identity_at_spawn(int(pid))
            self._job_assigned = True
            self._generation += 1
            self._last_exitcode = None
            self._last_exit_class = None
            self._startup_succeeded = True
            self._spawned_at = monotonic_now()
            self._restart_requested = False
            self._intentional_stop = False
            self._state = ChildState.RUNNING
            self._error = None
            self._reconcile_attempts = 0
            self._foreign_mutex_holder = False
            self._emit(
                ChildStarted(
                    child_name=self.name,
                    generation=self._generation,
                    pid=int(pid),
                    from_restart=True,
                )
            )
            logger.info(
                "[runtime] adopted os pid name=%s pid=%s generation=%s "
                "proof=%s",
                self.name,
                pid,
                self._generation,
                self._process_identity.proof_method
                if self._process_identity
                else None,
            )
            return True
        except Exception as exc:
            self._adopted_pid = None
            self._process_identity = None
            self._state = ChildState.RECONCILING
            self._error = f"adopt_failed:{exc}"
            logger.warning(
                "[runtime] adopt failed name=%s pid=%s error=%s",
                self.name,
                pid,
                exc,
            )
            return False

    def stop(self, timeout: Optional[float] = None) -> bool:
        join_timeout = (
            timeout
            if timeout is not None
            else self._descriptor.shutdown_timeout
        )
        try:
            self._intentional_stop = True
            self._restart_requested = True
            self._refresh_state()

            if self._adopted_pid is not None:
                from app.runtime.process_inquiry import terminate_pid

                self._state = ChildState.STOPPING
                terminate_pid(int(self._adopted_pid), force=True)
                self._adopted_pid = None
                self._state = ChildState.STOPPED
                self._last_exit_class = ExitClass.INTENTIONAL_STOP
                self._emit(
                    ChildStopped(
                        child_name=self.name,
                        exitcode=self._last_exitcode,
                        reason="intentional_stop",
                    )
                )
                return True

            proc = self._process
            if proc is None or not proc.is_alive():
                self._reap_if_needed()
                if self._state not in (
                    ChildState.FAILED,
                    ChildState.STOPPED,
                    ChildState.ABANDONED,
                    ChildState.RECONCILING,
                ):
                    self._state = ChildState.STOPPED
                self._last_exit_class = ExitClass.INTENTIONAL_STOP
                self._emit(
                    ChildStopped(
                        child_name=self.name,
                        exitcode=self._last_exitcode,
                        reason="intentional_stop",
                    )
                )
                return True

            self._state = ChildState.STOPPING
            try:
                proc.terminate()
            except Exception as exc:
                self._error = f"terminate failed: {exc}"
                logger.warning(
                    "[runtime] terminate failed name=%s error=%s",
                    self.name,
                    exc,
                )

            try:
                proc.join(timeout=join_timeout)
            except Exception as exc:
                self._error = f"join failed: {exc}"
                logger.warning(
                    "[runtime] join failed name=%s error=%s",
                    self.name,
                    exc,
                )

            self._reap_if_needed()
            alive = False
            try:
                alive = proc.is_alive()
            except Exception:
                alive = False

            if alive:
                self._state = ChildState.FAILED
                self._error = "still alive after terminate/join"
                return False

            self._state = ChildState.STOPPED
            self._last_exit_class = ExitClass.INTENTIONAL_STOP
            logger.info("[runtime] child stopped name=%s", self.name)
            self._emit(
                ChildStopped(
                    child_name=self.name,
                    exitcode=self._last_exitcode,
                    reason="intentional_stop",
                )
            )
            return True
        except Exception as exc:
            self._state = ChildState.FAILED
            self._error = str(exc)
            logger.warning(
                "[runtime] child stop failed name=%s error=%s",
                self.name,
                exc,
            )
            self._emit(
                ChildFailed(
                    child_name=self.name,
                    exitcode=self._last_exitcode,
                    error=str(exc),
                )
            )
            return False

    def status(self) -> ChildStatus:
        self._refresh_state()
        proc = self._process
        pid: Optional[int] = None
        alive = False
        if self._adopted_pid is not None:
            from app.runtime.process_inquiry import pid_alive

            pid = int(self._adopted_pid)
            alive = pid_alive(pid)
        elif proc is not None:
            try:
                pid = proc.pid
                alive = bool(proc.is_alive())
            except Exception:
                pid = None
                alive = False
        policy = self._descriptor.policy_kind
        return ChildStatus(
            name=self.name,
            state=self._state,
            enabled=self._descriptor.enabled,
            pid=pid if alive else (pid if self._adopted_pid is not None else (proc.pid if proc is not None else None)),
            exitcode=self.exitcode(),
            alive=alive,
            error=self._error,
            lock_aware=self._descriptor.lock_aware,
            restart_policy=policy.value,
            generation=self._generation,
            exit_class=(
                self._last_exit_class.value if self._last_exit_class else None
            ),
            consecutive_failures=self._backoff.consecutive_failures,
            last_backoff_seconds=self._backoff.last_delay,
            reconcile_attempts=self._reconcile_attempts,
            last_reconcile_decision=self._last_reconcile_decision,
            reconcile_reason=self._last_reconcile_reason,
            terminate_reason=self._last_terminate_reason,
            proof_method=self._last_proof_method,
            foreign_mutex_holder=self._foreign_mutex_holder,
            process_create_time=(
                self._process_identity.create_time
                if self._process_identity
                else None
            ),
        )

    def _spawn(self, *, from_restart: bool) -> bool:
        self._process = None
        self._error = None
        self._last_exitcode = None
        self._startup_succeeded = False
        self._job_assigned = False
        self._process_identity = None
        if not from_restart:
            self._state = ChildState.STARTING

        if self._job is None:
            self._state = ChildState.FAILED
            self._error = "job_object_missing"
            logger.error(
                "[runtime] child spawn refused name=%s reason=job_object_missing",
                self.name,
            )
            return False

        proc = multiprocessing.Process(
            target=self._descriptor.entrypoint,
            name=self._descriptor.name,
            args=tuple(self._descriptor.args),
            kwargs=dict(self._descriptor.kwargs),
            daemon=self._descriptor.daemon,
        )
        self._process = proc
        self._spawned_at = monotonic_now()
        proc.start()
        pid = proc.pid

        try:
            self._job.assign_pid(int(pid))
            self._job_assigned = True
        except JobObjectError as exc:
            import ctypes

            last_err = None
            try:
                last_err = ctypes.get_last_error()
            except Exception:
                last_err = None
            logger.error(
                "[runtime] job assign failed name=%s pid=%s error=%s "
                "GetLastError=%s — terminating unmanaged child "
                "(never continue unmanaged)",
                self.name,
                pid,
                exc,
                last_err,
            )
            try:
                proc.terminate()
                proc.join(timeout=self._descriptor.shutdown_timeout)
            except Exception as term_exc:
                logger.warning(
                    "[runtime] terminate after job-assign failure name=%s err=%s",
                    self.name,
                    term_exc,
                )
            self._process = None
            self._process_identity = None
            self._state = ChildState.FAILED
            self._error = f"job_assign_failed:{exc}"
            self._last_exitcode = proc.exitcode
            return False
        except Exception as exc:
            logger.error(
                "[runtime] unexpected job assign error name=%s pid=%s error=%s",
                self.name,
                pid,
                exc,
            )
            try:
                proc.terminate()
                proc.join(timeout=self._descriptor.shutdown_timeout)
            except Exception:
                pass
            self._process = None
            self._process_identity = None
            self._state = ChildState.FAILED
            self._error = f"job_assign_failed:{exc}"
            return False

        from app.runtime.process_identity import build_identity_at_spawn

        self._process_identity = build_identity_at_spawn(int(pid))
        logger.info(
            "[runtime] child identity name=%s pid=%s create_time=%s proof=%s",
            self.name,
            pid,
            self._process_identity.create_time,
            self._process_identity.proof_method,
        )
        self._generation += 1
        self._state = ChildState.RUNNING
        self._startup_succeeded = True
        self._error = None
        self._backoff.note_running(monotonic_now())
        logger.info(
            "[runtime] child started name=%s pid=%s generation=%s "
            "daemon=%s job_assigned=%s restart=%s",
            self.name,
            pid,
            self._generation,
            self._descriptor.daemon,
            self._job_assigned,
            from_restart,
        )
        self._emit(
            ChildStarted(
                child_name=self.name,
                generation=self._generation,
                pid=pid,
                from_restart=from_restart,
            )
        )
        return True

    def _emit(self, event: Any) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            bus.publish(event)
        except Exception as exc:
            logger.warning(
                "[runtime] event publish failed name=%s error=%s",
                self.name,
                exc,
            )

    def _dispose_process(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=self._descriptor.shutdown_timeout)
        except Exception as exc:
            logger.warning(
                "[runtime] dispose failed name=%s error=%s",
                self.name,
                exc,
            )
        try:
            if proc.exitcode is not None:
                self._last_exitcode = proc.exitcode
        except Exception:
            pass

    def _classify_exit(self, code: Optional[int]) -> ExitClass:
        """
        Phase M1 exit classification.

        - intentional_stop → INTENTIONAL_STOP
        - exit 0 → CLEAN_IDLE
        - explicit lock-busy exit code only → LOCK_BUSY
        - unknown exitcode 1 (incl. taskkill) → CRASH (never LOCK_BUSY)
        - other non-zero → CRASH
        - None → UNKNOWN
        """
        if self._intentional_stop:
            return ExitClass.INTENTIONAL_STOP
        if code == 0:
            return ExitClass.CLEAN_IDLE
        if (
            self._descriptor.lock_aware
            and code == EXPLICIT_LOCK_BUSY_EXITCODE
        ):
            # Child explicitly reported lock/singleton failure (not force-kill).
            lifetime = None
            if self._spawned_at is not None:
                lifetime = monotonic_now() - self._spawned_at
            logger.info(
                "[runtime] classify LOCK_BUSY name=%s exitcode=%s "
                "startup_succeeded=%s lifetime_s=%s",
                self.name,
                code,
                self._startup_succeeded,
                lifetime,
            )
            return ExitClass.LOCK_BUSY
        if code is None:
            return ExitClass.UNKNOWN
        # Generic 1 and all other non-zero → CRASH (force-kill / real crash).
        logger.info(
            "[runtime] classify CRASH name=%s exitcode=%s "
            "startup_succeeded=%s intentional=%s",
            self.name,
            code,
            self._startup_succeeded,
            self._intentional_stop,
        )
        return ExitClass.CRASH

    def _refresh_state(self) -> None:
        # Adopted OS PID tracking (no multiprocessing.Process)
        if self._adopted_pid is not None:
            if self._state in (
                ChildState.STOPPING,
                ChildState.RESTARTING,
                ChildState.BACKOFF,
                ChildState.ABANDONED,
                ChildState.RECONCILING,
                ChildState.ADOPTING,
            ):
                return
            from app.runtime.process_inquiry import pid_alive

            if pid_alive(int(self._adopted_pid)):
                if self._state == ChildState.STARTING:
                    self._state = ChildState.RUNNING
                return
            # Adopted process died
            code = 1
            self._last_exitcode = code
            self._adopted_pid = None
            if self._state in (ChildState.RUNNING, ChildState.STARTING):
                exit_class = self._classify_exit(code)
                self._last_exit_class = exit_class
                self._restart_requested = False
                self._state = ChildState.FAILED
                self._emit(
                    ChildFailed(
                        child_name=self.name,
                        exitcode=code,
                        exit_class=exit_class.value,
                        error="adopted_process_exited",
                    )
                )
            return

        proc = self._process
        if proc is None:
            return
        if self._state in (
            ChildState.STOPPING,
            ChildState.RESTARTING,
            ChildState.BACKOFF,
            ChildState.ABANDONED,
            ChildState.RECONCILING,
            ChildState.ADOPTING,
        ):
            return
        try:
            alive = bool(proc.is_alive())
        except Exception:
            return
        if alive:
            if self._state == ChildState.STARTING:
                self._state = ChildState.RUNNING
            return
        code = proc.exitcode
        if code is not None:
            self._last_exitcode = code
        if self._state in (ChildState.RUNNING, ChildState.STARTING):
            exit_class = self._classify_exit(code)
            self._last_exit_class = exit_class
            self._restart_requested = False
            if exit_class == ExitClass.LOCK_BUSY:
                # M2B: investigate ownership — never park permanently in STOPPED
                self._state = ChildState.RECONCILING
                self._error = "lock_busy"
                self._emit(
                    ChildStopped(
                        child_name=self.name,
                        exitcode=code,
                        reason=exit_class.value,
                    )
                )
            elif exit_class in (
                ExitClass.INTENTIONAL_STOP,
                ExitClass.CLEAN_IDLE,
            ):
                self._state = ChildState.STOPPED
                self._emit(
                    ChildStopped(
                        child_name=self.name,
                        exitcode=code,
                        reason=exit_class.value,
                    )
                )
            else:
                self._state = ChildState.FAILED
                self._emit(
                    ChildFailed(
                        child_name=self.name,
                        exitcode=code,
                        exit_class=exit_class.value,
                        error=self._error,
                    )
                )
            logger.info(
                "[runtime] child exited name=%s exitcode=%s class=%s state=%s",
                self.name,
                code,
                exit_class.value,
                self._state.value,
            )

    def _reap_if_needed(self) -> None:
        proc = self._process
        if proc is None:
            return
        try:
            if proc.exitcode is not None:
                self._last_exitcode = proc.exitcode
        except Exception:
            pass
