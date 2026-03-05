"""
Dashboard home: single API aggregating energy, alerts (top 5), next schedule,
floor count, and space utilization from current area events.
"""
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import case, func

from app.models.events import CurrentAreaEvent
from app.models.area import Area
from app.models.floor import Floor
from app.crud.dashboard_home_helpers import (
    get_active_alerts_list_for_dashboard,
    get_next_schedule_occurrence,
)


def get_dashboard_home_data(db: Session, current_user: Any) -> Dict[str, Any]:
    """
    Aggregate all dashboard home widget data in one response.
    Each section is wrapped in try/except so one failure does not break the whole payload.
    """
    result = {
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

    try:
        result["energy"] = _get_dashboard_energy(db, current_user)
    except Exception:
        pass

    try:
        alerts_list = get_active_alerts_list_for_dashboard(db, current_user)
        result["alerts"] = {
            "total": len(alerts_list),
            "top_5": alerts_list[:5],
        }
    except Exception:
        pass

    try:
        result["schedule"]["next"] = get_next_schedule_occurrence(db, current_user)
    except Exception:
        pass

    try:
        result["floors"]["count"] = _get_dashboard_floor_count(db, current_user)
    except Exception:
        pass

    try:
        result["space_utilization"] = _get_dashboard_space_utilization(
            db, current_user
        )
    except Exception:
        pass

    return result


def _allowed_floor_ids_for_user(db: Session, current_user: Any) -> Optional[List[int]]:
    """For Operator return list of permitted floor ids; for Admin/Superadmin return None (all)."""
    if getattr(current_user, "role", None) != "Operator":
        return None
    perms = getattr(current_user, "user_permissions", [])
    return [p.floor_id for p in perms if getattr(p, "floor_id", None) is not None]


def _area_ids_for_scope(
    db: Session, current_user: Any
) -> Optional[List[int]]:
    """Return area ids to scope by (None = all areas)."""
    floor_ids = _allowed_floor_ids_for_user(db, current_user)
    if floor_ids is None:
        return None
    rows = (
        db.query(Area.id).filter(Area.floor_id.in_(floor_ids)).distinct().all()
    )
    return [r[0] for r in rows] if rows else []


def _get_dashboard_energy(
    db: Session, current_user: Any
) -> Optional[Dict[str, Any]]:
    """
    From CurrentAreaEvent: sum instantaneous_power (consumption) and
    (instantaneous_max_power - instantaneous_power) (savings); return in kW.
    """
    area_ids = _area_ids_for_scope(db, current_user)
    q = db.query(
        func.coalesce(func.sum(CurrentAreaEvent.instantaneous_power), 0).label(
            "total_power"
        ),
        func.coalesce(func.sum(CurrentAreaEvent.instantaneous_max_power), 0).label(
            "max_power"
        ),
    )
    if area_ids is not None:
        q = q.filter(CurrentAreaEvent.area_id.in_(area_ids))
    row = q.first()
    if not row:
        return {
            "consumption_kw": 0.0,
            "savings_kw": 0.0,
            "savings_percent": 0.0,
        }
    total_power = float(row.total_power or 0)
    max_power = float(row.max_power or 0)
    savings_power = max(0, max_power - total_power)
    consumption_kw = round(total_power / 1000.0, 2)
    savings_kw = round(savings_power / 1000.0, 2)
    total_all = total_power + savings_power
    savings_percent = (
        round((savings_power / total_all) * 100, 2) if total_all > 0 else 0.0
    )
    return {
        "consumption_kw": consumption_kw,
        "savings_kw": savings_kw,
        "savings_percent": savings_percent,
    }


def _get_dashboard_floor_count(db: Session, current_user: Any) -> int:
    """Count of floors; for Operator only count permitted floors."""
    floor_ids = _allowed_floor_ids_for_user(db, current_user)
    if floor_ids is None:
        return db.query(Floor).count()
    return db.query(Floor).filter(Floor.id.in_(floor_ids)).count()


def _get_dashboard_space_utilization(
    db: Session, current_user: Any
) -> Dict[str, Any]:
    """
    From CurrentAreaEvent: count rows with occupancy_status Occupied/Unoccupied,
    compute percentages (only areas with sensor data).
    """
    area_ids = _area_ids_for_scope(db, current_user)
    q = db.query(
        func.sum(
            case((CurrentAreaEvent.occupancy_status == "Occupied", 1), else_=0)
        ).label("occupied"),
        func.sum(
            case((CurrentAreaEvent.occupancy_status == "Unoccupied", 1), else_=0)
        ).label("unoccupied"),
    ).filter(
        CurrentAreaEvent.occupancy_status.in_(["Occupied", "Unoccupied"])
    )
    if area_ids is not None:
        q = q.filter(CurrentAreaEvent.area_id.in_(area_ids))
    row = q.first()
    occ = int(row.occupied or 0) if row else 0
    unocc = int(row.unoccupied or 0) if row else 0
    total = occ + unocc
    if total == 0:
        return {
            "occupied_count": 0,
            "unoccupied_count": 0,
            "occupied_percent": 0.0,
            "unoccupied_percent": 0.0,
        }
    opct = round((occ / total) * 100, 2)
    return {
        "occupied_count": occ,
        "unoccupied_count": unocc,
        "occupied_percent": opct,
        "unoccupied_percent": round(100.0 - opct, 2),
    }
