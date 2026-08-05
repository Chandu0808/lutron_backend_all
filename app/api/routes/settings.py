from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.crud.maintenance_report import generate_maintenance_report
from app.models.processor import Processor
from app.models.sensors_and_modules import SensorAndModule
from app.models.drivers import Driver
from app.models.alert_type_display_settings import AlertTypeDisplaySetting
from app.models.user_model import User
from app.schemas.maintenance import MaintenanceReportRequest

router = APIRouter()


class DisableAlertsRequest(BaseModel):
    alert_type: str
    display: bool


CANONICAL_ALERT_TYPES = {
    "processor not responding": "Processor Not Responding",
    "device not responding": "Device Not Responding",
    "ballast failure": "Ballast Failure",
    "lamp failure": "Lamp Failure",
    "other warnings": "Other Warnings",
}


def _normalize_alert_type(alert_type: str) -> str:
    if not isinstance(alert_type, str):
        raise ValueError("alert_type must be a string")
    key = alert_type.strip().lower()
    if key in CANONICAL_ALERT_TYPES:
        return CANONICAL_ALERT_TYPES[key]
    # Allow slight variations (optional but keeps UI resilient)
    key = " ".join(key.split())  # normalize extra spaces
    if key in CANONICAL_ALERT_TYPES:
        return CANONICAL_ALERT_TYPES[key]
    raise ValueError(f"Unknown alert_type: {alert_type}")


def _get_display_settings_map(db: Session) -> Dict[str, bool]:
    # Default all known types to True if DB rows don't exist yet.
    type_map: Dict[str, bool] = {v: True for v in CANONICAL_ALERT_TYPES.values()}
    rows = db.query(AlertTypeDisplaySetting).all()
    for r in rows:
        type_map[r.alert_type] = bool(r.display)
    return type_map


@router.post("/disable_alerts")
def disable_alerts(
    payload: DisableAlertsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only Superadmin can call this feature.
    require_operator_permission_for_scope(
        required_level=5,  # Superadmin-only
        area_ids=None,
        floor_ids=None,
        enforce_on_empty_scope=False,
        db=db,
        current_user=current_user,
    )

    try:
        canonical_type = _normalize_alert_type(payload.alert_type)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # 1) Update global setting so future alerts remain hidden while disabled.
    setting = (
        db.query(AlertTypeDisplaySetting)
        .filter(AlertTypeDisplaySetting.alert_type == canonical_type)
        .first()
    )
    if setting:
        setting.display = payload.display
    else:
        setting = AlertTypeDisplaySetting(alert_type=canonical_type, display=payload.display)
        db.add(setting)
    db.commit()

    # 2) Update existing rows for the requested alert type.
    #
    # Note: read-side filtering will always consult the global setting too, so
    # newly created alerts after this call will also be hidden.
    if canonical_type == "Processor Not Responding":
        db.query(Processor).update({Processor.display: payload.display})
    elif canonical_type == "Device Not Responding":
        db.query(SensorAndModule).update({SensorAndModule.display: payload.display})
    elif canonical_type == "Ballast Failure":
        db.query(Driver).filter(Driver.error_code == "E2").update({Driver.display: payload.display})
    elif canonical_type == "Lamp Failure":
        db.query(Driver).filter(Driver.error_code == "FC").update({Driver.display: payload.display})
    elif canonical_type == "Other Warnings":
        # Keep consistent with read-side classification of "Other Warnings":
        # exclude NULL/None and empty-string error_code, and exclude E2/FC.
        q = db.query(Driver).filter(
            Driver.error_code.isnot(None),
            Driver.error_code != "",
            Driver.error_code.notin_(["E2", "FC"]),
        )
        q.update({Driver.display: payload.display})
    else:
        # Should never happen due to normalization, but keeps it safe.
        raise HTTPException(status_code=400, detail="Unsupported alert_type")

    db.commit()
    return {"status": "success", "alert_type": canonical_type, "display": payload.display}


@router.get("/alerts_display_status")
def alerts_display_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Only Superadmin can call this feature.
    require_operator_permission_for_scope(
        required_level=5,  # Superadmin-only
        area_ids=None,
        floor_ids=None,
        enforce_on_empty_scope=False,
        db=db,
        current_user=current_user,
    )

    type_map = _get_display_settings_map(db)
    ordered_types = [
        "Processor Not Responding",
        "Device Not Responding",
        "Ballast Failure",
        "Lamp Failure",
        "Other Warnings",
    ]

    return {
        "status": "success",
        "toggles": [
            {"alert_type": t, "display": bool(type_map.get(t, True))}
            for t in ordered_types
        ],
    }


@router.post("/maintenance")
def maintenance_report(
    payload: MaintenanceReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch devices or occupancy mode data from handshake-complete processors and return a CSV report.
    Device types and occupancy_mode are mutually exclusive.
    Continues when some processors are unreachable; fails only when none respond.
    """
    require_operator_permission_for_scope(
        required_level=1,
        area_ids=None,
        floor_ids=None,
        enforce_on_empty_scope=False,
        db=db,
        current_user=current_user,
    )

    result = generate_maintenance_report(db, payload.types)

    if result["status"] == "error":
        if result["message"] in (
            "No processors configured",
            "No processors with completed handshake",
            "occupancy_mode cannot be combined with device types",
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=result)

    return result

