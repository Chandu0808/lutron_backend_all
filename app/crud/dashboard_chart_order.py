from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.crud import dashboard_layout as layout_crud
from app.models.dashboard_chart_order import (
    SINGLETON_ROW_ID,
    DashboardChartOrder,
)
from app.models.dashboard_layout import DashboardLayout

ENERGY_SLOT_ORDER_DEFAULT: List[str] = [
    "consumption",
    "consumption_saving",
    "savings",
    "savings_by_strategy",
    "total_consumption_by_group",
    "light_power_density",
    "peak_and_minimum_consumption",
]

SPACE_CHARTS_TAB_ORDER_DEFAULT: List[str] = [
    "instant_occupancy_count",
    "instant_utilization_combined",
    "utilization_by_area_group",
    "utilization_by_area",
    "peak_and_minimum_utilization",
]

SPACE_MAIN_TAB_ORDER_DEFAULT: List[str] = [
    "utilization",
    "utilization_by_area_group",
    "peak_and_minimum_utilization",
    "utilization_by_area",
]

_FIELD_LAYOUT_PAIRS: Tuple[Tuple[str, str, List[str]], ...] = (
    ("energy_slot_order", layout_crud.LAYOUT_KEY_ENERGY_SLOT_ORDER, ENERGY_SLOT_ORDER_DEFAULT),
    (
        "space_charts_tab_order",
        layout_crud.LAYOUT_KEY_SPACE_CHARTS_TAB_ORDER,
        SPACE_CHARTS_TAB_ORDER_DEFAULT,
    ),
    (
        "space_main_tab_order",
        layout_crud.LAYOUT_KEY_SPACE_MAIN_TAB_ORDER,
        SPACE_MAIN_TAB_ORDER_DEFAULT,
    ),
)


def normalize_slot_order(parsed: Optional[List[str]], defaults: List[str]) -> List[str]:
    if not isinstance(parsed, list):
        return list(defaults)
    known = set(defaults)
    next_order = [slot for slot in parsed if isinstance(slot, str) and slot in known]
    for slot in defaults:
        if slot not in next_order:
            next_order.append(slot)
    return next_order


def _get_legacy_row(db: Session) -> Optional[DashboardChartOrder]:
    return (
        db.query(DashboardChartOrder)
        .filter(DashboardChartOrder.id == SINGLETON_ROW_ID)
        .first()
    )


def _read_field(
    db: Session,
    field_name: str,
    layout_key: str,
    legacy_row: Optional[DashboardChartOrder],
) -> Optional[List[str]]:
    layout_row = layout_crud.get_layout(db, layout_key)
    if layout_row is not None:
        return layout_row.layout_json
    if legacy_row is not None:
        return getattr(legacy_row, field_name)
    return None


def _upsert_layout_field(
    db: Session,
    layout_key: str,
    value: List[str],
    *,
    updated_by: Optional[int] = None,
) -> None:
    row = layout_crud.get_layout(db, layout_key)
    if row is None:
        row = DashboardLayout(
            layout_key=layout_key,
            layout_json=value,
            layout_version=1,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.layout_json = value
        row.updated_by = updated_by
    db.flush()


def get_dashboard_chart_order(db: Session) -> Dict[str, Optional[List[str]]]:
    legacy_row = _get_legacy_row(db)
    return {
        field_name: _read_field(db, field_name, layout_key, legacy_row)
        for field_name, layout_key, _defaults in _FIELD_LAYOUT_PAIRS
    }


def upsert_dashboard_chart_order(
    db: Session,
    *,
    energy_slot_order: Optional[List[str]] = None,
    space_charts_tab_order: Optional[List[str]] = None,
    space_main_tab_order: Optional[List[str]] = None,
    updated_by: Optional[int] = None,
) -> Dict[str, Optional[List[str]]]:
    updates = {
        "energy_slot_order": energy_slot_order,
        "space_charts_tab_order": space_charts_tab_order,
        "space_main_tab_order": space_main_tab_order,
    }
    if all(value is None for value in updates.values()):
        return get_dashboard_chart_order(db)

    for field_name, layout_key, defaults in _FIELD_LAYOUT_PAIRS:
        raw = updates[field_name]
        if raw is None:
            continue
        normalized = normalize_slot_order(raw, defaults)
        _upsert_layout_field(db, layout_key, normalized, updated_by=updated_by)

    db.commit()
    return get_dashboard_chart_order(db)
