from sqlalchemy.orm import Session
from datetime import datetime, date, time
from typing import List, Optional, Tuple
from app.models.activity_report import ActivityReport
from app.models.area import Area
from app.crud.area_tree import get_area_tree_by_floor


def get_area_codes_from_floor(db: Session, floor_ids: List[int]) -> List[str]:
    """
    Fetch all area codes for a list of floor_ids.
    Always return as strings to match ActivityReport.area_code type.
    """
    codes = db.query(Area.code).filter(Area.floor_id.in_(floor_ids)).all()
    return [str(c[0]) for c in codes if c[0] is not None]


def _collect_child_codes(node: dict, target_codes: set, collected: set):
    """
    Recursive helper to collect all child area codes for a given target area code.
    """
    if node["area_code"] in target_codes:
        def add_all_descendants(n):
            collected.add(str(n["area_code"]))  # always keep as string
            for child in n.get("children", []):
                add_all_descendants(child)
        add_all_descendants(node)
    else:
        for child in node.get("children", []):
            _collect_child_codes(child, target_codes, collected)


def expand_area_codes_with_children(
    db: Session,
    floor_ids: Optional[List[int]],
    area_codes: Optional[List[str]]
) -> Optional[List[str]]:
    """
    Expand a list of area codes to include all their child areas
    using the area tree of the floor.
    Always returns codes as strings.
    """
    if not area_codes:
        return None

    target_codes = set(str(c) for c in area_codes)  # ensure string type
    collected_codes = set()

    if floor_ids:
        for floor_id in set(floor_ids):
            floor_tree = get_area_tree_by_floor(db, floor_id)
            for root in floor_tree:
                _collect_child_codes(root, target_codes, collected_codes)

    # If no children found, just return the original codes
    return list(collected_codes) if collected_codes else list(target_codes)


from sqlalchemy import and_

def get_area_ids_and_processor_ids(db: Session, area_ids: List[int]) -> List[Tuple[int, int]]:
    """
    Fetch area_id and processor_id pairs for a list of area_ids.
    Returns list of tuples (area_id, processor_id).
    """
    if not area_ids:
        return []
    
    results = db.query(Area.id, Area.processor_id).filter(Area.id.in_(area_ids)).all()
    return [(area_id, processor_id) for area_id, processor_id in results if processor_id is not None]


def expand_area_ids_with_children(
    db: Session,
    floor_ids: Optional[List[int]],
    area_ids: Optional[List[int]]
) -> Optional[List[int]]:
    """
    Expand a list of area_ids to include all their child areas
    using the area tree of the floor.
    Returns list of area_ids.
    """
    if not area_ids:
        return None

    target_ids = set(area_ids)
    collected_ids = set()

    if floor_ids:
        for floor_id in set(floor_ids):
            floor_tree = get_area_tree_by_floor(db, floor_id)
            for root in floor_tree:
                _collect_child_ids(root, target_ids, collected_ids)

    # If no children found, just return the original ids
    return list(collected_ids) if collected_ids else list(target_ids)


def _collect_child_ids(node: dict, target_ids: set, collected: set):
    """
    Recursive helper to collect all child area ids for a given target area id.
    """
    if node.get("area_id") in target_ids:
        def add_all_descendants(n):
            if n.get("area_id"):
                collected.add(n["area_id"])
            for child in n.get("children", []):
                add_all_descendants(child)
        add_all_descendants(node)
    else:
        for child in node.get("children", []):
            _collect_child_ids(child, target_ids, collected)


def fetch_activity_report(
    db: Session,
    activity_type: Optional[str],
    floor_ids: Optional[List[int]],
    area_codes: Optional[List[str]],
    activity_desc_keywords: Optional[List[str]],
    start_date: date,
    start_time: time,
    end_date: date,
    end_time: time,
):
    """
    Fetch activity report entries from the activity_report table,
    filtered by type, floor, area, and time range.
    Maps frontend input 'DeviceControl' to DB value 'Device Control'.
    Only shows Device Control logs if sub_activity_type is present (Button events)
    and area_name is not empty.
    """
    start_dt = datetime.combine(start_date, start_time)
    end_dt = datetime.combine(end_date, end_time)

    q = db.query(ActivityReport).filter(
        ActivityReport.created_at >= start_dt,
        ActivityReport.created_at <= end_dt
    )

    # Normalize DeviceControl → Device Control
    def normalize_type(val: str) -> str:
        return "Device Control" if val == "DeviceControl" else val

    # Filter by activity type
    if activity_type:
        norm_type = normalize_type(activity_type)
        q = q.filter(ActivityReport.activity_type == norm_type)

        # Only show Device Control if sub_activity_type exists (Button logs)
        # and area_name is not empty
        if norm_type == "Device Control":
            q = q.filter(
                ActivityReport.sub_activity_type.isnot(None),
                ActivityReport.area_name.isnot(None),
                ActivityReport.area_name != ""
            )
        # Filter out Lights, Shades, Occupancy, Scene without area_id or area_name
        elif norm_type in ["Lights", "Shades", "Occupancy", "Scene"]:
            q = q.filter(
                ActivityReport.area_id.isnot(None),
                ActivityReport.area_name.isnot(None),
                ActivityReport.area_name != ""
            )
    else:
        # Even when no activity_type filter is given,
        # exclude Device Control rows without sub_activity_type or with empty area_name
        q = q.filter(
            ~(
                (ActivityReport.activity_type == "Device Control") &
                (
                    (ActivityReport.sub_activity_type.is_(None)) |
                    (ActivityReport.area_name.is_(None)) |
                    (ActivityReport.area_name == "")
                )
            )
        )
        # Exclude Lights, Shades, Occupancy, Scene without area_id or area_name
        q = q.filter(
            ~(
                (ActivityReport.activity_type.in_(["Lights", "Shades", "Occupancy", "Scene"])) &
                (
                    (ActivityReport.area_id.is_(None)) |
                    (ActivityReport.area_name.is_(None)) |
                    (ActivityReport.area_name == "")
                )
            )
        )

    # Filter by floors (requires join with Area since floor_id is not in activity_report)
    if floor_ids:
        q = q.join(Area, Area.id == ActivityReport.area_id).filter(
            Area.floor_id.in_(floor_ids)
        )

    # Filter by specific area codes (directly from activity_report.area_code)
    if area_codes:
        str_codes = [str(code) for code in area_codes if code is not None]
        if str_codes:
            q = q.filter(ActivityReport.area_code.in_(str_codes))
        else:
            q = q.filter(False)  # force empty if invalid area_codes provided

    # Filter by UI categories (Lights, Scene, etc.)
    if activity_desc_keywords:
        mapped_keywords = [normalize_type(kw) for kw in activity_desc_keywords]
        q = q.filter(ActivityReport.activity_type.in_(mapped_keywords))

    results = q.order_by(ActivityReport.created_at.desc()).all()
    return results


