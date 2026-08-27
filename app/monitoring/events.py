"""
Monitoring event models and Event Builder validation (Phase 3).

Instrumentation builds events here; MonitoringService persists via Storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union


class EventValidationError(ValueError):
    """Raised when an instrumentation intent cannot become a valid event."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_non_empty(name: str, value: Optional[str]) -> str:
    if value is None or not str(value).strip():
        raise EventValidationError(f"{name} is required")
    return str(value).strip()


def _require_status(status: str) -> str:
    normalized = _require_non_empty("status", status).lower()
    allowed = {"up", "degraded", "down", "unknown", "starting", "stopping"}
    if normalized not in allowed:
        raise EventValidationError(
            f"status must be one of {sorted(allowed)}, got {status!r}"
        )
    return normalized


def _cap_detail(detail: Optional[Dict[str, Any]], max_keys: int = 50) -> Dict[str, Any]:
    if not detail:
        return {}
    if not isinstance(detail, dict):
        raise EventValidationError("detail must be a dict")
    items = list(detail.items())[:max_keys]
    return dict(items)


@dataclass(frozen=True)
class HeartbeatEvent:
    component_code: str
    status: str
    observed_at: datetime
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectivityEvent:
    processor_id: int
    status: str
    event_type: str
    observed_at: datetime
    observer_component_code: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LeapPingEvent:
    processor_id: int
    success: bool
    sampled_at: datetime
    rtt_ms: Optional[int] = None
    observer_component_code: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobRunEvent:
    job_key: str
    outcome: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_class: Optional[str] = None
    error_message: Optional[str] = None
    host_pid: Optional[int] = None
    trigger_source: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpAggregateEvent:
    component_code: str
    bucket_start: datetime
    route_template: str
    method: str
    status_class: str
    request_count: int
    error_count: int = 0
    sum_duration_ms: int = 0
    max_duration_ms: int = 0


@dataclass(frozen=True)
class MetricSampleEvent:
    metric_key: str
    value: float
    sampled_at: datetime
    component_code: Optional[str] = None
    processor_id: Optional[int] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LifecycleEvent:
    component_code: str
    event_type: str
    event_at: datetime
    severity: str = "info"
    detail: Dict[str, Any] = field(default_factory=dict)
    fingerprint: Optional[str] = None


MonitoringEvent = Union[
    HeartbeatEvent,
    ConnectivityEvent,
    LeapPingEvent,
    JobRunEvent,
    HttpAggregateEvent,
    MetricSampleEvent,
    LifecycleEvent,
]

_JOB_OUTCOMES = {
    "success",
    "failure",
    "skipped_lock",
    "skipped_overlap",
    "running",
}

_HTTP_STATUS_CLASSES = {"2xx", "3xx", "4xx", "5xx", "other"}


