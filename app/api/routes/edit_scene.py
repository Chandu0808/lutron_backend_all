from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.schemas.scene_edit import EditSceneRequest, SceneStatusInput
from app.crud.edit_scene import edit_scene_assignments, get_scene_status
from app.utils.logger import logger
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.models.area import Area
from app.utils.activity_logger import log_activity
from app.utils.activity_report_logger import activity_report_log
from app.api.routes.full_area_status import get_scene_name_safe
from app.models.processor import Processor

router = APIRouter()

@router.post("/edit")
def edit_scene(
    request: EditSceneRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Update a scene's zone assignments (switched, dimmed, white-tune) based on area and scene ID.
    Logs only to activity_report_log with scene name.
    """
    area = db.query(Area).filter(Area.id == request.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    # Resolve processor → scene name
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    scene_name = get_scene_name_safe(processor, request.scene_id)

    try:
        result = edit_scene_assignments(
            db=db,
            area_id=request.area_id,
            scene_id=request.scene_id,
            details=[
                detail.model_dump() if hasattr(detail, "model_dump") else detail.dict()
                for detail in request.details
            ]
        )

        # Log into activity_report_log (user-level)
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=area.id,
            activity_type="User",
            activity_description=f"Edited scene '{scene_name}' configuration",
            area_name=area.name
        )

        return result

    except HTTPException as http_ex:
        raise http_ex
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error during scene update")

@router.post("/scene_status")
def scene_status(
    input: SceneStatusInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Get all current zone-level assignments for the specified area and scene.
    No logging recorded.
    """
    logger.info(f"[API] POST /scene_status | area_id={input.area_id}, scene_id={input.scene_id}")
    try:
        result = get_scene_status(db, input.area_id, input.scene_id)
        logger.info("[API] Scene status fetch successful")
        return result
    except HTTPException as http_ex:
        logger.error(f"[API] HTTPException in /scene_status: {http_ex.detail}")
        raise http_ex
    except Exception as e:
        logger.exception("[API] Unexpected error in /scene_status")
        raise HTTPException(status_code=500, detail="Internal server error during scene status fetch")
