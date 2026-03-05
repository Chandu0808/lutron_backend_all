# app/utils/activity_report_logger.py

from datetime import datetime
from sqlalchemy.orm import Session
from typing import Optional
from app.models.activity_report import ActivityReport
from app.models.area import Area
from app.models.user_model import User

ALLOWED_ACTIVITY_TYPES = {
    "User", "QuickControl", "Schedule", "AreaGroup", "Floor",
    "CCT", "Device Control", "Shades", "Lights", "Occupancy", "Scene"
}

def activity_report_log(
    db: Session,
    *,
    user_id: Optional[int] = None,
    area_id: Optional[int] = None,
    activity_type: str,
    activity_description: str,
    sub_activity_type: Optional[str] = None,   # <-- NEW
    area_name: Optional[str] = None            # <-- override for area_name
):
    """
    Store an activity log in the activity_report table.

    Args:
        db (Session): SQLAlchemy DB session
        user_id (int, optional): ID of the user who performed the action
        area_id (int, optional): ID of the area involved
        activity_type (str): One of ALLOWED_ACTIVITY_TYPES
        activity_description (str): Short description of the activity
        sub_activity_type (str, optional): More granular category of activity
        area_name (str, optional): Explicit override for area_name column
    """

    if activity_type not in ALLOWED_ACTIVITY_TYPES:
        raise ValueError(
            f"Invalid activity_type '{activity_type}'. "
            f"Must be one of: {', '.join(ALLOWED_ACTIVITY_TYPES)}"
        )

    # Use explicit area_name if provided
    resolved_area_name = area_name

    # Otherwise resolve from area_id
    if resolved_area_name is None and area_id:
        area = db.query(Area).filter(Area.id == area_id).first()
        if area:
            if area.floor:
                resolved_area_name = f"{area.floor.name} / {area.name}"
            else:
                resolved_area_name = area.name

    # Resolve user_name
    user_name = None
    if user_id:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user_name = user.name

    report = ActivityReport(
        date=datetime.now().date(),
        time=datetime.now().strftime("%H:%M:%S"),
        area_id=area_id,
        area_name=resolved_area_name,
        activity_type=activity_type,
        sub_activity_type=sub_activity_type,    # <-- NEW
        user_id=user_id,
        user_name=user_name,
        activity_desc=activity_description,
    )

    db.add(report)
    db.commit()
    db.refresh(report)
    return report
