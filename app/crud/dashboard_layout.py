from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.dashboard_layout import DashboardLayout

LAYOUT_KEY_ENERGY_SLOT_ORDER = "energy_slot_order"
LAYOUT_KEY_SPACE_CHARTS_TAB_ORDER = "space_charts_tab_order"
LAYOUT_KEY_SPACE_MAIN_TAB_ORDER = "space_main_tab_order"
LAYOUT_KEY_DASHBOARD_ORDER = "dashboard_order"

CANONICAL_LAYOUT_KEYS = frozenset(
    {
        LAYOUT_KEY_ENERGY_SLOT_ORDER,
        LAYOUT_KEY_SPACE_CHARTS_TAB_ORDER,
        LAYOUT_KEY_SPACE_MAIN_TAB_ORDER,
        LAYOUT_KEY_DASHBOARD_ORDER,
    }
)

DEFAULT_LAYOUT_JSON: Dict[str, Any] = {
    LAYOUT_KEY_ENERGY_SLOT_ORDER: [
        "consumption",
        "consumption_saving",
        "savings",
        "savings_by_strategy",
        "total_consumption_by_group",
        "light_power_density",
        "peak_and_minimum_consumption",
    ],
    LAYOUT_KEY_SPACE_CHARTS_TAB_ORDER: [
        "instant_occupancy_count",
        "instant_utilization_combined",
        "utilization_by_area_group",
        "utilization_by_area",
        "peak_and_minimum_utilization",
    ],
    LAYOUT_KEY_SPACE_MAIN_TAB_ORDER: [
        "utilization",
        "utilization_by_area_group",
        "peak_and_minimum_utilization",
        "utilization_by_area",
    ],
    LAYOUT_KEY_DASHBOARD_ORDER: [],
}


def get_layout(db: Session, layout_key: str) -> Optional[DashboardLayout]:
    return (
        db.query(DashboardLayout)
        .filter(DashboardLayout.layout_key == layout_key)
        .first()
    )


def list_layouts(db: Session) -> List[DashboardLayout]:
    return db.query(DashboardLayout).order_by(DashboardLayout.layout_key).all()


def upsert_layout(
    db: Session,
    layout_key: str,
    layout_json: Any,
    *,
    layout_version: int = 1,
    updated_by: Optional[int] = None,
) -> DashboardLayout:
    row = get_layout(db, layout_key)
    if row is None:
        row = DashboardLayout(
            layout_key=layout_key,
            layout_json=layout_json,
            layout_version=layout_version,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.layout_json = layout_json
        row.layout_version = layout_version
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def reset_layout(
    db: Session,
    layout_key: str,
    default_json: Optional[Any] = None,
    *,
    layout_version: int = 1,
    updated_by: Optional[int] = None,
) -> DashboardLayout:
    value = (
        deepcopy(default_json)
        if default_json is not None
        else deepcopy(DEFAULT_LAYOUT_JSON.get(layout_key, []))
    )
    return upsert_layout(
        db,
        layout_key,
        value,
        layout_version=layout_version,
        updated_by=updated_by,
    )
