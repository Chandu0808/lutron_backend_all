from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.crud import dashboard_layout as layout_crud
from app.crud import variant_dashboard_layout as variant_layout_crud
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

# Span maps + Advanced/Customized full layout blobs (dashboard_layout / variant rows).
LAYOUT_KEY_ENERGY_SLOT_SPAN = "energy_slot_span"
LAYOUT_KEY_SPACE_CHARTS_TAB_SPAN = "space_charts_tab_span"
LAYOUT_KEY_SPACE_MAIN_TAB_SPAN = "space_main_tab_span"
LAYOUT_KEY_ADVANCED_DASHBOARD_ORDER = "advanced_dashboard_order"
LAYOUT_KEY_CUSTOMIZED_DASHBOARD_ORDER = "customized_dashboard_order"

_SPAN_LAYOUT_KEYS: Tuple[Tuple[str, str], ...] = (
    ("energy_slot_span", LAYOUT_KEY_ENERGY_SLOT_SPAN),
    ("space_charts_tab_span", LAYOUT_KEY_SPACE_CHARTS_TAB_SPAN),
    ("space_main_tab_span", LAYOUT_KEY_SPACE_MAIN_TAB_SPAN),
)

_BLOB_LAYOUT_KEYS: Tuple[Tuple[str, str], ...] = (
    ("advanced_dashboard_order", LAYOUT_KEY_ADVANCED_DASHBOARD_ORDER),
    ("customized_dashboard_order", LAYOUT_KEY_CUSTOMIZED_DASHBOARD_ORDER),
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


def normalize_span_map(parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            continue
        out[key.strip()] = value
    return out


def normalize_dashboard_order_blob(parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            continue
        if key.endswith("Span"):
            out[key] = normalize_span_map(value if isinstance(value, dict) else {})
        elif isinstance(value, list):
            out[key] = [item for item in value if isinstance(item, str)]
        elif isinstance(value, dict):
            out[key] = value
    return out


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


def _read_json_layout(db: Session, layout_key: str) -> Optional[Any]:
    layout_row = layout_crud.get_layout(db, layout_key)
    if layout_row is None:
        return None
    return layout_row.layout_json


def _read_variant_or_shared_json(
    db: Session,
    variant: str,
    layout_key: str,
) -> Optional[Any]:
    row = variant_layout_crud.get_variant_layout(db, variant, layout_key)
    if row is not None:
        return row.layout_json
    return _read_json_layout(db, layout_key)


def _upsert_layout_field(
    db: Session,
    layout_key: str,
    value: Any,
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


def get_dashboard_chart_order(
    db: Session,
    *,
    variant: Optional[str] = None,
) -> Dict[str, Any]:
    if variant is not None:
        result: Dict[str, Any] = {}
        for field_name, layout_key, _defaults in _FIELD_LAYOUT_PAIRS:
            row = variant_layout_crud.get_variant_layout(db, variant, layout_key)
            if row is not None:
                result[field_name] = row.layout_json
            else:
                # Fall back to shared/legacy so existing installs keep working.
                legacy_row = _get_legacy_row(db)
                result[field_name] = _read_field(db, field_name, layout_key, legacy_row)
        for field_name, layout_key in _SPAN_LAYOUT_KEYS:
            raw = _read_variant_or_shared_json(db, variant, layout_key)
            result[field_name] = normalize_span_map(raw) if isinstance(raw, dict) else None
        for field_name, layout_key in _BLOB_LAYOUT_KEYS:
            raw = _read_variant_or_shared_json(db, variant, layout_key)
            result[field_name] = (
                normalize_dashboard_order_blob(raw) if isinstance(raw, dict) else None
            )
        return result

    legacy_row = _get_legacy_row(db)
    result = {
        field_name: _read_field(db, field_name, layout_key, legacy_row)
        for field_name, layout_key, _defaults in _FIELD_LAYOUT_PAIRS
    }
    for field_name, layout_key in _SPAN_LAYOUT_KEYS:
        raw = _read_json_layout(db, layout_key)
        result[field_name] = normalize_span_map(raw) if isinstance(raw, dict) else None
    for field_name, layout_key in _BLOB_LAYOUT_KEYS:
        raw = _read_json_layout(db, layout_key)
        result[field_name] = (
            normalize_dashboard_order_blob(raw) if isinstance(raw, dict) else None
        )
    return result


def upsert_dashboard_chart_order(
    db: Session,
    *,
    variant: Optional[str] = None,
    energy_slot_order: Optional[List[str]] = None,
    space_charts_tab_order: Optional[List[str]] = None,
    space_main_tab_order: Optional[List[str]] = None,
    energy_slot_span: Optional[Dict[str, Any]] = None,
    space_charts_tab_span: Optional[Dict[str, Any]] = None,
    space_main_tab_span: Optional[Dict[str, Any]] = None,
    advanced_dashboard_order: Optional[Dict[str, Any]] = None,
    customized_dashboard_order: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> Dict[str, Any]:
    updates = {
        "energy_slot_order": energy_slot_order,
        "space_charts_tab_order": space_charts_tab_order,
        "space_main_tab_order": space_main_tab_order,
        "energy_slot_span": energy_slot_span,
        "space_charts_tab_span": space_charts_tab_span,
        "space_main_tab_span": space_main_tab_span,
        "advanced_dashboard_order": advanced_dashboard_order,
        "customized_dashboard_order": customized_dashboard_order,
    }
    if all(value is None for value in updates.values()):
        return get_dashboard_chart_order(db, variant=variant)

    if variant is not None:
        for field_name, layout_key, defaults in _FIELD_LAYOUT_PAIRS:
            raw = updates[field_name]
            if raw is None:
                continue
            normalized = normalize_slot_order(raw, defaults)
            variant_layout_crud.upsert_variant_layout(
                db,
                variant,
                layout_key,
                normalized,
                layout_version=1,
                updated_by=updated_by,
            )

        for field_name, layout_key in _SPAN_LAYOUT_KEYS:
            raw = updates[field_name]
            if raw is None:
                continue
            variant_layout_crud.upsert_variant_layout(
                db,
                variant,
                layout_key,
                normalize_span_map(raw),
                layout_version=1,
                updated_by=updated_by,
            )
            # Keep shared copy so older clients without variant still see spans.
            _upsert_layout_field(
                db, layout_key, normalize_span_map(raw), updated_by=updated_by
            )

        for field_name, layout_key in _BLOB_LAYOUT_KEYS:
            raw = updates[field_name]
            if raw is None:
                continue
            existing = _read_variant_or_shared_json(db, variant, layout_key)
            base = normalize_dashboard_order_blob(
                existing if isinstance(existing, dict) else {}
            )
            incoming = normalize_dashboard_order_blob(raw)
            merged = {**base, **incoming}
            variant_layout_crud.upsert_variant_layout(
                db,
                variant,
                layout_key,
                merged,
                layout_version=1,
                updated_by=updated_by,
            )
            _upsert_layout_field(db, layout_key, merged, updated_by=updated_by)

        return get_dashboard_chart_order(db, variant=variant)

    for field_name, layout_key, defaults in _FIELD_LAYOUT_PAIRS:
        raw = updates[field_name]
        if raw is None:
            continue
        normalized = normalize_slot_order(raw, defaults)
        _upsert_layout_field(db, layout_key, normalized, updated_by=updated_by)

    for field_name, layout_key in _SPAN_LAYOUT_KEYS:
        raw = updates[field_name]
        if raw is None:
            continue
        _upsert_layout_field(
            db, layout_key, normalize_span_map(raw), updated_by=updated_by
        )

    for field_name, layout_key in _BLOB_LAYOUT_KEYS:
        raw = updates[field_name]
        if raw is None:
            continue
        existing = _read_json_layout(db, layout_key)
        base = normalize_dashboard_order_blob(existing if isinstance(existing, dict) else {})
        incoming = normalize_dashboard_order_blob(raw)
        merged = {**base, **incoming}
        _upsert_layout_field(db, layout_key, merged, updated_by=updated_by)

    db.commit()
    return get_dashboard_chart_order(db, variant=variant)
