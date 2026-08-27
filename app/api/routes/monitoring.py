"""
Monitoring HTTP API (Phase 5).

- POST /monitoring/ingest — shared-secret authenticated event ingest
- GET  /monitoring/health — component/pipeline health board
- GET  /monitoring/pipeline — pipeline runtime status
- GET  /monitoring/processors/connectivity — connectivity current map
- GET  /monitoring/jobs — system job panel

No daemon clients in this phase — synthetic HTTP ingest only.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.dependencies.auth import require_admin
from app.dependencies.monitoring_auth import require_monitoring_ingest_token
from app.models.user_model import User
from app.monitoring.events import EventValidationError
from app.monitoring.flags import (
    is_monitoring_enabled,
    is_monitoring_ingest_enabled,
)
from app.monitoring.ingest import parse_ingest_payload
from app.monitoring.service import get_monitoring_service
from app.monitoring import api_read_models
from app.schemas.monitoring import (
    MonitoringIngestResponse,
    MonitoringIngestResultItem,
)

router = APIRouter()


def _require_monitoring_enabled() -> None:
    if not is_monitoring_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring is disabled",
        )


def _require_service_ready():
    _require_monitoring_enabled()
    service = get_monitoring_service()
    if service is None or not service.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring service is not ready",
        )
    return service


@router.get("/health")
def monitoring_health(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """Component health board + pipeline status (Superadmin JWT required)."""
    _require_monitoring_enabled()
    return api_read_models.get_health_board()


@router.get("/pipeline")
def monitoring_pipeline(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """In-process pipeline runtime status."""
    _require_monitoring_enabled()
    return api_read_models.get_pipeline_status()


@router.get("/processors/connectivity")
def monitoring_connectivity(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """Current LEAP connectivity map (empty until later phases emit)."""
    _require_monitoring_enabled()
    return api_read_models.get_connectivity_map()


@router.get("/jobs")
def monitoring_jobs(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """System job definitions + latest runs."""
    _require_monitoring_enabled()
    return api_read_models.get_jobs_panel()


@router.post("/ingest", response_model=MonitoringIngestResponse)
async def monitoring_ingest(
    request: Request,
    _token: str = Depends(require_monitoring_ingest_token),
) -> MonitoringIngestResponse:
    """
    Authenticated event ingest.

    Requires ``MONITORING_ENABLED`` and ``MONITORING_INGEST_ENABLED``,
    plus ``MONITORING_INGEST_TOKEN`` via ``X-Monitoring-Ingest-Token``
    (or ``Authorization: Bearer <token>``).

    Body: single event object, or ``{\"events\": [ ... ]}``.
    """
    if not is_monitoring_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring is disabled",
        )
    if not is_monitoring_ingest_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring ingest is disabled",
        )

    service = _require_service_ready()

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON body",
        ) from exc

    try:
        events = parse_ingest_payload(body if isinstance(body, dict) else {})
    except EventValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    results = []
    accepted_count = 0
    dropped_count = 0
    rejected_count = 0

    for event in events:
        accept = service.submit(event)
        event_name = type(event).__name__
        if accept.accepted:
            accepted_count += 1
        if accept.dropped:
            dropped_count += 1
        if accept.rejected:
            rejected_count += 1
        results.append(
            MonitoringIngestResultItem(
                accepted=accept.accepted,
                dropped=accept.dropped,
                rejected=accept.rejected,
                reason=accept.reason,
                event_type=event_name,
            )
        )

    # Backpressure: if everything rejected/dropped due to queue, surface 429/503
    if accepted_count == 0 and dropped_count > 0 and rejected_count == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring ingest backpressured; events dropped",
            headers={"Retry-After": "1"},
        )
    if accepted_count == 0 and rejected_count > 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring service rejected events",
        )

    return MonitoringIngestResponse(
        ok=accepted_count > 0,
        results=results,
        accepted_count=accepted_count,
        dropped_count=dropped_count,
        rejected_count=rejected_count,
    )
