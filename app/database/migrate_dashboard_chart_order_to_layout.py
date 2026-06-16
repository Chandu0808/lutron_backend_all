"""One-time backfill: dashboard_chart_order singleton row -> dashboard_layout rows."""

from __future__ import annotations

from typing import Dict

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.crud import dashboard_layout as layout_crud
from app.models.dashboard_chart_order import (
    SINGLETON_ROW_ID,
    DashboardChartOrder,
)

_LEGACY_TO_LAYOUT_KEY: Dict[str, str] = {
    "energy_slot_order": layout_crud.LAYOUT_KEY_ENERGY_SLOT_ORDER,
    "space_charts_tab_order": layout_crud.LAYOUT_KEY_SPACE_CHARTS_TAB_ORDER,
    "space_main_tab_order": layout_crud.LAYOUT_KEY_SPACE_MAIN_TAB_ORDER,
}


def _table_exists(engine: Engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def migrate_dashboard_chart_order_to_layout(
    db: Session,
) -> Dict[str, int]:
    """
    Copy non-null legacy dashboard_chart_order columns into dashboard_layout.

    Idempotent: skips layout keys that already have a row.
    Returns counts: migrated, skipped_existing, skipped_null.
    """
    migrated = 0
    skipped_existing = 0
    skipped_null = 0

    legacy_row = (
        db.query(DashboardChartOrder)
        .filter(DashboardChartOrder.id == SINGLETON_ROW_ID)
        .first()
    )
    if legacy_row is None:
        return {
            "migrated": migrated,
            "skipped_existing": skipped_existing,
            "skipped_null": skipped_null,
        }

    for field_name, layout_key in _LEGACY_TO_LAYOUT_KEY.items():
        value = getattr(legacy_row, field_name)
        if value is None:
            skipped_null += 1
            continue
        if layout_crud.get_layout(db, layout_key) is not None:
            skipped_existing += 1
            continue
        layout_crud.upsert_layout(db, layout_key, value, updated_by=None)
        migrated += 1

    return {
        "migrated": migrated,
        "skipped_existing": skipped_existing,
        "skipped_null": skipped_null,
    }


def run_dashboard_chart_order_to_layout_migration(engine: Engine) -> Dict[str, int]:
    """Run migration if both source and target tables exist."""
    if not _table_exists(engine, "dashboard_chart_order"):
        return {"migrated": 0, "skipped_existing": 0, "skipped_null": 0}
    if not _table_exists(engine, "dashboard_layout"):
        return {"migrated": 0, "skipped_existing": 0, "skipped_null": 0}

    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        return migrate_dashboard_chart_order_to_layout(db)
    finally:
        db.close()
