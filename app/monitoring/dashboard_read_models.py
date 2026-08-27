"""
Monitoring Dashboard read models (Phase 12).

Sole assembler for dashboard HTTP responses. Routes must not query Storage.
Acknowledge is the only write path (via AlertStateManager / Storage).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.monitoring.alerts.state_manager import AlertStateManager
from app.monitoring.flags import (
    is_monitoring_alerts_enabled,
    is_monitoring_analytics_enabled,
    is_monitoring_enabled,
    is_monitoring_http_metrics_enabled,
    is_monitoring_ingest_enabled,
    is_monitoring_jobs_enabled,
    is_monitoring_leap_telemetry_enabled,
)
from app.monitoring.health_stale import effective_health_status
from app.monitoring import issue_classification
from app.monitoring.registry import get_registry
from app.monitoring import runtime_recovery_read_model as runtime_recovery
from app.monitoring.exception_capture import extract_exception_fields
from app.monitoring.service import MonitoringService, get_monitoring_service
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.exceptions import StorageNotFoundError
from app.monitoring.storage.session import monitoring_session
from app.monitoring.storage.types import AlertInstanceRow, AlertRuleRow

logger = logging.getLogger("lutron_monitoring.dashboard_read_models")


def _dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _pipeline_dict(service: Optional[MonitoringService]) -> Optional[Dict[str, Any]]:
    if service is None:
        return {
            "running": False,
            "degraded": False,
            "health_status": "down",
            "queue_length": 0,
            "max_queue": 0,
            "last_success_at": None,
            "last_error": None,
            "counters": {},
            "worker_state": "stopped",
        }
    status = service.get_runtime_status()
    return {
        "running": status.running,
        "degraded": status.degraded,
        "health_status": status.health_status,
        "queue_length": status.queue_length,
        "max_queue": status.max_queue,
        "last_success_at": _dt(status.last_success_at),
        "last_error": status.last_error,
        "counters": status.counters,
        "worker_state": "running" if status.running else "stopped",
        "dropped_events": int(
            (status.counters or {}).get("dropped_total", 0)
            or (status.counters or {}).get("queue_full_drop_incoming", 0)
            or 0
        ),
    }


def get_summary() -> Dict[str, Any]:
    service = get_monitoring_service()
    registry = get_registry()
    component_counts = {"total": 0, "up": 0, "down": 0, "degraded": 0, "unknown": 0}
    processor_summary = {"total": 0, "up": 0, "down": 0, "degraded": 0, "other": 0}
    active_alerts = 0
    job_summary: Dict[str, Any] = {
        "definitions": 0,
        "recent_success": 0,
        "recent_failure": 0,
    }
    latest_analytics: Optional[str] = None

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        health_rows = storage.health.get_all_health_current()
        component_counts["total"] = len(health_rows)
        for row in health_rows:
            key = effective_health_status(row.status, row.last_heartbeat_at)
            if key not in component_counts:
                component_counts[key] = 0
            component_counts[key] = component_counts.get(key, 0) + 1

        for row in storage.connectivity.get_connectivity_current():
            processor_summary["total"] += 1
            st = (row.status or "other").lower()
            if st in processor_summary:
                processor_summary[st] += 1
            else:
                processor_summary["other"] += 1

        active_alerts = storage.alerts.count_alert_instances(status="open")
        active_alerts += storage.alerts.count_alert_instances(status="acknowledged")

        defs = storage.jobs.list_job_definitions(active_only=True)
        job_summary["definitions"] = len(defs)
        recent = storage.jobs.list_job_runs(limit=50)
        job_summary["recent_success"] = sum(1 for r in recent if r.outcome == "success")
        job_summary["recent_failure"] = sum(1 for r in recent if r.outcome == "failure")

        rollups = storage.metrics.query_metric_rollups(limit=1)
        if rollups:
            latest_analytics = _dt(rollups[0].bucket_start)

    if registry is not None:
        component_counts["registered"] = len(registry.components_by_code)

    return {
        "monitoring_enabled": is_monitoring_enabled(),
        "pipeline": _pipeline_dict(service),
        "component_counts": component_counts,
        "active_alert_count": active_alerts,
        "processor_status_summary": processor_summary,
        "job_summary": job_summary,
        "latest_analytics_timestamp": latest_analytics,
    }


def get_components() -> Dict[str, Any]:
    registry = get_registry()
    items: List[Dict[str, Any]] = []
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        for row in storage.health.get_all_health_current():
            code = None
            display = None
            if registry is not None:
                for c, comp in registry.components_by_code.items():
                    if comp.id == row.component_id:
                        code = c
                        display = comp.display_name
                        break
            items.append(
                {
                    "component_id": str(row.component_id),
                    "component_code": code,
                    "display_name": display,
                    "status": effective_health_status(
                        row.status, row.last_heartbeat_at
                    ),
                    "last_heartbeat_at": _dt(row.last_heartbeat_at),
                    "updated_at": _dt(row.updated_at),
                    "detail": row.detail_json,
                }
            )
    return {"components": items, "count": len(items)}


def get_processors() -> Dict[str, Any]:
    from app.monitoring.leap_connection_limits import (
        LEAP_MAX_CONNECTIONS,
        get_active_estimated,
    )

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.connectivity.get_connectivity_current()
    active_map = get_active_estimated()
    if not isinstance(active_map, dict):
        active_map = {}
    processors = []
    for r in rows:
        detail = dict(r.detail_json or {})
        active = int(active_map.get(int(r.processor_id), detail.get("active_estimated") or 0) or 0)
        reason = detail.get("reason")
        processors.append(
            {
                "processor_id": r.processor_id,
                "status": r.status,
                "observer_component_id": (
                    str(r.observer_component_id) if r.observer_component_id else None
                ),
                "last_ok_at": _dt(r.last_ok_at),
                "last_error_at": _dt(r.last_error_at),
                "updated_at": _dt(r.updated_at),
                "detail": detail,
                "leap": {
                    "max_connections": LEAP_MAX_CONNECTIONS,
                    "active_estimated": active,
                    "pressure_ratio": round(active / float(LEAP_MAX_CONNECTIONS), 3),
                    "saturated": reason == "max_clients"
                    or bool(detail.get("max_clients"))
                    or active >= LEAP_MAX_CONNECTIONS,
                    "last_connect_reason": reason,
                },
            }
        )
    return {
        "processors": processors,
        "count": len(processors),
        "leap_max_connections": LEAP_MAX_CONNECTIONS,
    }


def get_alerts(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    component: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    component_id: Optional[UUID] = None
    if component:
        registry = get_registry()
        if registry is not None:
            component_id = registry.component_id(component)

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        total = storage.alerts.count_alert_instances(
            status=status, severity=severity, component_id=component_id
        )
        rows = storage.alerts.list_alert_instances(
            status=status,
            severity=severity,
            component_id=component_id,
            limit=limit,
            offset=offset,
        )

    items = [
        {
            "id": r.id,
            "rule_id": str(r.rule_id),
            "status": r.status,
            "severity": r.severity,
            "fingerprint": r.fingerprint,
            "title": r.title,
            "message": r.message,
            "opened_at": _dt(r.opened_at),
            "acknowledged_at": _dt(r.acknowledged_at),
            "resolved_at": _dt(r.resolved_at),
            "acknowledged_by_user_id": r.acknowledged_by_user_id,
            "component_id": str(r.component_id) if r.component_id else None,
            "processor_id": r.processor_id,
            "job_definition_id": (
                str(r.job_definition_id) if r.job_definition_id else None
            ),
            "detail": r.detail_json,
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def acknowledge_alert(alert_id: int, *, user_id: int) -> Dict[str, Any]:
    with monitoring_session(commit=True) as session:
        storage = MonitoringStorage(session)
        manager = AlertStateManager(storage)
        try:
            row = manager.acknowledge(alert_id, user_id=user_id)
        except StorageNotFoundError as exc:
            raise KeyError(str(exc)) from exc
    return {
        "id": row.id,
        "status": row.status,
        "acknowledged_at": _dt(row.acknowledged_at),
        "acknowledged_by_user_id": row.acknowledged_by_user_id,
    }


def get_jobs(*, limit: int = 50) -> Dict[str, Any]:
    registry = get_registry()
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        definitions = storage.jobs.list_job_definitions(active_only=False)
        recent = storage.jobs.list_job_runs(limit=limit)

    def_by_id = {d.id: d for d in definitions}
    recent_items = []
    for run in recent:
        definition = def_by_id.get(run.job_definition_id)
        recent_items.append(
            {
                "id": run.id,
                "job_key": definition.job_key if definition else None,
                "display_name": definition.display_name if definition else None,
                "outcome": run.outcome,
                "started_at": _dt(run.started_at),
                "finished_at": _dt(run.finished_at),
                "duration_ms": run.duration_ms,
                "error_class": run.error_class,
                "trigger_source": run.trigger_source,
            }
        )

    return {
        "recent_runs": recent_items,
        "definitions": [
            {
                "job_key": d.job_key,
                "display_name": d.display_name,
                "is_active": d.is_active,
                "component_id": str(d.component_id),
            }
            for d in definitions
        ],
        "registry_job_count": (
            len(registry.jobs_by_key) if registry is not None else 0
        ),
    }


def get_http(*, limit: int = 100) -> Dict[str, Any]:
    registry = get_registry()
    api_id = registry.component_id("api") if registry else None
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        agg_rows = storage.http_agg.query_http_agg(
            component_id=api_id, limit=limit
        )
        rollup_keys = [
            "analytics.http.request_count",
            "analytics.http.avg_latency_ms",
            "analytics.http.max_latency_ms",
            "analytics.http.error_rate",
        ]
        rollups: List[Dict[str, Any]] = []
        for key in rollup_keys:
            mid = registry.metric_id(key) if registry else None
            if mid is None:
                continue
            for r in storage.metrics.query_metric_rollups(
                metric_definition_id=mid, limit=20
            ):
                rollups.append(
                    {
                        "metric_key": key,
                        "bucket_start": _dt(r.bucket_start),
                        "bucket_size": r.bucket_size,
                        "sample_count": r.sample_count,
                        "avg_value": r.avg_value,
                        "min_value": r.min_value,
                        "max_value": r.max_value,
                        "sum_value": r.sum_value,
                    }
                )

    return {
        "aggregates": [
            {
                "bucket_start": _dt(r.bucket_start),
                "route_template": r.route_template,
                "method": r.method,
                "status_class": r.status_class,
                "request_count": r.request_count,
                "error_count": r.error_count,
                "sum_duration_ms": r.sum_duration_ms,
                "max_duration_ms": r.max_duration_ms,
            }
            for r in agg_rows
        ],
        "rollups": rollups,
    }


def get_analytics(
    *,
    bucket_size: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.metrics.query_metric_rollups(
            bucket_size=bucket_size, limit=limit
        )
        registry = get_registry()
        key_by_id = {}
        if registry is not None:
            key_by_id = {m.id: m.metric_key for m in registry.metrics_by_key.values()}

    return {
        "bucket_size": bucket_size,
        "rollups": [
            {
                "metric_definition_id": str(r.metric_definition_id),
                "metric_key": key_by_id.get(r.metric_definition_id),
                "bucket_start": _dt(r.bucket_start),
                "bucket_size": r.bucket_size,
                "sample_count": r.sample_count,
                "sum_value": r.sum_value,
                "avg_value": r.avg_value,
                "min_value": r.min_value,
                "max_value": r.max_value,
                "component_id": str(r.component_id) if r.component_id else None,
                "processor_id": r.processor_id,
            }
            for r in rows
        ],
        "count": len(rows),
    }


def get_pipeline() -> Dict[str, Any]:
    return _pipeline_dict(get_monitoring_service()) or {
        "running": False,
        "health_status": "down",
        "worker_state": "stopped",
        "queue_length": 0,
        "dropped_events": 0,
    }


# ----- Runtime Recovery observer (Monitoring bridge) ---------------------

_RUNTIME_PREFIX = "runtime."
_RESTART_TYPES = {
    "runtime.RestartRequested",
    "runtime.RestartScheduled",
    "runtime.RestartStarted",
    "runtime.RestartSucceeded",
    "runtime.RestartFailed",
    "runtime.BackoffEntered",
}


def _event_row_dict(row: Any, code_by_id: Dict[UUID, str]) -> Dict[str, Any]:
    payload = row.payload_json or {}
    return {
        "id": row.id,
        "event_at": _dt(row.event_at),
        "event_type": row.event_type,
        "severity": row.severity,
        "component_id": str(row.component_id) if row.component_id else None,
        "component_code": code_by_id.get(row.component_id) if row.component_id else None,
        "fingerprint": row.fingerprint,
        "payload": payload,
        "child_name": payload.get("child_name"),
        "category": payload.get("category"),
    }


def get_runtime_events(
    *,
    limit: int = 100,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Recent Runtime Recovery events from mon_event (read-only)."""
    code_by_id: Dict[UUID, str] = {}
    registry = get_registry()
    if registry is not None:
        code_by_id = {c.id: c.code for c in registry.components_by_code.values()}

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        if event_type:
            et = event_type if event_type.startswith(_RUNTIME_PREFIX) else (
                f"{_RUNTIME_PREFIX}{event_type}"
            )
            rows = storage.events.list_events(event_type=et, limit=limit)
        else:
            rows = storage.events.list_events(
                event_type_prefix=_RUNTIME_PREFIX, limit=limit
            )
    return {
        "events": [_event_row_dict(r, code_by_id) for r in rows],
        "count": len(rows),
    }


