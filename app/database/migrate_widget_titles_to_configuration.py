"""One-time backfill: widget_titles rows -> widget_configuration."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.crud import widget_configuration as widget_config_crud
from app.crud.widget_title_adapter import seed_widget_configuration_defaults
from app.models.widget_configuration import WidgetConfiguration
from app.models.widget_title import WidgetTitle

DEFAULT_WIDGET_CONFIG = widget_config_crud.DEFAULT_WIDGET_CONFIG


def _table_exists(engine: Engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def migrate_widget_titles_to_configuration(db: Session) -> Dict[str, int]:
    """
    Copy widget_titles rows into widget_configuration by widget_key.

    Idempotent: skips keys that already exist in widget_configuration.
    Preserves display_name, dropdown_name, updated_by, updated_at.
    """
    migrated = 0
    skipped_existing = 0

    legacy_rows = db.query(WidgetTitle).all()
    if not legacy_rows:
        return {"migrated": 0, "skipped_existing": 0}

    for legacy in legacy_rows:
        if widget_config_crud.get_widget_configuration_by_key(db, legacy.widget_key) is not None:
            skipped_existing += 1
            continue
        row = WidgetConfiguration(
            widget_key=legacy.widget_key,
            display_name=legacy.display_name,
            dropdown_name=legacy.dropdown_name,
            is_visible=True,
            config=deepcopy(DEFAULT_WIDGET_CONFIG),
            updated_by=legacy.updated_by,
        )
        if legacy.updated_at is not None:
            row.updated_at = legacy.updated_at
            row.created_at = legacy.updated_at
        db.add(row)
        migrated += 1

    db.commit()
    return {"migrated": migrated, "skipped_existing": skipped_existing}


def run_widget_titles_to_configuration_migration(engine: Engine) -> Dict[str, int]:
    if not _table_exists(engine, "widget_titles"):
        return {"migrated": 0, "skipped_existing": 0}
    if not _table_exists(engine, "widget_configuration"):
        return {"migrated": 0, "skipped_existing": 0}

    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        return migrate_widget_titles_to_configuration(db)
    finally:
        db.close()


def ensure_widget_title_configuration(engine: Engine) -> Dict[str, int]:
    """Migrate legacy rows then seed known defaults (startup helper)."""
    counts = run_widget_titles_to_configuration_migration(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        counts["seeded"] = seed_widget_configuration_defaults(db)
    finally:
        db.close()
    return counts
