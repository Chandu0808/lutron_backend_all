# app/utils/activity_logger.py

from sqlalchemy.orm import Session
from app.models.activity_logs import ActivityLog
from app.models.user_model import User
from app.models.area import Area
from app.models.floor import Floor

def log_activity(
    db: Session,
    user_id: int = None,          # Optional for listener events
    area_id: int = None,
    floor_id: int = None,         # Optional, fallback from area
    activity_type: str = "",
    activity_description: str = ""
):
    """
    Logs user or system (listener) activity into the activity_logs table.
    - GUI actions should pass user_id.
    - Listener actions can omit user_id (null user).
    """

    # Fetch user only if user_id is provided
    user = db.query(User).filter(User.id == user_id).first() if user_id else None

    area = db.query(Area).filter(Area.id == area_id).first() if area_id else None
    floor = db.query(Floor).filter(Floor.id == floor_id).first() if floor_id else area.floor if area else None

    log = ActivityLog(
        user_id=user.id if user else None,
        user_name=user.name if user else None,
        area_id=area.id if area else None,
        area_name=area.name if area else None,
        area_code=area.code if area else None,
        floor_id=floor.id if floor else None,
        floor_name=floor.name if floor else None,
        activity_type=activity_type,
        activity_description=activity_description
    )

    db.add(log)
    db.commit()
    db.refresh(log)
    return log