def get_runtime_restart_history(*, limit: int = 100) -> Dict[str, Any]:
    code_by_id: Dict[UUID, str] = {}
    registry = get_registry()
    if registry is not None:
        code_by_id = {c.id: c.code for c in registry.components_by_code.values()}

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.events.list_events(
            event_type_prefix=_RUNTIME_PREFIX, limit=max(limit * 3, 200)
        )
    filtered = [r for r in rows if r.event_type in _RESTART_TYPES]
    filtered = filtered[:limit]
    return {
        "restarts": [_event_row_dict(r, code_by_id) for r in filtered],
        "count": len(filtered),
    }


def get_runtime_abandoned(*, limit: int = 100) -> Dict[str, Any]:
    code_by_id: Dict[UUID, str] = {}
    registry = get_registry()
    if registry is not None:
        code_by_id = {c.id: c.code for c in registry.components_by_code.values()}

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.events.list_events(
            event_type="runtime.Abandoned", limit=limit
        )
    return {
        "abandoned": [_event_row_dict(r, code_by_id) for r in rows],
        "count": len(rows),
    }


def get_runtime_supervisor_status() -> Dict[str, Any]:
    """Live supervisor snapshot when bridge is bound; else event-derived."""
    from app.monitoring.runtime_bridge import get_runtime_bridge

    bridge = get_runtime_bridge()
    live: Optional[Dict[str, Any]] = None
    if bridge is not None and bridge.supervisor is not None:
        try:
            snap = bridge.supervisor.status()
            live = snap.to_dict() if hasattr(snap, "to_dict") else None
        except Exception:
            live = None

    return {
        "bridge": bridge.stats() if bridge is not None else None,
        "supervisor": live,
        "monitoring_enabled": is_monitoring_enabled(),
    }


