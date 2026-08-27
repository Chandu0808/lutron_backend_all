"""
Monitoring Instrumentation SDK (Phase 3+).

Producers call these helpers only. Never opens DB sessions or calls Storage.

Submit routing:
- Local MonitoringService when ``attach(service)`` (API process)
- RemoteClient when ``attach_remote(client)`` (daemon processes)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from app.monitoring import events as event_builder
from app.monitoring.events import EventValidationError
from app.monitoring.service import AcceptResult, MonitoringService

if TYPE_CHECKING:
    from app.monitoring.remote_client import RemoteClient

logger = logging.getLogger("lutron_monitoring.instrumentation")

_service: Optional[MonitoringService] = None
_remote_client: Optional["RemoteClient"] = None


def attach(service: MonitoringService) -> None:
    """Wire Instrumentation to an in-process MonitoringService."""
    global _service
    _service = service


def detach() -> None:
    global _service
    _service = None


def attach_remote(client: "RemoteClient") -> None:
    """Wire Instrumentation to HTTP RemoteClient (daemon processes)."""
    global _remote_client
    _remote_client = client


def detach_remote() -> None:
    global _remote_client
    _remote_client = None


def get_attached_service() -> Optional[MonitoringService]:
    return _service


def get_attached_remote() -> Optional["RemoteClient"]:
    return _remote_client


def _submit_safe(event) -> Optional[AcceptResult]:
    svc = _service
    if svc is not None:
        try:
            return svc.submit(event)
        except Exception as exc:
            logger.warning("[monitoring][instrumentation] submit failed: %s", exc)
            return AcceptResult(accepted=False, rejected=True, reason="submit_error")

    remote = _remote_client
    if remote is not None:
        try:
            return remote.submit(event)
        except Exception as exc:
            logger.warning("[monitoring][instrumentation] remote submit failed: %s", exc)
            return AcceptResult(accepted=False, rejected=True, reason="remote_error")

    return None


def heartbeat(
    component_code: str,
    status: str,
    detail: Optional[Dict[str, Any]] = None,
    *,
    observed_at: Optional[datetime] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_heartbeat(
            component_code=component_code,
            status=status,
            observed_at=observed_at,
            detail=detail,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid heartbeat: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] heartbeat error: %s", exc)
        return None


def connectivity(
    processor_id: int,
    status: str,
    event_type: str,
    *,
    observer_component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    observed_at: Optional[datetime] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_connectivity(
            processor_id=processor_id,
            status=status,
            event_type=event_type,
            observer_component_code=observer_component_code,
            detail=detail,
            observed_at=observed_at,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid connectivity: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] connectivity error: %s", exc)
        return None


def leap_ping(
    processor_id: int,
    success: bool,
    *,
    rtt_ms: Optional[int] = None,
    observer_component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    sampled_at: Optional[datetime] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_leap_ping(
            processor_id=processor_id,
            success=success,
            rtt_ms=rtt_ms,
            observer_component_code=observer_component_code,
            detail=detail,
            sampled_at=sampled_at,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid leap_ping: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] leap_ping error: %s", exc)
        return None


def job_run(
    job_key: str,
    outcome: str,
    *,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    host_pid: Optional[int] = None,
    trigger_source: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_job_run(
            job_key=job_key,
            outcome=outcome,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_class=error_class,
            error_message=error_message,
            host_pid=host_pid,
            trigger_source=trigger_source,
            detail=detail,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid job_run: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] job_run error: %s", exc)
        return None


def lifecycle(
    component_code: str,
    event_type: str,
    *,
    severity: str = "info",
    detail: Optional[Dict[str, Any]] = None,
    fingerprint: Optional[str] = None,
    event_at: Optional[datetime] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_lifecycle(
            component_code=component_code,
            event_type=event_type,
            severity=severity,
            detail=detail,
            fingerprint=fingerprint,
            event_at=event_at,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid lifecycle: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] lifecycle error: %s", exc)
        return None


def metric(
    metric_key: str,
    value: float,
    *,
    component_code: Optional[str] = None,
    processor_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    sampled_at: Optional[datetime] = None,
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_metric_sample(
            metric_key=metric_key,
            value=value,
            component_code=component_code,
            processor_id=processor_id,
            detail=detail,
            sampled_at=sampled_at,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid metric: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] metric error: %s", exc)
        return None


def http_aggregate(
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
) -> Optional[AcceptResult]:
    try:
        event = event_builder.build_http_aggregate(
            component_code=component_code,
            bucket_start=bucket_start,
            route_template=route_template,
            method=method,
            status_class=status_class,
            request_count=request_count,
            error_count=error_count,
            sum_duration_ms=sum_duration_ms,
            max_duration_ms=max_duration_ms,
        )
        return _submit_safe(event)
    except EventValidationError as exc:
        logger.warning("[monitoring][instrumentation] invalid http_aggregate: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[monitoring][instrumentation] http_aggregate error: %s", exc)
        return None
