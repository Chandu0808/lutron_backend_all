"""
Phase 6 — live Resource Usage read model (RAM / process status).

Reads PIDs from Runtime Supervisor (via monitoring runtime bridge) and
samples RSS with psutil. No DB writes. No historical samples. No Storage.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("lutron_monitoring.resource_usage")

# Supervised children registered in app.main (exact names).
SUPERVISED_PROCESS_NAMES: Tuple[str, ...] = (
    "listener",
    "energy_logger",
    "loadcontroller_listener",
)

# Stable output order: API first, then supervised children.
PROCESS_ORDER: Tuple[str, ...] = ("api",) + SUPERVISED_PROCESS_NAMES

STATUS_RUNNING = "RUNNING"
STATUS_STOPPED = "STOPPED"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_UNKNOWN = "UNKNOWN"

# PID-reuse defense: create_time must match supervisor identity within slack.
_CREATE_TIME_SLACK_SECONDS = 2.0


def _empty_process(name: str, *, status: str, pid: Optional[int] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "pid": pid,
        "ram_mb": None,
        "ram_percent": None,
    }


def _import_psutil():
    try:
        import psutil

        return psutil
    except Exception as exc:
        logger.warning("[monitoring][resources] psutil unavailable: %s", exc)
        return None


def _system_total_bytes(psutil_mod: Any) -> Optional[int]:
    try:
        total = int(psutil_mod.virtual_memory().total)
        return total if total > 0 else None
    except Exception as exc:
        logger.warning("[monitoring][resources] virtual_memory failed: %s", exc)
        return None


def _rss_metrics(
    *,
    pid: int,
    total_bytes: Optional[int],
    expected_create_time: Optional[float],
    psutil_mod: Any,
) -> Tuple[str, Optional[int], Optional[float], Optional[float]]:
    """
    Returns (status, pid_out, ram_mb, ram_percent).

    Narrow handling for expected process races; logs warnings for unexpected errors.
    """
    NoSuchProcess = psutil_mod.NoSuchProcess
    AccessDenied = psutil_mod.AccessDenied
    ZombieProcess = getattr(psutil_mod, "ZombieProcess", type("ZombieProcess", (Exception,), {}))

    try:
        proc = psutil_mod.Process(int(pid))
        with proc.oneshot():
            if expected_create_time is not None:
                try:
                    create_time = float(proc.create_time())
                    if abs(create_time - float(expected_create_time)) > _CREATE_TIME_SLACK_SECONDS:
                        logger.warning(
                            "[monitoring][resources] pid reuse suspected "
                            "pid=%s expected_ct=%.3f actual_ct=%.3f",
                            pid,
                            expected_create_time,
                            create_time,
                        )
                        return STATUS_NOT_FOUND, None, None, None
                except (NoSuchProcess, AccessDenied, ZombieProcess):
                    raise
                except Exception as exc:
                    logger.debug(
                        "[monitoring][resources] create_time check skipped pid=%s: %s",
                        pid,
                        exc,
                    )

            try:
                proc_status = str(proc.status() or "").lower()
            except (NoSuchProcess, AccessDenied, ZombieProcess):
                raise
            except Exception:
                proc_status = ""

            if proc_status == getattr(psutil_mod, "STATUS_ZOMBIE", "zombie"):
                return STATUS_UNKNOWN, int(pid), None, None

            try:
                rss = int(proc.memory_info().rss)
            except (NoSuchProcess, AccessDenied, ZombieProcess):
                raise
            except Exception as exc:
                logger.warning(
                    "[monitoring][resources] memory_info failed pid=%s: %s", pid, exc
                )
                return STATUS_UNKNOWN, int(pid), None, None

        if rss < 0:
            return STATUS_UNKNOWN, int(pid), None, None

        ram_mb = round(rss / (1024 * 1024), 1)
        ram_percent: Optional[float] = None
        if total_bytes and total_bytes > 0:
            ram_percent = round((rss / total_bytes) * 100.0, 2)

        # Prefer RUNNING when the OS process is inspectable and not zombie.
        return STATUS_RUNNING, int(pid), ram_mb, ram_percent

    except NoSuchProcess:
        logger.debug(
            "[monitoring][resources] process exited before sample pid=%s", pid
        )
        return STATUS_NOT_FOUND, None, None, None
    except ZombieProcess:
        return STATUS_UNKNOWN, int(pid), None, None
    except AccessDenied:
        logger.warning(
            "[monitoring][resources] access denied reading pid=%s", pid
        )
        return STATUS_UNKNOWN, int(pid), None, None
    except Exception as exc:
        logger.warning(
            "[monitoring][resources] unable to read metrics pid=%s: %s", pid, exc
        )
        return STATUS_UNKNOWN, int(pid), None, None


def _map_supervisor_status(
    *,
    state: Optional[str],
    alive: bool,
    pid: Optional[int],
) -> str:
    st = (state or "").strip().upper()
    if pid is None or int(pid) <= 0:
        if st in ("STOPPED", "FAILED", "ABANDONED", "BACKOFF"):
            return STATUS_STOPPED
        if st in ("CREATED", "REGISTERED"):
            return STATUS_NOT_FOUND
        return STATUS_NOT_FOUND
    if alive or st in ("RUNNING", "STARTING", "RESTARTING", "RECONCILING", "ADOPTING"):
        return STATUS_RUNNING
    if st in ("STOPPED", "FAILED", "ABANDONED", "BACKOFF", "STOPPING"):
        return STATUS_STOPPED
    return STATUS_UNKNOWN


def _supervisor_children() -> Tuple[Optional[Dict[str, Dict[str, Any]]], Optional[str]]:
    """
    Return (children_by_name, error_reason).

    Never raises. children_by_name is None when supervisor snapshot unavailable.
    """
    try:
        from app.monitoring.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge()
        if bridge is None or bridge.supervisor is None:
            return None, "Runtime Supervisor is unavailable"
        snap = bridge.supervisor.status()
        children = getattr(snap, "children", None) or []
        by_name: Dict[str, Dict[str, Any]] = {}
        for child in children:
            if hasattr(child, "to_dict"):
                data = child.to_dict()
            elif isinstance(child, dict):
                data = child
            else:
                continue
            name = data.get("name")
            if isinstance(name, str) and name.strip():
                by_name[name.strip()] = data
        return by_name, None
    except Exception as exc:
        logger.warning(
            "[monitoring][resources] supervisor status failed: %s", exc
        )
        return None, "Runtime Supervisor status failed"


def _sample_named_process(
    *,
    name: str,
    pid: Optional[int],
    create_time: Optional[float],
    supervisor_state: Optional[str],
    supervisor_alive: bool,
    total_bytes: Optional[int],
    psutil_mod: Any,
) -> Dict[str, Any]:
    base_status = _map_supervisor_status(
        state=supervisor_state, alive=supervisor_alive, pid=pid
    )

    if pid is None or int(pid) <= 0:
        return _empty_process(name, status=base_status, pid=None)

    status, pid_out, ram_mb, ram_percent = _rss_metrics(
        pid=int(pid),
        total_bytes=total_bytes,
        expected_create_time=create_time,
        psutil_mod=psutil_mod,
    )

    # Prefer OS evidence when available; keep supervisor STOPPED if process gone.
    if status == STATUS_NOT_FOUND:
        if base_status == STATUS_STOPPED:
            return _empty_process(name, status=STATUS_STOPPED, pid=None)
        return _empty_process(name, status=STATUS_NOT_FOUND, pid=None)

    if status == STATUS_UNKNOWN and base_status == STATUS_STOPPED:
        return _empty_process(name, status=STATUS_STOPPED, pid=pid_out)

    # AccessDenied / zombie / sample failure → UNKNOWN with null RAM (do not
    # promote to RUNNING merely because supervisor still reports alive).
    if status == STATUS_UNKNOWN:
        return {
            "name": name,
            "status": STATUS_UNKNOWN,
            "pid": pid_out,
            "ram_mb": None,
            "ram_percent": None,
        }

    return {
        "name": name,
        "status": STATUS_RUNNING if status == STATUS_RUNNING else status,
        "pid": pid_out,
        "ram_mb": ram_mb,
        "ram_percent": ram_percent,
    }


def build_resources_payload() -> Dict[str, Any]:
    """
    Assemble live resource usage payload.

    Always returns a structured dict; never raises to the route.
    """
    psutil_mod = _import_psutil()
    if psutil_mod is None:
        return {
            "available": False,
            "reason": "psutil is not available",
            "host": None,
            "processes": [],
        }

    total_bytes = _system_total_bytes(psutil_mod)
    host = None
    if total_bytes is not None:
        host = {"total_ram_mb": round(total_bytes / (1024 * 1024), 1)}

    children_by_name, supervisor_reason = _supervisor_children()
    processes: List[Dict[str, Any]] = []

    # API process (this uvicorn/worker). Scheduler RAM is shared — do not list it.
    api_pid = os.getpid()
    api_row = _sample_named_process(
        name="api",
        pid=api_pid,
        create_time=None,
        supervisor_state="RUNNING",
        supervisor_alive=True,
        total_bytes=total_bytes,
        psutil_mod=psutil_mod,
    )
    processes.append(api_row)

    for name in SUPERVISED_PROCESS_NAMES:
        child = (children_by_name or {}).get(name)
        if child is None:
            processes.append(
                _empty_process(
                    name,
                    status=STATUS_NOT_FOUND if children_by_name is None else STATUS_NOT_FOUND,
                    pid=None,
                )
            )
            continue
        pid = child.get("pid")
        try:
            pid_int = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_int = None
        create_time = child.get("process_create_time")
        try:
            create_time_f = float(create_time) if create_time is not None else None
        except (TypeError, ValueError):
            create_time_f = None
        processes.append(
            _sample_named_process(
                name=name,
                pid=pid_int,
                create_time=create_time_f,
                supervisor_state=str(child.get("state") or "") or None,
                supervisor_alive=bool(child.get("alive")),
                total_bytes=total_bytes,
                psutil_mod=psutil_mod,
            )
        )

    # Sort deterministically.
    order = {n: i for i, n in enumerate(PROCESS_ORDER)}
    processes.sort(key=lambda p: order.get(p.get("name") or "", 999))

    reason = None
    if supervisor_reason and children_by_name is None:
        reason = supervisor_reason

    return {
        "available": True,
        "reason": reason,
        "host": host,
        "processes": processes,
    }
