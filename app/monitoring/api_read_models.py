"""
Monitoring API read models (Phase 5).

Composes Storage reads for Monitoring HTTP GET endpoints.
Does not write telemetry (Service owns writes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.monitoring.registry import get_registry
from app.monitoring.service import MonitoringService, get_monitoring_service
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.session import monitoring_session
from app.monitoring.health_stale import effective_health_status


def get_health_board() -> Dict[str, Any]:
    """
    Hot-path health board: components + current health + pipeline runtime status.
    """
    service = get_monitoring_service()
    pipeline = None
    if service is not None:
        status = service.get_runtime_status()
        pipeline = {
            "running": status.running,
            "degraded": status.degraded,
            "health_status": status.health_status,
            "queue_length": status.queue_length,
            "max_queue": status.max_queue,
            "last_success_at": _dt(status.last_success_at),
            "last_error": status.last_error,
            "counters": status.counters,
        }

    registry = get_registry()
    components_meta = {}
    if registry is not None:
        for code, row in registry.components_by_code.items():
            components_meta[code] = {
                "id": str(row.id),
                "code": row.code,
                "kind": row.kind,
                "display_name": row.display_name,
                "is_active": row.is_active,
            }

    health_rows: List[Dict[str, Any]] = []
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        for row in storage.health.get_all_health_current():
            code = None
            if registry is not None:
                for c, comp in registry.components_by_code.items():
                    if comp.id == row.component_id:
                        code = c
                        break
            health_rows.append(
                {
                    "component_id": str(row.component_id),
                    "component_code": code,
                    "status": effective_health_status(
                        row.status, row.last_heartbeat_at
                    ),
                    "last_heartbeat_at": _dt(row.last_heartbeat_at),
                    "updated_at": _dt(row.updated_at),
                    "detail": row.detail_json,
                }
            )

    return {
        "monitoring_enabled": True,
        "pipeline": pipeline,
        "components": list(components_meta.values()),
        "health": health_rows,
    }


def get_connectivity_map() -> Dict[str, Any]:
    # Prefer dashboard assembler (includes leap slot estimate fields).
    try:
        from app.monitoring import dashboard_read_models

        return dashboard_read_models.get_processors()
    except Exception:
        pass
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        rows = storage.connectivity.get_connectivity_current()
    return {
        "processors": [
            {
                "processor_id": r.processor_id,
                "status": r.status,
                "observer_component_id": (
                    str(r.observer_component_id) if r.observer_component_id else None
                ),
                "last_ok_at": _dt(r.last_ok_at),
                "last_error_at": _dt(r.last_error_at),
                "updated_at": _dt(r.updated_at),
                "detail": r.detail_json,
            }
            for r in rows
        ]
    }


def get_jobs_panel() -> Dict[str, Any]:
    registry = get_registry()
    jobs: List[Dict[str, Any]] = []
    with monitoring_session(commit=False) as session:
        storage = MonitoringStorage(session)
        definitions = storage.jobs.list_job_definitions(active_only=True)
        for definition in definitions:
            latest = storage.jobs.get_latest_job_run(definition.id)
            jobs.append(
                {
                    "job_key": definition.job_key,
                    "display_name": definition.display_name,
                    "component_id": str(definition.component_id),
                    "is_active": definition.is_active,
                    "latest_run": None
                    if latest is None
                    else {
                        "id": latest.id,
                        "outcome": latest.outcome,
                        "started_at": _dt(latest.started_at),
                        "finished_at": _dt(latest.finished_at),
                        "duration_ms": latest.duration_ms,
                        "trigger_source": latest.trigger_source,
                    },
                }
            )
    return {
        "jobs": jobs,
        "registry_job_count": (
            len(registry.jobs_by_key) if registry is not None else 0
        ),
    }


def get_pipeline_status() -> Dict[str, Any]:
    service: Optional[MonitoringService] = get_monitoring_service()
    if service is None:
        return {"running": False, "health_status": "down"}
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
    }


def _dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()
