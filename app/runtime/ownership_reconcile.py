"""
Energy logger ownership reconciliation (Phase M2.5).

Triggered when a lock_aware child exits EXPLICIT_LOCK_BUSY_EXITCODE (78).

Authority for adopt/kill: LIVE Job membership + PID-reuse defense.
Never terminate based on diagnostic PID, TEMP file, cmdline, or historical
assign logs. Foreign mutex holders → wait + alert → ABANDON.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from app.runtime.energy_logger_mutex import (
    is_mutex_object_present,
    mutex_name,
    wait_until_mutex_absent,
)
from app.runtime.job_object import JobObjectBase
from app.runtime.process_identity import (
    ProcessIdentity,
    get_process_create_time,
    identity_still_matches,
    verify_create_time,
)
from app.runtime.process_inquiry import (
    pid_alive,
    read_diagnostic_pid_hint,
    terminate_pid,
)

logger = logging.getLogger("lutron_runtime.ownership_reconcile")

ENV_MAX_ATTEMPTS = "RUNTIME_RECONCILE_MAX_ATTEMPTS"
ENV_WAIT_SECONDS = "RUNTIME_RECONCILE_WAIT_SECONDS"
ENV_TERMINATE_WAIT = "RUNTIME_RECONCILE_TERMINATE_WAIT_SECONDS"
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WAIT_SECONDS = 2.0
DEFAULT_TERMINATE_WAIT = 3.0


def reconcile_max_attempts() -> int:
    raw = (os.getenv(ENV_MAX_ATTEMPTS) or "").strip()
    if not raw:
        return DEFAULT_MAX_ATTEMPTS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS
    return value if value > 0 else DEFAULT_MAX_ATTEMPTS


def reconcile_wait_seconds() -> float:
    raw = (os.getenv(ENV_WAIT_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_WAIT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_WAIT_SECONDS
    return value if value > 0 else DEFAULT_WAIT_SECONDS


def reconcile_terminate_wait_seconds() -> float:
    raw = (os.getenv(ENV_TERMINATE_WAIT) or "").strip()
    if not raw:
        return DEFAULT_TERMINATE_WAIT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TERMINATE_WAIT
    return value if value > 0 else DEFAULT_TERMINATE_WAIT


@dataclass
class HolderDiagnostic:
    pid: int
    name: str
    cmdline: str
    create_time: Optional[float]
    ownership_status: str  # diagnostic_only | proven_job_member | protected | dead_hint
    in_job: bool = False
    proven_runtime_owner: bool = False
    proof_method: Optional[str] = None
    action: str = "none"  # none | terminate | adopt | skip | wait_foreign | ignore

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TerminateDecision:
    pid: int
    why: str
    proof_method: str
    job_membership: bool
    create_time_verified: bool
    create_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconcileReport:
    child_name: str
    attempt: int
    max_attempts: int
    mutex_name: str
    mutex_held_before: bool
    decision: str
    success: bool
    diagnostics: List[HolderDiagnostic] = field(default_factory=list)
    diagnostic_pid_hint: Optional[int] = None
    mutex_held_after: Optional[bool] = None
    adopted_pid: Optional[int] = None
    spawned: bool = False
    error: Optional[str] = None
    timeline: List[str] = field(default_factory=list)
    live_job_members: List[int] = field(default_factory=list)
    foreign_mutex_holder: bool = False
    reconcile_reason: Optional[str] = None
    terminate_reason: Optional[str] = None
    proof_method: Optional[str] = None
    terminate_decisions: List[TerminateDecision] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _live_job_pids(job: Optional[JobObjectBase]) -> List[int]:
    if job is None:
        return []
    try:
        members = [int(p) for p in (job.list_live_member_pids() or []) if p]
    except Exception as exc:
        logger.warning("[reconcile] live job list failed: %s", exc)
        return []
    return [p for p in members if pid_alive(p)]


def _prove_job_member(
    pid: int,
    *,
    job: Optional[JobObjectBase],
    known_identities: Dict[int, ProcessIdentity],
) -> HolderDiagnostic:
    """
    Prove Runtime ownership via live Job membership + create_time when known.

    Never uses cmdline / TEMP / historical assign lists.
    """
    in_job = False
    if job is not None:
        try:
            in_job = bool(job.is_pid_in_job(int(pid)))
        except Exception:
            in_job = int(pid) in set(_live_job_pids(job))

    live_ct = get_process_create_time(int(pid))
    identity = known_identities.get(int(pid))
    create_time_ok = False
    proof: Optional[str] = None
    proven = False

    if in_job and identity is not None and identity.create_time is not None:
        create_time_ok = verify_create_time(int(pid), identity.create_time)
        if create_time_ok:
            proven = True
            proof = "live_job+create_time"
        else:
            # PID reuse: same numeric pid, different process object
            proven = False
            proof = "create_time_mismatch_reject"
    elif in_job:
        # Live Job membership alone is documented Supervisor ownership.
        # Record live create_time for subsequent identity checks.
        proven = True
        proof = "live_job"
        create_time_ok = live_ct is not None
    else:
        proven = False
        proof = None

    status = "proven_job_member" if proven else "not_job_member"
    if proof == "create_time_mismatch_reject":
        status = "pid_reuse_rejected"

    return HolderDiagnostic(
        pid=int(pid),
        name="job_member",
        cmdline="",  # intentionally unused for authority
        create_time=live_ct if live_ct is not None else (
            identity.create_time if identity else None
        ),
        ownership_status=status,
        in_job=in_job,
        proven_runtime_owner=proven,
        proof_method=proof,
    )


def run_ownership_reconcile(
    *,
    child_name: str,
    attempt: int,
    protected_pids: Set[int],
    job: Optional[JobObjectBase] = None,
    known_identities: Optional[Dict[int, ProcessIdentity]] = None,
    # Deprecated M2B kwarg — accepted but IGNORED for adopt/kill (diagnostic only).
    job_assigned_pids: Optional[List[int]] = None,
    adopt_callback: Optional[Callable[[int], bool]] = None,
    spawn_callback: Optional[Callable[[], bool]] = None,
    alert_callback: Optional[Callable[[ReconcileReport], None]] = None,
) -> ReconcileReport:
    """
    Decision tree (M2.5):

    1. Mutex absent → spawn (holder_gone)
    2. Live Job members with proof → adopt; on adopt failure → terminate
       only that proven member (with recorded proof)
    3. Mutex present + no proven Job member → foreign holder:
       wait, alert, remain RECONCILING / ABANDON — never heuristic kill
    """
    del job_assigned_pids  # must not influence decisions

    max_attempts = reconcile_max_attempts()
    identities = dict(known_identities or {})
    report = ReconcileReport(
        child_name=child_name,
        attempt=attempt,
        max_attempts=max_attempts,
        mutex_name=mutex_name(),
        mutex_held_before=False,
        decision="pending",
        success=False,
        diagnostic_pid_hint=read_diagnostic_pid_hint(),
    )
    report.timeline.append(f"reconcile_begin attempt={attempt}/{max_attempts}")

    if attempt > max_attempts:
        report.decision = "abandon_max_attempts"
        report.error = f"exceeded_max_attempts:{max_attempts}"
        report.reconcile_reason = "exceeded_max_attempts"
        report.timeline.append(report.error)
        logger.warning(
            "[reconcile] abandon name=%s attempts=%s max=%s",
            child_name,
            attempt,
            max_attempts,
        )
        return report

    from app.runtime.energy_logger_mutex import (
        MUTEX_ABSENT,
        MUTEX_INACCESSIBLE,
        probe_mutex_object,
    )

    probe = probe_mutex_object()
    held = probe != MUTEX_ABSENT
    report.mutex_held_before = held
    report.timeline.append(
        f"mutex_probe state={probe} held={held} name={report.mutex_name}"
    )

    if probe == MUTEX_INACCESSIBLE:
        # OpenMutex ACCESS_DENIED: object exists but this process cannot open it.
        # Must NOT treat as absent (that caused CreateMutex error=5 spawn storms).
        report.foreign_mutex_holder = True
        report.reconcile_reason = "mutex_inaccessible"
        report.decision = (
            "abandon_foreign_mutex"
            if attempt >= max_attempts
            else "retry_foreign_mutex"
        )
        report.error = "mutex_inaccessible_access_denied"
        report.success = False
        report.timeline.append(
            "mutex_inaccessible → foreign wait/abandon (no spawn)"
        )
        logger.warning(
            "[reconcile] mutex inaccessible name=%s attempt=%s — not spawning",
            child_name,
            attempt,
        )
        if alert_callback is not None:
            try:
                alert_callback(report)
            except Exception as exc:
                report.timeline.append(f"alert_callback_failed:{exc}")
        if attempt >= max_attempts:
            report.decision = "abandon_foreign_mutex"
            report.reconcile_reason = "mutex_inaccessible_abandon"
            report.timeline.append("abandon: mutex inaccessible after max attempts")
            return report
        time.sleep(min(0.05, reconcile_wait_seconds()))
        return report

    if not held:
        report.decision = "holder_gone_spawn"
        report.reconcile_reason = "mutex_absent"
        report.timeline.append("mutex_absent → spawn")
        ok = bool(spawn_callback() if spawn_callback else False)
        report.spawned = ok
        report.success = ok
        report.mutex_held_after = is_mutex_object_present()
        if not ok:
            report.error = "spawn_failed_after_holder_gone"
        logger.info(
            "[reconcile] holder_gone name=%s spawn_ok=%s",
            child_name,
            ok,
        )
        return report

    live_members = _live_job_pids(job)
    report.live_job_members = list(live_members)
    report.timeline.append(f"live_job_members={live_members}")

    # Diagnostic-only rows (never actionable)
    hint = report.diagnostic_pid_hint
    if hint is not None:
        hint_alive = pid_alive(int(hint))
        report.diagnostics.append(
            HolderDiagnostic(
                pid=int(hint),
                name="diagnostic_pid_file",
                cmdline="",
                create_time=None,
                ownership_status="diagnostic_only",
                in_job=bool(job and job.is_pid_in_job(int(hint))) if job else False,
                proven_runtime_owner=False,
                proof_method=None,
                action="ignore_diagnostic_pid",
            )
        )
        report.timeline.append(
            f"diagnostic_pid_hint pid={hint} alive={hint_alive} action=ignored"
        )

    # Evaluate live Job members only
    proven: List[HolderDiagnostic] = []
    for pid in live_members:
        if pid in protected_pids:
            diag = HolderDiagnostic(
                pid=pid,
                name="protected",
                cmdline="",
                create_time=get_process_create_time(pid),
                ownership_status="protected",
                in_job=True,
                proven_runtime_owner=False,
                proof_method="live_job",
                action="skip_protected",
            )
            report.diagnostics.append(diag)
            continue
        diag = _prove_job_member(pid, job=job, known_identities=identities)
        report.diagnostics.append(diag)
        report.timeline.append(
            f"job_member pid={pid} proven={diag.proven_runtime_owner} "
            f"proof={diag.proof_method}"
        )
        if diag.proven_runtime_owner:
            proven.append(diag)
        elif diag.ownership_status == "pid_reuse_rejected":
            report.timeline.append(
                f"pid_reuse_reject pid={pid} — no adopt/terminate"
            )

    # Prefer adopt of proven Job members
    for diag in proven:
        if adopt_callback is None:
            break
        report.timeline.append(
            f"adopt_attempt pid={diag.pid} proof={diag.proof_method}"
        )
        diag.action = "adopt"
        try:
            ok = bool(adopt_callback(diag.pid))
        except Exception as exc:
            ok = False
            report.error = f"adopt_failed:{exc}"
            report.timeline.append(report.error)
            diag.action = "adopt_failed"
        if ok:
            report.decision = "adopt_proven_runtime"
            report.adopted_pid = diag.pid
            report.success = True
            report.proof_method = diag.proof_method
            report.reconcile_reason = "adopt_live_job_member"
            report.mutex_held_after = is_mutex_object_present()
            logger.info(
                "[reconcile] adopted proven job member name=%s pid=%s proof=%s",
                child_name,
                diag.pid,
                diag.proof_method,
            )
            return report

        # Adopt failed → may terminate ONLY this proven member
        ct_verified = bool(
            diag.create_time is not None
            and verify_create_time(diag.pid, diag.create_time)
        ) or diag.proof_method == "live_job"
        decision = TerminateDecision(
            pid=diag.pid,
            why="adopt_failed_clear_proven_job_member",
            proof_method=diag.proof_method or "live_job",
            job_membership=True,
            create_time_verified=ct_verified,
            create_time=diag.create_time,
        )
        # Re-verify membership immediately before kill
        still_in_job = bool(job and job.is_pid_in_job(diag.pid))
        still_identity = True
        if diag.proof_method == "live_job+create_time" and diag.create_time is not None:
            still_identity = verify_create_time(diag.pid, diag.create_time)
        if not still_in_job or not still_identity:
            report.timeline.append(
                f"terminate_skipped pid={diag.pid} "
                f"in_job={still_in_job} identity={still_identity}"
            )
            diag.action = "terminate_skipped_proof_lost"
            continue

        diag.action = "terminate"
        report.terminate_decisions.append(decision)
        report.terminate_reason = decision.why
        report.proof_method = decision.proof_method
        report.timeline.append(
            f"terminate pid={diag.pid} why={decision.why} "
            f"proof={decision.proof_method} job=1 create_time_ok={ct_verified}"
        )
        result = terminate_pid(diag.pid, force=True)
        report.timeline.append(
            f"terminate_result pid={diag.pid} ok={result.get('ok')}"
        )
        logger.info(
            "[reconcile] terminate proven job member name=%s pid=%s "
            "why=%s proof=%s ok=%s",
            child_name,
            diag.pid,
            decision.why,
            decision.proof_method,
            result.get("ok"),
        )

        wait_s = reconcile_terminate_wait_seconds()
        freed = wait_until_mutex_absent(timeout_seconds=wait_s)
        report.timeline.append(f"wait_mutex_absent timeout={wait_s} freed={freed}")
        report.mutex_held_after = is_mutex_object_present()
        if not report.mutex_held_after:
            report.decision = "terminate_proven_and_spawn"
            report.reconcile_reason = "cleared_proven_job_member"
            ok = bool(spawn_callback() if spawn_callback else False)
            report.spawned = ok
            report.success = ok
            if not ok:
                report.error = "spawn_failed_after_proven_terminate"
            return report

    # Foreign holder: mutex present, no proven Job member explains it
    report.foreign_mutex_holder = True
    report.reconcile_reason = "foreign_mutex_holder"
    report.timeline.append(
        "foreign_mutex_holder → wait (no heuristic terminate)"
    )
    logger.warning(
        "[reconcile] foreign mutex holder name=%s attempt=%s "
        "live_job_members=%s — waiting, not terminating",
        child_name,
        attempt,
        live_members,
    )

    if alert_callback is not None:
        try:
            alert_callback(report)
        except Exception as exc:
            report.timeline.append(f"alert_callback_failed:{exc}")
            logger.warning("[reconcile] alert callback failed: %s", exc)

    wait_s = reconcile_wait_seconds()
    freed = wait_until_mutex_absent(timeout_seconds=wait_s)
    report.timeline.append(f"foreign_wait timeout={wait_s} freed={freed}")
    report.mutex_held_after = is_mutex_object_present()

    if not report.mutex_held_after:
        report.decision = "foreign_cleared_spawn"
        report.reconcile_reason = "foreign_holder_gone"
        report.foreign_mutex_holder = False
        report.timeline.append("mutex_free after foreign wait → spawn")
        ok = bool(spawn_callback() if spawn_callback else False)
        report.spawned = ok
        report.success = ok
        if not ok:
            report.error = "spawn_failed_after_foreign_cleared"
        return report

    if attempt >= max_attempts:
        report.decision = "abandon_foreign_mutex"
        report.error = "foreign_mutex_holder_timeout"
        report.reconcile_reason = "foreign_mutex_holder_abandon"
        report.success = False
        report.timeline.append("abandon: foreign mutex still held after max attempts")
        logger.error(
            "[reconcile] ABANDON foreign mutex name=%s attempts=%s",
            child_name,
            attempt,
        )
        return report

    report.decision = "retry_foreign_mutex"
    report.error = "foreign_mutex_still_held"
    report.success = False
    # Brief pause so monitor ticks do not spin hot
    time.sleep(min(0.05, wait_s))
    return report