def fetch_activity_report_by_area_ids(
    db: Session,
    activity_type: Optional[str],
    floor_ids: Optional[List[int]],
    area_ids: Optional[List[int]],
    activity_desc_keywords: Optional[List[str]],
    start_date: date,
    start_time: time,
    end_date: date,
    end_time: time,
):
    """
    Fetch activity report entries from the activity_report table,
    filtered by type, floor, area_ids, and time range.
    Uses area_id and processor_id for more accurate filtering.
    Maps frontend input 'DeviceControl' to DB value 'Device Control'.
    Only shows Device Control logs if sub_activity_type is present (Button events)
    and area_name is not empty.
    """
    start_dt = datetime.combine(start_date, start_time)
    end_dt = datetime.combine(end_date, end_time)

    q = db.query(ActivityReport).filter(
        ActivityReport.created_at >= start_dt,
        ActivityReport.created_at <= end_dt
    )

    # Normalize DeviceControl → Device Control
    def normalize_type(val: str) -> str:
        return "Device Control" if val == "DeviceControl" else val

    # Filter by activity type
    if activity_type:
        norm_type = normalize_type(activity_type)
        q = q.filter(ActivityReport.activity_type == norm_type)

        # Only show Device Control if sub_activity_type exists (Button logs)
        # and area_name is not empty
        if norm_type == "Device Control":
            q = q.filter(
                ActivityReport.sub_activity_type.isnot(None),
                ActivityReport.area_name.isnot(None),
                ActivityReport.area_name != ""
            )
        # Filter out Lights, Shades, Occupancy, Scene without area_id or area_name
        elif norm_type in ["Lights", "Shades", "Occupancy", "Scene"]:
            q = q.filter(
                ActivityReport.area_id.isnot(None),
                ActivityReport.area_name.isnot(None),
                ActivityReport.area_name != ""
            )
    else:
        # Even when no activity_type filter is given,
        # exclude Device Control rows without sub_activity_type or with empty area_name
        q = q.filter(
            ~(
                (ActivityReport.activity_type == "Device Control") &
                (
                    (ActivityReport.sub_activity_type.is_(None)) |
                    (ActivityReport.area_name.is_(None)) |
                    (ActivityReport.area_name == "")
                )
            )
        )
        # Exclude Lights, Shades, Occupancy, Scene without area_id or area_name
        q = q.filter(
            ~(
                (ActivityReport.activity_type.in_(["Lights", "Shades", "Occupancy", "Scene"])) &
                (
                    (ActivityReport.area_id.is_(None)) |
                    (ActivityReport.area_name.is_(None)) |
                    (ActivityReport.area_name == "")
                )
            )
        )

    # Filter by floors (requires join with Area since floor_id is not in activity_report)
    if floor_ids:
        q = q.join(Area, Area.id == ActivityReport.area_id).filter(
            Area.floor_id.in_(floor_ids)
        )

    # Filter by specific area_ids (using both area_id and processor_id for accuracy)
    if area_ids:
        # Get area_id and processor_id pairs for the provided area_ids
        area_processor_pairs = get_area_ids_and_processor_ids(db, area_ids)
        
        if area_processor_pairs:
            # Create conditions for each (area_id, processor_id) pair
            conditions = []
            for area_id, processor_id in area_processor_pairs:
                conditions.append(
                    and_(
                        ActivityReport.area_id == area_id,
                        Area.processor_id == processor_id
                    )
                )
            
            # Join with Area table if not already joined
            if not floor_ids:
                q = q.join(Area, Area.id == ActivityReport.area_id)
            
            # Apply the conditions with OR logic
            from sqlalchemy import or_
            q = q.filter(or_(*conditions))
        else:
            q = q.filter(False)  # force empty if no valid area_ids provided

    # Filter by UI categories (Lights, Scene, etc.)
    if activity_desc_keywords:
        mapped_keywords = [normalize_type(kw) for kw in activity_desc_keywords]
        q = q.filter(ActivityReport.activity_type.in_(mapped_keywords))

    results = q.order_by(ActivityReport.created_at.desc()).all()
    return results