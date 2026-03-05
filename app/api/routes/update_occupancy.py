from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.schemas.update_occupancy import UpdateOccupancyRequest
from app.crud.update_occupancy import (
    update_area_occupancy_setting,
    get_area_occupancy_setting,
    update_group_occupancy_setting,
    get_area_group_occupancy_setting
)
from app.models.user_model import User
from app.utils.activity_logger import log_activity
from app.utils.activity_report_logger import activity_report_log
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.area import Area


router = APIRouter()

@router.post("/occupancy/update_setting")
def update_occupancy_setting(
    request: UpdateOccupancyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Set occupancy mode (Auto/Disabled/Vacant) for a specific area."""
    try:
        # 1) Resolve area & floor
        area = db.query(Area).filter(Area.id == request.area_id).first()
        if not area:
            raise HTTPException(status_code=404, detail="Area not found")

        # 2) User-friendly permission check: need control (>=2) on this floor
        try:
            require_operator_permission_for_scope(
                required_level=2,  # monitor_control
                floor_ids=[area.floor_id],
                enforce_on_empty_scope=True,
                db=db,
                current_user=current_user
            )
        except HTTPException as e:
            if e.status_code == 403:
                return {
                    "status": "failed",
                    "message": f"Not authorized to update occupancy settings in floor {area.floor_id}"
                }
            raise  # re-raise unexpected errors

        # 3) Perform update
        update_area_occupancy_setting(db, request.area_id, request.occupancy_mode)

        # 4) Activity report logs (clean descriptions)
        try:
            # # Entity-level log
            # activity_report_log(
            #     db=db,
            #     user_id=current_user.id,
            #     area_id=request.area_id,
            #     activity_type="Occupancy",
            #     activity_description=f"Occupancy setting set to '{request.occupancy_mode}'",
            #     area_name=area.name
            # )

            # User-level log
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=request.area_id,
                activity_type="User",
                activity_description=f"Occupancy setting set to '{request.occupancy_mode}'",
                area_name=area.name
            )

        except Exception as log_error:
            print(f"[LOG ERROR] Failed to log occupancy update activity: {log_error}")

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/area/occupancy_setting/{area_id}")
def area_occupancy_setting(area_id: int, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    """Get the current active occupancy mode for a specific area."""
    try:
        mode = get_area_occupancy_setting(db, area_id)
        return {"status": "success", "area_id": area_id, "active_mode": mode}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.post("/area_group/update_setting")
def update_area_group_occupancy_setting(
    request: UpdateOccupancyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Update occupancy mode for all areas within a group."""
    try:
        # Perform the group update
        update_group_occupancy_setting(db, request.area_id, request.occupancy_mode)

        try:
            # # Entity-level log
            # activity_report_log(
            #     db=db,
            #     user_id=user.id,
            #     area_id=request.area_id,
            #     activity_type="OccupancyGroup",
            #     activity_description=f"Occupancy setting set to '{request.occupancy_mode}'",
            #     area_name=f"Area Group {request.area_id}"
            # )

            # User-level log
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=request.area_id,
                activity_type="User",
                activity_description=f"Occupancy setting set to '{request.occupancy_mode}'",
                area_name=f"Area Group {request.area_id}"
            )

        except Exception as log_error:
            print(f"[LOG ERROR] Failed to log activity: {log_error}")

        return {"status": "success"}

    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/area_group/occupancy_setting/{group_id}")
def area_group_occupancy_setting(group_id: int, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    """Get overall occupancy status for a group. Returns common mode or 'Mixed'."""
    try:
        group_status = get_area_group_occupancy_setting(db, group_id)
        return {"status": "success", "group_id": group_id, "group_status": group_status}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