def get_runtime_restart_counts(*, limit: int = 500) -> Dict[str, Any]:
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.events.list_events(
            event_type_prefix=_RUNTIME_PREFIX, limit=limit
        )

    totals: Dict[str, int] = {}
    by_child: Dict[str, Dict[str, int]] = {}
    for row in rows:
        et = row.event_type or ""
        if not et.startswith(_RUNTIME_PREFIX):
            continue
        short = et[len(_RUNTIME_PREFIX) :]
        totals[short] = totals.get(short, 0) + 1
        payload = row.payload_json or {}
        child = payload.get("child_name") or "api"
        bucket = by_child.setdefault(str(child), {})
        bucket[short] = bucket.get(short, 0) + 1

    return {
        "totals": totals,
        "by_child": by_child,
        "sample_size": len(rows),
        "restart_succeeded": totals.get("RestartSucceeded", 0),
        "restart_failed": totals.get("RestartFailed", 0),
        "abandoned": totals.get("Abandoned", 0),
    }


# ----- Phase 2 product read models (status / issues / resources) -----


def get_monitoring_status() -> Dict[str, Any]:
    """
    Effective monitoring status (Phase 7).

    Ungated: safe when monitoring work is OFF. Does not require monitoring
    schema. Distinguishes environment override from installation setting.
    """
    from app.monitoring.flags import (
        is_monitoring_alerts_enabled,
        is_monitoring_analytics_enabled,
        is_monitoring_http_metrics_enabled,
        is_monitoring_ingest_enabled,
        is_monitoring_jobs_enabled,
        is_monitoring_leap_telemetry_enabled,
        resolve_monitoring_status_fields,
    )

    fields = resolve_monitoring_status_fields()
    service = get_monitoring_service()
    pipeline = _pipeline_dict(service)
    return {
        "enabled": fields["enabled"],
        "monitoring_enabled": fields["monitoring_enabled"],
        "source": fields["source"],
        "writable": fields["writable"],
        "environment_enabled": fields["environment_enabled"],
        "configured_enabled": fields["configured_enabled"],
        "configured_defaulted": fields.get("configured_defaulted", False),
        "settings_available": fields.get("settings_available", True),
        "flags": {
            "MONITORING_ENABLED": fields["environment_enabled"],
            "MONITORING_INGEST_ENABLED": is_monitoring_ingest_enabled(),
            "MONITORING_LEAP_TELEMETRY": is_monitoring_leap_telemetry_enabled(),
            "MONITORING_HTTP_METRICS_ENABLED": is_monitoring_http_metrics_enabled(),
            "MONITORING_JOBS_ENABLED": is_monitoring_jobs_enabled(),
            "MONITORING_ALERTS_ENABLED": is_monitoring_alerts_enabled(),
            "MONITORING_ANALYTICS_ENABLED": is_monitoring_analytics_enabled(),
        },
        "service": {
            "running": bool(pipeline.get("running")),
            "degraded": bool(pipeline.get("degraded")),
            "health_status": pipeline.get("health_status"),
            "queue_length": pipeline.get("queue_length"),
            "last_success_at": pipeline.get("last_success_at"),
            "last_error": pipeline.get("last_error"),
        },
    }


