"""Parse HTTP ingest payloads into MonitoringEvent instances (Phase 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.monitoring.events import (
    EventValidationError,
    MonitoringEvent,
    build_connectivity,
    build_heartbeat,
    build_http_aggregate,
    build_job_run,
    build_leap_ping,
    build_lifecycle,
    build_metric_sample,
)


def _parse_dt(value: Any, field_name: str) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EventValidationError(f"invalid {field_name}: {value!r}") from exc
    raise EventValidationError(f"invalid {field_name} type")


def _as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"{field_name} must be an int") from exc


def parse_monitoring_event(payload: Dict[str, Any]) -> MonitoringEvent:
    """
    Map a JSON object to a typed MonitoringEvent.

    Required field: ``event_type`` one of:
    heartbeat, connectivity, leap_ping, job_run, http_aggregate, metric, lifecycle
    """
    if not isinstance(payload, dict):
        raise EventValidationError("event payload must be an object")

    event_type = (payload.get("event_type") or payload.get("type") or "").strip().lower()
    if not event_type:
        raise EventValidationError("event_type is required")

    if event_type == "heartbeat":
        return build_heartbeat(
            component_code=payload.get("component_code"),
            status=payload.get("status"),
            observed_at=_parse_dt(payload.get("observed_at"), "observed_at"),
            detail=payload.get("detail"),
        )

    if event_type == "connectivity":
        return build_connectivity(
            processor_id=_as_int(payload.get("processor_id"), "processor_id"),
            status=payload.get("status"),
            event_type=payload.get("connectivity_event_type")
            or payload.get("connectivity_type")
            or "connectivity_changed",
            observed_at=_parse_dt(payload.get("observed_at"), "observed_at"),
            observer_component_code=payload.get("observer_component_code"),
            detail=payload.get("detail"),
        )

    if event_type in ("leap_ping", "ping"):
        return build_leap_ping(
            processor_id=_as_int(payload.get("processor_id"), "processor_id"),
            success=bool(payload.get("success")),
            sampled_at=_parse_dt(payload.get("sampled_at"), "sampled_at"),
            rtt_ms=payload.get("rtt_ms"),
            observer_component_code=payload.get("observer_component_code"),
            detail=payload.get("detail"),
        )

    if event_type in ("job_run", "job"):
        return build_job_run(
            job_key=payload.get("job_key"),
            outcome=payload.get("outcome"),
            started_at=_parse_dt(payload.get("started_at"), "started_at"),
            finished_at=_parse_dt(payload.get("finished_at"), "finished_at"),
            duration_ms=payload.get("duration_ms"),
            error_class=payload.get("error_class"),
            error_message=payload.get("error_message"),
            host_pid=payload.get("host_pid"),
            trigger_source=payload.get("trigger_source"),
            detail=payload.get("detail"),
        )

    if event_type in ("http_aggregate", "http"):
        bucket_start = _parse_dt(payload.get("bucket_start"), "bucket_start")
        if bucket_start is None:
            raise EventValidationError("bucket_start is required")
        return build_http_aggregate(
            component_code=payload.get("component_code"),
            bucket_start=bucket_start,
            route_template=payload.get("route_template"),
            method=payload.get("method"),
            status_class=payload.get("status_class"),
            request_count=payload.get("request_count", 0),
            error_count=payload.get("error_count", 0),
            sum_duration_ms=payload.get("sum_duration_ms", 0),
            max_duration_ms=payload.get("max_duration_ms", 0),
        )

    if event_type in ("metric", "metric_sample"):
        return build_metric_sample(
            metric_key=payload.get("metric_key"),
            value=payload.get("value"),
            sampled_at=_parse_dt(payload.get("sampled_at"), "sampled_at"),
            component_code=payload.get("component_code"),
            processor_id=payload.get("processor_id"),
            detail=payload.get("detail"),
        )

    if event_type == "lifecycle":
        return build_lifecycle(
            component_code=payload.get("component_code"),
            event_type=payload.get("lifecycle_event_type") or payload.get("lifecycle_type"),
            event_at=_parse_dt(payload.get("event_at"), "event_at"),
            severity=payload.get("severity") or "info",
            detail=payload.get("detail"),
            fingerprint=payload.get("fingerprint"),
        )

    raise EventValidationError(f"unsupported event_type={event_type!r}")


def parse_ingest_payload(body: Dict[str, Any]) -> List[MonitoringEvent]:
    """
    Accept either a single event object or ``{"events": [ ... ]}``.
    """
    if not isinstance(body, dict):
        raise EventValidationError("body must be a JSON object")

    if "events" in body:
        raw_events = body.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise EventValidationError("events must be a non-empty list")
        if len(raw_events) > 100:
            raise EventValidationError("events batch exceeds limit of 100")
        return [parse_monitoring_event(item) for item in raw_events]

    return [parse_monitoring_event(body)]