def build_heartbeat(
    *,
    component_code: str,
    status: str,
    observed_at: Optional[datetime] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> HeartbeatEvent:
    return HeartbeatEvent(
        component_code=_require_non_empty("component_code", component_code),
        status=_require_status(status),
        observed_at=observed_at or _utcnow(),
        detail=_cap_detail(detail),
    )


def build_connectivity(
    *,
    processor_id: int,
    status: str,
    event_type: str,
    observed_at: Optional[datetime] = None,
    observer_component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> ConnectivityEvent:
    if not isinstance(processor_id, int) or processor_id <= 0:
        raise EventValidationError("processor_id must be a positive int")
    et = _require_non_empty("event_type", event_type)
    return ConnectivityEvent(
        processor_id=processor_id,
        status=_require_status(status),
        event_type=et,
        observed_at=observed_at or _utcnow(),
        observer_component_code=(
            observer_component_code.strip() if observer_component_code else None
        ),
        detail=_cap_detail(detail),
    )


def build_leap_ping(
    *,
    processor_id: int,
    success: bool,
    sampled_at: Optional[datetime] = None,
    rtt_ms: Optional[int] = None,
    observer_component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> LeapPingEvent:
    if not isinstance(processor_id, int) or processor_id <= 0:
        raise EventValidationError("processor_id must be a positive int")
    if rtt_ms is not None and (not isinstance(rtt_ms, int) or rtt_ms < 0):
        raise EventValidationError("rtt_ms must be a non-negative int or None")
    return LeapPingEvent(
        processor_id=processor_id,
        success=bool(success),
        sampled_at=sampled_at or _utcnow(),
        rtt_ms=rtt_ms,
        observer_component_code=(
            observer_component_code.strip() if observer_component_code else None
        ),
        detail=_cap_detail(detail),
    )


def build_job_run(
    *,
    job_key: str,
    outcome: str,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    host_pid: Optional[int] = None,
    trigger_source: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> JobRunEvent:
    outcome_n = _require_non_empty("outcome", outcome).lower()
    if outcome_n not in _JOB_OUTCOMES:
        raise EventValidationError(
            f"outcome must be one of {sorted(_JOB_OUTCOMES)}, got {outcome!r}"
        )
    started = started_at or _utcnow()
    msg = error_message[:2048] if error_message else None
    return JobRunEvent(
        job_key=_require_non_empty("job_key", job_key),
        outcome=outcome_n,
        started_at=started,
        finished_at=finished_at or (started if outcome_n != "running" else None),
        duration_ms=duration_ms,
        error_class=error_class,
        error_message=msg,
        host_pid=host_pid,
        trigger_source=trigger_source,
        detail=_cap_detail(detail),
    )


def build_http_aggregate(
    *,
    component_code: str,
    bucket_start: datetime,
    route_template: str,
    method: str,
    status_class: str,
    request_count: int,
    error_count: int = 0,
    sum_duration_ms: int = 0,
    max_duration_ms: int = 0,
) -> HttpAggregateEvent:
    sc = _require_non_empty("status_class", status_class).lower()
    if sc not in _HTTP_STATUS_CLASSES:
        raise EventValidationError(
            f"status_class must be one of {sorted(_HTTP_STATUS_CLASSES)}"
        )
    if request_count < 0 or error_count < 0:
        raise EventValidationError("request_count/error_count must be >= 0")
    method_n = _require_non_empty("method", method).upper()
    route = _require_non_empty("route_template", route_template)
    if len(route) > 256:
        raise EventValidationError("route_template too long")
    return HttpAggregateEvent(
        component_code=_require_non_empty("component_code", component_code),
        bucket_start=bucket_start,
        route_template=route,
        method=method_n,
        status_class=sc,
        request_count=int(request_count),
        error_count=int(error_count),
        sum_duration_ms=int(sum_duration_ms),
        max_duration_ms=int(max_duration_ms),
    )


def build_metric_sample(
    *,
    metric_key: str,
    value: float,
    sampled_at: Optional[datetime] = None,
    component_code: Optional[str] = None,
    processor_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> MetricSampleEvent:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise EventValidationError("value must be numeric") from exc
    if processor_id is not None and processor_id <= 0:
        raise EventValidationError("processor_id must be positive when set")
    return MetricSampleEvent(
        metric_key=_require_non_empty("metric_key", metric_key),
        value=numeric,
        sampled_at=sampled_at or _utcnow(),
        component_code=component_code.strip() if component_code else None,
        processor_id=processor_id,
        detail=_cap_detail(detail),
    )


def build_lifecycle(
    *,
    component_code: str,
    event_type: str,
    event_at: Optional[datetime] = None,
    severity: str = "info",
    detail: Optional[Dict[str, Any]] = None,
    fingerprint: Optional[str] = None,
) -> LifecycleEvent:
    sev = _require_non_empty("severity", severity).lower()
    return LifecycleEvent(
        component_code=_require_non_empty("component_code", component_code),
        event_type=_require_non_empty("event_type", event_type),
        event_at=event_at or _utcnow(),
        severity=sev,
        detail=_cap_detail(detail),
        fingerprint=fingerprint,
    )
