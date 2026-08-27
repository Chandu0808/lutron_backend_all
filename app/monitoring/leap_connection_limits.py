"""
LEAP connection-limit telemetry (whole-app).

Lutron processors allow max 10 concurrent certificate-based LEAP clients.
This module:
- classifies connect failures (esp. max_clients / 503)
- tracks LMS-side estimated active slots (long-lived + short-lived)
- emits monitoring metrics / connectivity (passive; never raises to callers)

Does not open LEAP connections for probing.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("lutron_monitoring.leap_connection_limits")

LEAP_MAX_CONNECTIONS = 10
PRESSURE_RATIO = 0.8  # warn at >= 8/10 estimated

_lock = threading.Lock()
# processor_id -> active LMS-held connections (estimate)
_active_by_processor: Dict[int, int] = {}
# ipv4 -> processor_id cache
_ip_to_processor: Dict[str, Optional[int]] = {}
_reject_total = 0


def classify_leap_connect_error(exc_or_msg: Any) -> str:
    """Map exception / message → stable reason code."""
    text = str(exc_or_msg or "").lower()
    if not text or text == "none":
        return "unknown"
    if (
        "503" in text
        or "serviceunavailable" in text
        or "service unavailable" in text
        or "max number of client" in text
        or "max clients" in text
        or "maximum number of client" in text
        or "too many client" in text
    ):
        return "max_clients"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "refused" in text or "10061" in text or "errno 111" in text:
        return "refused"
    if "certificate" in text or "ssl" in text or "tls" in text:
        return "ssl"
    if "name or service not known" in text or "getaddrinfo" in text:
        return "dns"
    return "other"


def resolve_processor_id_by_ipv4(ipv4: Optional[str]) -> Optional[int]:
    if not ipv4:
        return None
    with _lock:
        if ipv4 in _ip_to_processor:
            return _ip_to_processor[ipv4]
    pid: Optional[int] = None
    try:
        from app.database.session import SessionLocal
        from app.models.processor import Processor

        db = SessionLocal()
        try:
            row = db.query(Processor.id).filter(Processor.ipv4 == ipv4).first()
            pid = int(row[0]) if row else None
        finally:
            db.close()
    except Exception:
        pid = None
    with _lock:
        _ip_to_processor[ipv4] = pid
    return pid


def get_active_estimated(processor_id: Optional[int] = None) -> Any:
    with _lock:
        if processor_id is None:
            return dict(_active_by_processor)
        return int(_active_by_processor.get(int(processor_id), 0))


def get_reject_total() -> int:
    with _lock:
        return int(_reject_total)


def note_slot_acquired(processor_id: Optional[int], *, source: str = "unknown") -> int:
    if processor_id is None:
        return 0
    pid = int(processor_id)
    with _lock:
        _active_by_processor[pid] = int(_active_by_processor.get(pid, 0)) + 1
        active = _active_by_processor[pid]
    _emit_active_gauges(pid, active, source=source)
    return active


def note_slot_released(processor_id: Optional[int], *, source: str = "unknown") -> int:
    if processor_id is None:
        return 0
    pid = int(processor_id)
    with _lock:
        cur = int(_active_by_processor.get(pid, 0))
        active = max(0, cur - 1)
        if active == 0:
            _active_by_processor.pop(pid, None)
        else:
            _active_by_processor[pid] = active
    _emit_active_gauges(pid, active, source=source)
    return active


def _monitoring_leap_ok() -> bool:
    try:
        from app.monitoring.flags import (
            is_monitoring_enabled,
            is_monitoring_leap_telemetry_enabled,
        )

        return bool(is_monitoring_enabled() and is_monitoring_leap_telemetry_enabled())
    except Exception:
        return False


def _emit_active_gauges(processor_id: int, active: int, *, source: str) -> None:
    if not _monitoring_leap_ok():
        return
    try:
        from app.monitoring import instrumentation

        instrumentation.metric(
            "leap.active_estimated",
            float(active),
            processor_id=processor_id,
            detail={"source": source, "max_connections": LEAP_MAX_CONNECTIONS},
        )
        instrumentation.metric(
            "leap.max_connections",
            float(LEAP_MAX_CONNECTIONS),
            processor_id=processor_id,
            detail={"source": source},
        )
        saturated = 1.0 if active >= LEAP_MAX_CONNECTIONS else 0.0
        # Pressure gauge for ratio alerts (active / max)
        instrumentation.metric(
            "leap.connection_pressure_active",
            float(active),
            processor_id=processor_id,
            detail={
                "source": source,
                "ratio": active / float(LEAP_MAX_CONNECTIONS),
            },
        )
        if saturated:
            instrumentation.metric(
                "leap.connection_saturated",
                1.0,
                processor_id=processor_id,
                detail={"reason": "active_at_cap", "active": active},
            )
    except Exception as exc:
        logger.debug("leap active metric skip: %s", exc)


def report_connect_outcome(
    *,
    success: bool,
    processor_id: Optional[int] = None,
    ipv4: Optional[str] = None,
    duration_ms: Optional[float] = None,
    error: Any = None,
    source: str = "json_connection",
    track_short_lived_slot: bool = True,
) -> str:
    """
    Emit connect success/fail metrics. Returns reason code (\"ok\" or classify).

    For short-lived sockets, increments active for the duration of the caller's
    hold only if they call note_slot_acquired/released themselves. Here we only
    count instantaneous connect attempts unless track_short_lived_slot and
    success — then +1 until caller releases is NOT done automatically (callers
    that hold the socket should call note_slot_*). Short-lived open→close
    paths should call note_slot_acquired on success and note_slot_released in
    finally when they want occupancy tracking.
    """
    pid = processor_id
    if pid is None:
        pid = resolve_processor_id_by_ipv4(ipv4)

    reason = "ok" if success else classify_leap_connect_error(error)
    if not _monitoring_leap_ok():
        return reason

    try:
        from app.monitoring import instrumentation
        from app.monitoring.leap_telemetry import report_connectivity

        detail = {
            "source": source,
            "ipv4": ipv4,
            "reason": reason,
            "max_connections": LEAP_MAX_CONNECTIONS,
            "active_estimated": get_active_estimated(pid) if pid else None,
        }
        if duration_ms is not None:
            detail["duration_ms"] = round(float(duration_ms), 2)
            instrumentation.metric(
                "leap.connect_time_ms",
                float(duration_ms),
                processor_id=pid,
                detail=detail,
            )

        if success:
            instrumentation.metric(
                "leap.connect_success_total",
                1.0,
                processor_id=pid,
                detail=detail,
            )
            instrumentation.metric(
                "leap.connection_saturated",
                0.0,
                processor_id=pid,
                detail={**detail, "cleared": True},
            )
        else:
            instrumentation.metric(
                "leap.connect_fail_total",
                1.0,
                processor_id=pid,
                detail=detail,
            )
            if reason == "max_clients":
                global _reject_total
                with _lock:
                    _reject_total += 1
                    rejects = _reject_total
                instrumentation.metric(
                    "leap.max_clients_reject_total",
                    float(rejects),
                    processor_id=pid,
                    detail=detail,
                )
                instrumentation.metric(
                    "leap.connection_saturated",
                    1.0,
                    processor_id=pid,
                    detail=detail,
                )
                if pid is not None:
                    report_connectivity(
                        int(pid),
                        status="degraded",
                        event_type="max_clients",
                        detail={**detail, "event_type": "max_clients", "max_clients": True},
                    )
            elif pid is not None and reason in ("refused", "timeout", "ssl"):
                # Soft signal only — do not overwrite a hard DOWN from listener.
                report_connectivity(
                    int(pid),
                    status="degraded",
                    event_type="connection_failed",
                    detail=detail,
                )
    except Exception as exc:
        logger.debug("leap connect outcome skip: %s", exc)

    return reason


def report_leap_header_status(
    *,
    processor_id: Optional[int],
    header: Optional[Dict[str, Any]],
    source: str = "leap_response",
) -> Optional[str]:
    """
    If a LEAP response Header shows 503 / ServiceUnavailable, treat as max_clients.
    """
    if not isinstance(header, dict):
        return None
    status = str(header.get("StatusCode") or header.get("Status") or "")
    body_type = str(header.get("MessageBodyType") or "")
    blob = f"{status} {body_type}"
    reason = classify_leap_connect_error(blob)
    if reason != "max_clients":
        return None
    report_connect_outcome(
        success=False,
        processor_id=processor_id,
        error=blob,
        source=source,
        track_short_lived_slot=False,
    )
    return reason


# used by tests
def _reset_state_for_tests() -> None:
    global _reject_total
    with _lock:
        _active_by_processor.clear()
        _ip_to_processor.clear()
        _reject_total = 0
