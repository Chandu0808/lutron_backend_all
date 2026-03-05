from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database.session import get_db
from app.models.area import Area
from app.models.user_model import User
from app.crud.device import get_device_lock_status_by_area, toggle_device_lock_by_button
from app.utils.logger import logger
from app.dependencies.auth import get_current_user
from app.utils.activity_logger import log_activity
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.activity_report_logger import activity_report_log

router = APIRouter()


# -------------------- Schemas -------------------- #
class ButtonStatusRequest(BaseModel):
    area_id: int

class ButtonUpdateRequest(BaseModel):
    area_id: int
    buttoncode: int


# -------------------- Routes -------------------- #

@router.post("/button_status")
def get_button_status(
    request: ButtonStatusRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns device lock status for a given area.
    Requires control permission (level 2) on the area's floor.
    User-friendly: returns {status: "failed", ...} if unauthorized.
    """

    # 1) Resolve area to get floor_id
    area = db.query(Area).filter(Area.id == request.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    # 2) Permission check (control = 2) with user-friendly fallback
    try:
        require_operator_permission_for_scope(
            required_level=2,                 # monitor_control
            floor_ids=[area.floor_id],        # enforce on the area's floor
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": f"Not authorized to view button status for floor {area.floor_id}",
            }
        raise  # re-raise unexpected errors (e.g., 422)

    # 3) Fetch device lock / button status (no logs)
    result = get_device_lock_status_by_area(db, area)
    if result.get("status") != "success":
        raise HTTPException(
            status_code=500,
            detail=result.get("message", "Error retrieving button status"),
        )

    return result



@router.post("/button_update")
def toggle_button(
    request: ButtonUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Toggle a button lock/unlock state for a device in the given area.
    Uses LED status to determine final Locked/Unlocked state.
    Logs both internal activity and activity report.
    """
    area = db.query(Area).filter(Area.id == request.area_id).first()
    if not area:
        logger.warning(
            "[BUTTON UPDATE] Area not found. User=%s, AreaID=%d",
            user.email, request.area_id
        )
        raise HTTPException(status_code=404, detail="Area not found")

    # Call helper to toggle lock/unlock and fetch new state
    result = toggle_device_lock_by_button(db, area, request.buttoncode)
    if result["status"] != "success":
        logger.error(
            "[BUTTON UPDATE] Failed. User=%s, AreaID=%d, ButtonCode=%d, Reason=%s",
            user.email, request.area_id, request.buttoncode, result.get("message", "Unknown")
        )
        raise HTTPException(
            status_code=500,
            detail=result.get("message", "Error toggling button")
        )

    # Determine final lock state
    device_info = result.get("devices", [{}])[0]
    lock_state = device_info.get("status", "Unknown")

    # Success: log activity (no buttoncode shown, only Locked/Unlocked)
    log_msg = f"Device {lock_state}"

    log_activity(
        db=db,
        user_id=user.id,
        area_id=area.id,
        activity_type="GUI Trigger",
        activity_description=log_msg
    )

    activity_report_log(
        db=db,
        user_id=user.id,
        area_id=area.id,
        activity_type="User",
        activity_description=log_msg,
        area_name=area.name
    )

    logger.info(
        "[BUTTON UPDATE] Success. %s for AreaID=%d by User=%s",
        log_msg, request.area_id, user.email
    )

    return {"status": "success", "message": log_msg}
