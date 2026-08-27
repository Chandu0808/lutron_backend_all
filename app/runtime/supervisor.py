"""
Runtime Supervisor (Phases 1–4).

Owns ChildManagers, HealthMonitor (detection only), restart orchestration,
and an internal RuntimeEventBus (publish only — never consumes).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.runtime.child_manager import ChildManager, ChildStatus
from app.runtime.energy_logger_mutex import is_mutex_object_present, mutex_name
from app.runtime.events import (
    Abandoned,
    BackoffEntered,
    DiagnosticsSubscriber,
    ForeignMutexDetected,
    LoggingSubscriber,
    ReconcileCompleted,
    ReconcileStarted,
    RestartFailed,
    RestartRequested,
    RestartScheduled,
    RestartStarted,
    RestartSucceeded,
    RuntimeEventBus,
    SupervisorStarted,
    SupervisorStopped,
)
from app.runtime.health_monitor import DEFAULT_INTERVAL_SECONDS, HealthMonitor
from app.runtime.install_id import resolve_install_id
from app.runtime.job_object import (
    JobObjectBase,
    JobObjectError,
    ParentJobDiagnostics,
    create_runtime_job_object,
    detect_parent_job_state,
)
from app.runtime.lifecycle import ChildState
from app.runtime.ownership_reconcile import (
    ReconcileReport,
    reconcile_max_attempts,
    run_ownership_reconcile,
)
from app.runtime.process_descriptor import ProcessDescriptor
from app.runtime.restart_backoff import BackoffConfig, monotonic_now
from app.runtime.restart_policy import (
    ExitClass,
    RestartAction,
    RestartContext,
    evaluate_restart_policy,
)

logger = logging.getLogger("lutron_runtime.supervisor")


def _poll_interval_from_env() -> float:
    raw = (os.getenv("RUNTIME_POLL_INTERVAL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_INTERVAL_SECONDS


@dataclass(frozen=True)
class SupervisorStatus:
    running: bool
    monitor_running: bool = False
    children: List[ChildStatus] = field(default_factory=list)
    job_active: bool = False
    job_name: Optional[str] = None
    reconcile_state: Optional[str] = None
    mutex_present: Optional[bool] = None
    mutex_name: Optional[str] = None
    live_job_members: List[int] = field(default_factory=list)
    foreign_mutex_holder: bool = False
    reconcile_reason: Optional[str] = None
    terminate_reason: Optional[str] = None
    proof_method: Optional[str] = None
    parent_job: Optional[Dict[str, Any]] = None
    install_id: Optional[str] = None
    install_id_source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "monitor_running": self.monitor_running,
            "job_active": self.job_active,
            "job_name": self.job_name,
            "reconcile_state": self.reconcile_state,
            "mutex_present": self.mutex_present,
            "mutex_name": self.mutex_name,
            "live_job_members": list(self.live_job_members),
            "foreign_mutex_holder": self.foreign_mutex_holder,
            "reconcile_reason": self.reconcile_reason,
            "terminate_reason": self.terminate_reason,
            "proof_method": self.proof_method,
            "parent_job": self.parent_job,
            "install_id": self.install_id,
            "install_id_source": self.install_id_source,
            "children": [c.to_dict() for c in self.children],
        }


class RuntimeSupervisor:
    """
    Process-wide owner of runtime children.

    HealthMonitor detects exits; ``request_restart`` applies policy + backoff.
    Publishes internal Runtime events; does not subscribe to them.

    Phase M1: owns a Windows Job Object (KILL_ON_JOB_CLOSE). Every child is
    assigned immediately after spawn. Closing the job on shutdown (or API
    process death releasing the handle) terminates remaining members.
    """

    def __init__(
        self,
        *,
        health_interval_seconds: Optional[float] = None,
        backoff_config: Optional[BackoffConfig] = None,
        event_bus: Optional[RuntimeEventBus] = None,
        enable_default_subscribers: bool = True,
        job: Optional[JobObjectBase] = None,
    ) -> None:
        self._children: Dict[str, ChildManager] = {}
        self._started = False
        self._shutdown = False
        self._backoff_config = backoff_config or BackoffConfig.from_env()
        self._event_bus = event_bus or RuntimeEventBus()
        self._diagnostics = DiagnosticsSubscriber(bus=self._event_bus)
        self._logging_subscriber: Optional[LoggingSubscriber] = None
        if enable_default_subscribers:
            self._logging_subscriber = LoggingSubscriber(bus=self._event_bus)
        interval = (
            health_interval_seconds
            if health_interval_seconds is not None
            else _poll_interval_from_env()
        )
        self._monitor = HealthMonitor(
            on_tick=self.handle_monitor_tick,
            interval_seconds=interval,
        )
        # Optional pre-injected job (tests). Otherwise created in start().
        self._job: Optional[JobObjectBase] = job
        self._job_owned = job is None  # close only if we created it
        self._parent_job_diag: Optional[ParentJobDiagnostics] = None
        self._last_reconcile: Optional[ReconcileReport] = None
        self._install_resolution = resolve_install_id()

    @property
    def running(self) -> bool:
        return self._started and not self._shutdown

    @property
    def monitor_running(self) -> bool:
        return self._monitor.running

    @property
    def backoff_config(self) -> BackoffConfig:
        return self._backoff_config

    @property
    def event_bus(self) -> RuntimeEventBus:
        return self._event_bus

    @property
    def diagnostics(self) -> DiagnosticsSubscriber:
        return self._diagnostics

    @property
    def job(self) -> Optional[JobObjectBase]:
        return self._job

    def register(self, descriptor: ProcessDescriptor) -> ChildManager:
        if self._shutdown:
            raise RuntimeError("Cannot register after supervisor shutdown")
        name = descriptor.name
        if name in self._children:
            raise ValueError(f"Child already registered: {name}")
        manager = ChildManager(
            descriptor,
            backoff_config=self._backoff_config,
            event_bus=self._event_bus,
            job=self._job,
        )
        manager.mark_registered()
        self._children[name] = manager
        logger.info(
            "[runtime] registered child name=%s policy=%s daemon=%s",
            name,
            descriptor.policy_kind.value,
            descriptor.daemon,
        )
        return manager

    def get_child(self, name: str) -> Optional[ChildManager]:
        return self._children.get(name)

    def start(self) -> None:
        if self._shutdown:
            raise RuntimeError("Supervisor already shut down")
        self._parent_job_diag = detect_parent_job_state()
        logger.info(
            "[runtime] parent_job api_in_any_job=%s nested_supported=%s note=%s",
            self._parent_job_diag.api_in_any_job,
            self._parent_job_diag.nested_jobs_supported,
            self._parent_job_diag.note,
        )
        if self._job is None:
            try:
                self._job = create_runtime_job_object()
                self._job_owned = True
            except JobObjectError as exc:
                logger.error("[runtime] job object create failed: %s", exc)
                raise
        for manager in self._children.values():
            manager.set_job(self._job)
        self._started = True
        logger.info(
            "[runtime] supervisor started job_active=%s job_name=%s "
            "install_id=%s source=%s",
            bool(self._job and self._job.active),
            self._job.name if self._job else None,
            self._install_resolution.install_id,
            self._install_resolution.source,
        )
        self._emit(
            SupervisorStarted(child_count=len(self._children))
        )

    def start_all(self) -> Dict[str, bool]:
        if self._shutdown:
            return {name: False for name in self._children}
        if not self._started:
            self.start()

        results: Dict[str, bool] = {}
        for name, manager in self._children.items():
            try:
                if not manager.descriptor.enabled:
                    results[name] = False
                    continue
                results[name] = manager.start()
            except Exception as exc:
                results[name] = False
                logger.warning(
                    "[runtime] start_all failed for %s: %s", name, exc
                )
        return results

    def start_monitor(self) -> None:
        if self._shutdown:
            return
        if not self._started:
            self.start()
        self._monitor.start()

    def stop_monitor(self, timeout: float = 5.0) -> None:
        self._monitor.stop(timeout=timeout)

    def stop_all(self) -> Dict[str, bool]:
        self.stop_monitor()
        results: Dict[str, bool] = {}
        for name in reversed(list(self._children.keys())):
            manager = self._children[name]
            try:
                results[name] = manager.stop()
            except Exception as exc:
                results[name] = False
                logger.warning(
                    "[runtime] stop_all failed for %s: %s", name, exc
                )
        return results

    def shutdown(self) -> None:
        """
        Graceful shutdown order (Phase M1):

        1. stop health monitor (no further restarts)
        2. intentional stop each child (terminate + join with timeout)
        3. close Job Object (KILL_ON_JOB_CLOSE reaps any survivors)
        """
        self._shutdown = True
        try:
            self.stop_monitor()
            for name in reversed(list(self._children.keys())):
                manager = self._children[name]
                try:
                    ok = manager.stop()
                    if not ok:
                        logger.warning(
                            "[runtime] shutdown stop incomplete name=%s — "
                            "job close will force-kill if still assigned",
                            name,
                        )
                except Exception as exc:
                    logger.warning(
                        "[runtime] shutdown stop failed for %s: %s", name, exc
                    )
        finally:
            self._close_job()
            self._started = False
            self._emit(
                SupervisorStopped(child_count=len(self._children))
            )
            logger.info("[runtime] supervisor shutdown complete")

    def _close_job(self) -> None:
        job = self._job
        if job is None:
            return
        if not self._job_owned:
            # Test-injected job: still close so KILL_ON_JOB_CLOSE runs when
            # the injected object is a real WindowsJobObject owned by caller;
            # NullJobObject close is harmless.
            try:
                job.close()
            except Exception as exc:
                logger.warning("[runtime] job close (injected) failed: %s", exc)
            return
        try:
            job.close()
        except Exception as exc:
            logger.warning("[runtime] job close failed: %s", exc)
        finally:
            self._job = None
            for manager in self._children.values():
                manager.set_job(None)

    def handle_monitor_tick(self) -> None:
        """
        Detection + restart orchestration entry (called by HealthMonitor).

        HealthMonitor itself never restarts children and never publishes
        policy decisions.
        """
        now = monotonic_now()
        for manager in list(self._children.values()):
            try:
                manager.observe(now)
            except Exception as exc:
                logger.warning(
                    "[runtime] observe failed name=%s error=%s",
                    manager.name,
                    exc,
                )

        if not self.running:
            return

        for manager in list(self._children.values()):
            try:
                if manager.state == ChildState.BACKOFF and manager.backoff_due(now):
                    self._execute_restart(manager)
                    continue
                if manager.needs_reconcile():
                    self._reconcile_ownership(manager)
                    continue
                if manager.needs_restart_evaluation():
                    self.request_restart(manager.name)
            except Exception as exc:
                logger.warning(
                    "[runtime] monitor tick child failed name=%s error=%s",
                    manager.name,
                    exc,
                )

    def _protected_pids(self) -> set:
        import os

        pids = {os.getpid()}
        for mgr in self._children.values():
            st = mgr.status()
            if st.alive and st.pid:
                pids.add(int(st.pid))
        return pids

    def _reconcile_ownership(self, manager: ChildManager) -> None:
        """
        Phase M2.5: safe ownership reconcile after exit 78.

        Adopt/kill only live Job-proven members with identity verification.
        Foreign mutex → wait + alert; never heuristic terminate.
        """
        if self._shutdown or not self._started:
            return
        if not manager.descriptor.lock_aware:
            return

        attempt = manager.begin_reconcile_attempt()
        max_attempts = reconcile_max_attempts()
        self._emit(
            ReconcileStarted(
                child_name=manager.name,
                attempt=attempt,
                reason="lock_busy",
            )
        )
        logger.info(
            "[runtime] reconcile start name=%s attempt=%s/%s",
            manager.name,
            attempt,
            max_attempts,
        )

        if attempt > max_attempts:
            manager.end_reconcile_attempt("abandon_max_attempts", success=False)
            manager.record_reconcile_meta(
                reason="exceeded_max_attempts",
                foreign_mutex_holder=False,
            )
            manager.mark_abandoned(
                f"reconcile_max_attempts:{max_attempts}", now=monotonic_now()
            )
            self._emit(
                Abandoned(
                    child_name=manager.name,
                    reason=f"reconcile_max_attempts:{max_attempts}",
                )
            )
            self._emit(
                ReconcileCompleted(
                    child_name=manager.name,
                    attempt=attempt,
                    decision="abandon_max_attempts",
                    success=False,
                    error="max_attempts",
                    reconcile_reason="exceeded_max_attempts",
                )
            )
            return

        known: Dict[int, Any] = {}
        for mgr in self._children.values():
            ident = mgr.process_identity
            if ident is not None and ident.pid:
                known[int(ident.pid)] = ident
            st = mgr.status()
            if st.alive and st.pid and st.process_create_time is not None:
                from app.runtime.process_identity import ProcessIdentity

                known.setdefault(
                    int(st.pid),
                    ProcessIdentity(
                        pid=int(st.pid),
                        create_time=st.process_create_time,
                        proof_method="create_time",
                    ),
                )

        def _spawn() -> bool:
            return bool(manager.restart())

        def _adopt(pid: int) -> bool:
            return bool(manager.adopt_os_pid(int(pid)))

        def _alert(report: ReconcileReport) -> None:
            self._emit(
                ForeignMutexDetected(
                    child_name=manager.name,
                    attempt=attempt,
                    mutex_name=report.mutex_name,
                    live_job_members=tuple(report.live_job_members),
                    reconcile_reason=report.reconcile_reason
                    or "foreign_mutex_holder",
                )
            )

        report = run_ownership_reconcile(
            child_name=manager.name,
            attempt=attempt,
            protected_pids=self._protected_pids(),
            job=self._job,
            known_identities=known,
            adopt_callback=_adopt,
            spawn_callback=_spawn,
            alert_callback=_alert,
        )
        self._last_reconcile = report
        manager.record_reconcile_meta(
            reason=report.reconcile_reason,
            terminate_reason=report.terminate_reason,
            proof_method=report.proof_method,
            foreign_mutex_holder=report.foreign_mutex_holder,
        )
        manager.end_reconcile_attempt(report.decision, success=report.success)
        self._emit(
            ReconcileCompleted(
                child_name=manager.name,
                attempt=attempt,
                decision=report.decision,
                success=report.success,
                error=report.error,
                foreign_mutex_holder=report.foreign_mutex_holder,
                reconcile_reason=report.reconcile_reason,
                terminate_reason=report.terminate_reason,
                proof_method=report.proof_method,
                live_job_members=tuple(report.live_job_members),
            )
        )
        logger.info(
            "[runtime] reconcile done name=%s decision=%s success=%s "
            "foreign=%s proof=%s timeline=%s",
            manager.name,
            report.decision,
            report.success,
            report.foreign_mutex_holder,
            report.proof_method,
            report.timeline,
        )

        if report.success:
            return

        if report.decision in (
            "abandon_max_attempts",
            "abandon_foreign_mutex",
        ):
            manager.mark_abandoned(report.error or "reconcile_failed")
            self._emit(
                Abandoned(
                    child_name=manager.name,
                    reason=report.error or "reconcile_failed",
                )
            )
            return

        # Failed spawn or foreign mutex still held: stay in RECONCILING
        if manager.state != ChildState.RUNNING:
            if is_mutex_object_present() or manager.last_exit_class == ExitClass.LOCK_BUSY:
                if manager.state not in (
                    ChildState.RECONCILING,
                    ChildState.ABANDONED,
                    ChildState.RUNNING,
                ):
                    manager.mark_reconciling(report.error or "reconciling")

    def request_restart(self, name: str) -> bool:
        """
        Evaluate policy + backoff, then schedule or perform restart.

        Returns True if a restart was executed or scheduled (BACKOFF).
        """
        if self._shutdown or not self._started:
            return False
        manager = self._children.get(name)
        if manager is None:
            return False

        now = monotonic_now()
        manager.observe(now)
        manager.mark_restart_requested()

        exit_class = manager.last_exit_class or ExitClass.UNKNOWN
        self._emit(
            RestartRequested(
                child_name=name,
                exit_class=exit_class.value,
                reason="unexpected_exit",
            )
        )

        if manager.intentional_stop:
            return False
        if not manager.descriptor.enabled:
            return False

        should_count = exit_class in (
            ExitClass.CRASH,
            ExitClass.UNKNOWN,
        ) or (
            exit_class == ExitClass.CLEAN_IDLE
            and manager.descriptor.policy_kind.value == "always"
        )

        failures = manager.failures_in_window(now)
        if should_count and manager.state in (
            ChildState.FAILED,
            ChildState.STOPPED,
        ):
            failures = manager.record_failure_for_policy(now)

        ctx = RestartContext(
            exit_class=exit_class,
            enabled=manager.descriptor.enabled,
            intentional_stop=manager.intentional_stop,
            supervisor_shutting_down=self._shutdown,
            failures_in_window=failures,
            max_restarts=self._backoff_config.max_restarts,
            already_abandoned=manager.state == ChildState.ABANDONED,
        )
        decision = evaluate_restart_policy(
            manager.descriptor.policy_kind, ctx
        )

        if decision.action == RestartAction.DENY:
            logger.info(
                "[runtime] restart denied name=%s reason=%s",
                name,
                decision.reason,
            )
            return False

        if decision.action == RestartAction.ABANDON:
            manager.mark_abandoned(decision.reason, now=now)
            self._emit(
                Abandoned(child_name=name, reason=decision.reason)
            )
            return False

        attempt_index = max(0, manager.backoff_state.consecutive_failures - 1)
        delay = self._backoff_config.next_delay(attempt_index)
        self._emit(
            RestartScheduled(
                child_name=name,
                delay_seconds=delay,
                attempt_index=attempt_index,
            )
        )
        if delay <= 0:
            return self._execute_restart(manager)

        manager.enter_backoff(delay, now=now)
        self._emit(
            BackoffEntered(child_name=name, delay_seconds=delay)
        )
        return True

    def _execute_restart(self, manager: ChildManager) -> bool:
        logger.info("[runtime] executing restart name=%s", manager.name)
        gen_before = manager.generation
        self._emit(
            RestartStarted(
                child_name=manager.name,
                generation_before=gen_before,
            )
        )
        ok = manager.restart()
        if ok:
            self._emit(
                RestartSucceeded(
                    child_name=manager.name,
                    generation=manager.generation,
                    pid=manager.status().pid,
                )
            )
        else:
            self._emit(
                RestartFailed(
                    child_name=manager.name,
                    error=manager.status().error,
                )
            )
        return ok

    def status(self) -> SupervisorStatus:
        children = [manager.status() for manager in self._children.values()]
        job = self._job
        live_members: List[int] = []
        if job is not None:
            try:
                live_members = list(job.list_live_member_pids() or [])
            except Exception:
                live_members = []

        last = self._last_reconcile
        lock_aware_states = [
            c.state.value
            for c in children
            if c.lock_aware
        ]
        reconcile_state = None
        foreign = False
        reason = None
        term_reason = None
        proof = None
        for c in children:
            if c.lock_aware and c.state.value in ("RECONCILING", "ADOPTING"):
                reconcile_state = c.state.value
            if c.foreign_mutex_holder:
                foreign = True
            if c.reconcile_reason:
                reason = c.reconcile_reason
            if c.terminate_reason:
                term_reason = c.terminate_reason
            if c.proof_method:
                proof = c.proof_method
        if last is not None:
            foreign = foreign or last.foreign_mutex_holder
            reason = reason or last.reconcile_reason
            term_reason = term_reason or last.terminate_reason
            proof = proof or last.proof_method
            if last.live_job_members:
                live_members = list(last.live_job_members)

        mutex_present = None
        try:
            mutex_present = bool(is_mutex_object_present())
        except Exception:
            mutex_present = None

        parent = (
            self._parent_job_diag.to_dict()
            if self._parent_job_diag is not None
            else None
        )

        return SupervisorStatus(
            running=self.running,
            monitor_running=self.monitor_running,
            children=children,
            job_active=bool(job and job.active),
            job_name=job.name if job else None,
            reconcile_state=reconcile_state
            or (lock_aware_states[0] if lock_aware_states else None),
            mutex_present=mutex_present,
            mutex_name=mutex_name(),
            live_job_members=live_members,
            foreign_mutex_holder=foreign,
            reconcile_reason=reason,
            terminate_reason=term_reason,
            proof_method=proof,
            parent_job=parent,
            install_id=self._install_resolution.install_id,
            install_id_source=self._install_resolution.source,
        )

    def tick_monitor_once(self) -> None:
        self._monitor.tick_once()

    def _emit(self, event: Any) -> None:
        try:
            self._event_bus.publish(event)
        except Exception as exc:
            logger.warning("[runtime] supervisor event publish failed: %s", exc)
