"""
Monitoring Dashboard REST API (Phase 12).

JWT-authenticated read endpoints + alert acknowledge.
Assembles responses only via dashboard_read_models (no direct Storage in routes).
Superadmin-only via existing ``require_admin`` dependency.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies.auth import require_admin
from app.models.user_model import User
from app.monitoring import dashboard_read_models
from app.monitoring.flags import is_monitoring_enabled
from app.schemas.monitoring_dashboard import (
    AlertAcknowledgeResponse,
    MonitoringResourcesResponse,
    MonitoringStatusResponse,
    MonitoringStatusUpdateRequest,
    MonitoringSummaryResponse,
    PaginatedAlertsResponse,
    PaginatedIssuesResponse,
)

router = APIRouter()


def _require_monitoring_enabled() -> None:
    if not is_monitoring_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Monitoring is disabled",
        )


@router.get("/status", response_model=MonitoringStatusResponse)
def monitoring_status(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """
    Effective monitoring enablement status.

    Intentionally NOT gated by effective monitoring so Superadmin can see OFF.
    """
    del user  # auth via require_admin
    return dashboard_read_models.get_monitoring_status()


@router.patch("/status", response_model=MonitoringStatusResponse)
def monitoring_status_update(
    body: MonitoringStatusUpdateRequest,
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """
    Persist monitoring_enabled in installation_settings (Phase 7).

    Rejected with 409 when MONITORING_ENABLED environment override forces OFF.
    """
    try:
        return dashboard_read_models.set_monitoring_status(
            enabled=bool(body.enabled),
            updated_by=int(user.id) if getattr(user, "id", None) is not None else None,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to persist monitoring status: {exc}",
        ) from exc


@router.get("/summary", response_model=MonitoringSummaryResponse)
def monitoring_summary(user: User = Depends(require_admin)) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_summary()


@router.get("/components")
def monitoring_components(user: User = Depends(require_admin)) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_components()


@router.get("/processors")
def monitoring_processors(user: User = Depends(require_admin)) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_processors()


@router.get("/alerts", response_model=PaginatedAlertsResponse)
def monitoring_alerts(
    user: User = Depends(require_admin),
    status_filter: Optional[str] = Query(
        None, alias="status", description="open|acknowledged|resolved"
    ),
    severity: Optional[str] = Query(None),
    component: Optional[str] = Query(None, description="component_code"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    if status_filter and status_filter not in ("open", "acknowledged", "resolved"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be open|acknowledged|resolved",
        )
    return dashboard_read_models.get_alerts(
        status=status_filter,
        severity=severity,
        component=component,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=AlertAcknowledgeResponse,
)
def monitoring_acknowledge_alert(
    alert_id: int,
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    try:
        return dashboard_read_models.acknowledge_alert(alert_id, user_id=int(user.id))
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Alert not found or not open",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unable to acknowledge alert: {exc}",
        ) from exc


@router.get("/jobs")
def monitoring_dashboard_jobs(
    user: User = Depends(require_admin),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_jobs(limit=limit)


@router.get("/http")
def monitoring_http(user: User = Depends(require_admin)) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_http()


@router.get("/analytics")
def monitoring_analytics(
    user: User = Depends(require_admin),
    bucket_size: Optional[str] = Query(None, description="1h|1d"),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    if bucket_size and bucket_size not in ("1h", "1d"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="bucket_size must be 1h or 1d",
        )
    return dashboard_read_models.get_analytics(bucket_size=bucket_size, limit=limit)


@router.get("/pipeline")
def monitoring_dashboard_pipeline(
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_pipeline()


# ----- Runtime Recovery (observer / read-only) -----


@router.get("/runtime/events")
def monitoring_runtime_events(
    user: User = Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
    event_type: Optional[str] = Query(
        None, description="e.g. ChildFailed or runtime.ChildFailed"
    ),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_runtime_events(
        limit=limit, event_type=event_type
    )


@router.get("/runtime/restarts")
def monitoring_runtime_restarts(
    user: User = Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_runtime_restart_history(limit=limit)


@router.get("/runtime/abandoned")
def monitoring_runtime_abandoned(
    user: User = Depends(require_admin),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_runtime_abandoned(limit=limit)


@router.get("/runtime/supervisor")
def monitoring_runtime_supervisor(
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_runtime_supervisor_status()


@router.get("/runtime/restart-counts")
def monitoring_runtime_restart_counts(
    user: User = Depends(require_admin),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    _require_monitoring_enabled()
    return dashboard_read_models.get_runtime_restart_counts(limit=limit)


@router.get("/issues", response_model=PaginatedIssuesResponse)
def monitoring_issues(
    user: User = Depends(require_admin),
    status_filter: Optional[str] = Query(
        None, alias="status", description="open|acknowledged|resolved"
    ),
    severity: Optional[str] = Query(None),
    component: Optional[str] = Query(None, description="component_code"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    Product Application Issues read model (Phase 2–5 contract).

    Built from mon_alert_instance + classification + optional job/runtime/
    exception enrichment. Does not mutate alert state.
    """
    _require_monitoring_enabled()
    if status_filter and status_filter not in ("open", "acknowledged", "resolved"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be open|acknowledged|resolved",
        )
    return dashboard_read_models.get_issues(
        status=status_filter,
        severity=severity,
        component=component,
        limit=limit,
        offset=offset,
    )


@router.get("/resources", response_model=MonitoringResourcesResponse)
def monitoring_resources(user: User = Depends(require_admin)) -> Dict[str, Any]:
    """
    Live Resource Usage (Phase 6): process status, PID, RSS RAM.

    Storage / historical RAM samples are intentionally absent.
    """
    _require_monitoring_enabled()
    return dashboard_read_models.get_resources()