def set_monitoring_status(
    *,
    enabled: bool,
    updated_by: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Persist monitoring_enabled and return the resulting status DTO.

    Raises ValueError when environment override forbids writes.
    Propagates DB errors from the settings layer.
    """
    from app.monitoring.flags import (
        is_monitoring_environment_enabled,
        set_configured_monitoring_enabled,
    )

    if not is_monitoring_environment_enabled():
        raise PermissionError(
            "Monitoring is disabled by the MONITORING_ENABLED environment override."
        )
    set_configured_monitoring_enabled(bool(enabled), updated_by=updated_by)
    return get_monitoring_status()


def _issue_error_message(detail: Dict[str, Any], message: Optional[str]) -> Optional[str]:
    for key in ("error", "reason", "error_message", "message"):
        raw = detail.get(key) if detail else None
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if message and str(message).strip():
        return str(message).strip()
    return None


def _component_payload(
    *,
    component_id: Optional[UUID],
    code: Optional[str],
    name: Optional[str],
    kind: Optional[str],
) -> Optional[Dict[str, Any]]:
    if component_id is None and code is None and name is None:
        return None
    return {
        "id": str(component_id) if component_id else None,
        "code": code,
        "name": name,
        "kind": kind,
    }


def _job_payload(
    *,
    job_def: Optional[Any],
    run: Optional[Any],
) -> Optional[Dict[str, Any]]:
    """Build job enrichment only when a job definition is known."""
    if job_def is None:
        return None
    latest = None
    if run is not None:
        run_detail = run.detail_json if isinstance(run.detail_json, dict) else {}
        latest = {
            "id": run.id,
            "outcome": run.outcome,
            "error_class": run.error_class,
            "error_message": run.error_message,
            "duration_ms": run.duration_ms,
            "host_pid": run.host_pid,
            "started_at": _dt(run.started_at),
            "finished_at": _dt(run.finished_at),
            # Bounded exception metadata for Issues error.* enrichment (Phase 5).
            "detail": {"exception": run_detail["exception"]}
            if isinstance(run_detail.get("exception"), dict)
            else {},
        }
    return {
        "job_key": job_def.job_key,
        "display_name": job_def.display_name,
        "job_definition_id": str(job_def.id),
        "latest_run": latest,
    }


def _alert_to_issue(
    row: AlertInstanceRow,
    *,
    rule: Optional[AlertRuleRow],
    component_id: Optional[UUID],
    component_code: Optional[str],
    component_name: Optional[str],
    component_kind: Optional[str],
    job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detail = row.detail_json or {}
    rule_type = rule.rule_type if rule is not None else None
    rule_code = rule.code if rule is not None else None
    source_type = issue_classification.classify_source_type(
        rule_type=rule_type,
        rule_code=rule_code,
        component_code=component_code,
        processor_id=row.processor_id,
    )
    error_type = issue_classification.classify_error_type(
        rule_type=rule_type,
        source_type=source_type,
        detail=detail,
    )
    # Prefer job-run error_class for error.type when present.
    if job and isinstance(job.get("latest_run"), dict):
        run_err = job["latest_run"].get("error_class")
        if isinstance(run_err, str) and run_err.strip():
            error_type = run_err.strip()
        run_msg = job["latest_run"].get("error_message")
        error_message = (
            run_msg
            if isinstance(run_msg, str) and run_msg.strip()
            else _issue_error_message(detail, row.message)
        )
    else:
        error_message = _issue_error_message(detail, row.message)

    # Phase 5: exception file/line/function/traceback from job run (preferred)
    # or alert detail_json when present. Never invent values.
    run_detail = None
    if job and isinstance(job.get("latest_run"), dict):
        run_detail = job["latest_run"].get("detail")
    exc_fields = extract_exception_fields(
        run_detail if isinstance(run_detail, dict) else None,
        detail if isinstance(detail, dict) else None,
    )
    if isinstance(exc_fields.get("type"), str) and exc_fields["type"].strip():
        # Prefer concrete exception type over rule label when captured.
        error_type = exc_fields["type"].strip()
    if isinstance(exc_fields.get("message"), str) and exc_fields["message"].strip():
        if not (isinstance(error_message, str) and error_message.strip()):
            error_message = exc_fields["message"].strip()

    source_id = issue_classification.source_id_for(
        source_type=source_type,
        processor_id=row.processor_id,
        component_code=component_code,
    )

    return {
        "id": row.id,
        "status": row.status,
        "severity": row.severity,
        "title": row.title,
        "message": row.message,
        "fingerprint": row.fingerprint,
        "source": {
            "type": source_type,
            "id": source_id,
        },
        "component": _component_payload(
            component_id=component_id,
            code=component_code,
            name=component_name,
            kind=component_kind,
        ),
        "processor": (
            None if row.processor_id is None else {"id": row.processor_id}
        ),
        "job": job,
        "error": {
            "type": error_type,
            "message": error_message,
            "file": exc_fields.get("file"),
            "line": exc_fields.get("line"),
            "function": exc_fields.get("function"),
            "traceback": exc_fields.get("traceback"),
        },
        "timestamps": {
            "detected_at": _dt(row.opened_at),
            "resolved_at": _dt(row.resolved_at),
        },
        "recovery": runtime_recovery.empty_recovery(),
        "rule": (
            None
            if rule is None
            else {
                "code": rule.code,
                "type": rule.rule_type,
                "display_name": rule.display_name,
            }
        ),
    }


def get_issues(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    component: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Product Issues read model over mon_alert_instance.

    Phase 3: classification + full component resolution + deterministic job
    enrichment via alert.job_definition_id.
    Phase 4: batched runtime recovery enrichment from mon_event (read-only).
    """
    component_id: Optional[UUID] = None
    registry = get_registry()
    if component and registry is not None:
        component_id = registry.component_id(component)

    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        total = storage.alerts.count_alert_instances(
            status=status, severity=severity, component_id=component_id
        )
        rows = storage.alerts.list_alert_instances(
            status=status,
            severity=severity,
            component_id=component_id,
            limit=limit,
            offset=offset,
        )
        rules = {r.id: r for r in storage.alerts.list_alert_rules(enabled_only=False)}

        # Component index: id → ComponentRow fields
        components_by_id: Dict[UUID, Any] = {}
        if registry is not None:
            for _code, comp in registry.components_by_code.items():
                components_by_id[comp.id] = comp

        # Job definitions indexed by id (one list query).
        jobs_by_id = {
            j.id: j for j in storage.jobs.list_job_definitions(active_only=False)
        }

        # Batch latest runs for job_definition_ids present on this page.
        job_ids_needed = {
            r.job_definition_id for r in rows if r.job_definition_id is not None
        }
        latest_run_by_job: Dict[UUID, Any] = {}
        runs_by_job: Dict[UUID, List[Any]] = {}
        for jid in job_ids_needed:
            runs = storage.jobs.list_job_runs(job_definition_id=jid, limit=30)
            runs_by_job[jid] = runs
            if runs:
                latest_run_by_job[jid] = runs[0]

        items: List[Dict[str, Any]] = []
        for row in rows:
            rule = rules.get(row.rule_id)

            # Component resolution: alert.component_id, else job.component_id.
            resolved_comp = None
            if row.component_id and row.component_id in components_by_id:
                resolved_comp = components_by_id[row.component_id]
            elif row.job_definition_id and row.job_definition_id in jobs_by_id:
                job_def = jobs_by_id[row.job_definition_id]
                if job_def.component_id in components_by_id:
                    resolved_comp = components_by_id[job_def.component_id]

            # Deterministic job enrichment only when alert carries job_definition_id.
            job_payload = None
            if row.job_definition_id is not None:
                job_def = jobs_by_id.get(row.job_definition_id)
                run = None
                detail = row.detail_json or {}
                run_ids = detail.get("run_ids")
                if isinstance(run_ids, list) and run_ids:
                    wanted = set()
                    for x in run_ids:
                        try:
                            wanted.add(int(x))
                        except (TypeError, ValueError):
                            continue
                    for candidate in runs_by_job.get(row.job_definition_id, []):
                        if candidate.id in wanted:
                            run = candidate
                            break
                if run is None:
                    run = latest_run_by_job.get(row.job_definition_id)
                job_payload = _job_payload(job_def=job_def, run=run)

            items.append(
                _alert_to_issue(
                    row,
                    rule=rule,
                    component_id=resolved_comp.id if resolved_comp else row.component_id,
                    component_code=resolved_comp.code if resolved_comp else None,
                    component_name=(
                        resolved_comp.display_name if resolved_comp else None
                    ),
                    component_kind=resolved_comp.kind if resolved_comp else None,
                    job=job_payload,
                )
            )

        # Phase 4: one bounded runtime-event query for correlatable children.
        # Best-effort: enrichment failure must not fail the Issues endpoint.
        try:
            bounds = runtime_recovery.compute_query_bounds(items)
            if bounds is not None:
                since, until, child_names = bounds
                component_ids: List[UUID] = []
                if registry is not None:
                    for code in child_names:
                        cid = registry.component_id(code)
                        if cid is not None:
                            component_ids.append(cid)
                event_rows = runtime_recovery.fetch_runtime_recovery_events(
                    storage,
                    child_names=child_names,
                    since=since,
                    until=until,
                    component_ids=component_ids or None,
                )
                runtime_recovery.enrich_issues_with_runtime_recovery(items, event_rows)
        except Exception:
            logger.warning(
                "[monitoring][issues] runtime recovery enrichment failed; "
                "returning issues without recovery",
                exc_info=True,
            )
            for issue in items:
                issue["recovery"] = runtime_recovery.empty_recovery()

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_resources() -> Dict[str, Any]:
    """
    Live Resource Usage (Phase 6).

    Supervisor PIDs + psutil RSS. Never raises; degrades to available=false
    only when psutil itself is missing.
    """
    from app.monitoring.resource_usage_read_model import build_resources_payload

    try:
        return build_resources_payload()
    except Exception:
        logger.exception("[monitoring][resources] unexpected failure")
        return {
            "available": False,
            "reason": "Resource sampling failed",
            "host": None,
            "processes": [],
        }
