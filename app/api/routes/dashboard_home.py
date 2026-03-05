"""Dashboard home: single GET endpoint aggregating all dashboard widget data."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.user_model import User
from app.crud.dashboard_home import get_dashboard_home_data

router = APIRouter()


@router.get("/dashboard", response_model=None)
def get_dashboard_home(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Single API for dashboard home: energy (kW), top 5 alerts, next schedule,
    floor count, and space utilization from current area events.
    """
    try:
        require_operator_permission_for_scope(
            required_level=1,
            area_ids=None,
            floor_ids=None,
            enforce_on_empty_scope=False,
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "energy": None,
                "alerts": {"total": 0, "top_5": []},
                "schedule": {"next": None},
                "floors": {"count": 0},
                "space_utilization": {
                    "occupied_count": 0,
                    "unoccupied_count": 0,
                    "occupied_percent": 0.0,
                    "unoccupied_percent": 0.0,
                },
            }
        raise
    return get_dashboard_home_data(db, current_user)
