"""Idempotent variant-scoped configuration tables and default seeds."""

from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.crud.variant_config_defaults import get_all_default_variants
from app.crud.variant_dashboard_layout import seed_variant_layouts
from app.crud.variant_theme_setting import seed_variant_theme_settings, upsert_variant_theme_setting
from app.crud.variant_widget_configuration import (
    get_variant_widget_configuration_by_key,
    seed_variant_widget_configurations,
    upsert_variant_widget_configuration_by_key,
)
from app.database.session import SessionLocal
from app.models.variant_dashboard_layout import VariantDashboardLayout
from app.models.variant_theme_setting import VariantThemeSetting
from app.models.variant_widget_configuration import VariantWidgetConfiguration

_VARIANT_CONFIG_MODELS = (
    VariantWidgetConfiguration,
    VariantDashboardLayout,
    VariantThemeSetting,
)

# Old Theme.key → (config_group, config_key) for basic-variant migration.
_LEGACY_THEME_KEY_MAP = (
    ("background_image", "background", "image"),
    ("ui.background", "application", "background"),
    ("ui.content", "application", "content"),
    ("ui.button", "application", "button"),
    ("heatmap.light", "heatmap", "light"),
    ("heatmap.occupancy", "heatmap", "occupancy"),
    ("heatmap.energy", "heatmap", "energy"),
)


def _table_exists(engine: Engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def ensure_variant_config_tables(engine: Engine) -> None:
    for model in _VARIANT_CONFIG_MODELS:
        model.__table__.create(engine, checkfirst=True)


def variant_config_tables_present(engine: Engine) -> bool:
    return all(_table_exists(engine, model.__tablename__) for model in _VARIANT_CONFIG_MODELS)


def _sync_legacy_theme_into_basic(db) -> None:
    """Copy existing Theme table values into the basic variant (preserves installs)."""
    try:
        from app.models.theme_model import Theme
    except Exception:
        return

    try:
        rows = {r.key: r.value for r in db.query(Theme).all()}
    except Exception:
        return

    if not rows:
        return

    for old_key, group, key in _LEGACY_THEME_KEY_MAP:
        value = rows.get(old_key)
        if value is None or value == "":
            continue
        upsert_variant_theme_setting(db, "basic", group, key, value)


def _sync_shared_widget_names_into_variants(db) -> None:
    """Push shared widget_configuration display/dropdown names into all variants."""
    try:
        from app.crud import widget_configuration as widget_crud
    except Exception:
        return

    try:
        shared_rows = widget_crud.list_widget_configurations(db)
    except Exception:
        return

    for shared in shared_rows:
        if not shared.display_name:
            continue
        for variant in get_all_default_variants():
            existing = get_variant_widget_configuration_by_key(
                db, variant, shared.widget_key
            )
            if existing is None:
                continue
            # Only overwrite when shared name differs (user rename on shared table).
            if (
                existing.display_name == shared.display_name
                and (existing.dropdown_name or "")
                == (shared.dropdown_name or shared.display_name)
            ):
                continue
            upsert_variant_widget_configuration_by_key(
                db,
                variant,
                shared.widget_key,
                display_name=shared.display_name,
                dropdown_name=shared.dropdown_name or shared.display_name,
            )


def seed_variant_config_defaults() -> None:
    db = SessionLocal()
    try:
        for variant in get_all_default_variants():
            seed_variant_widget_configurations(db, variant)
            seed_variant_layouts(db, variant)
            seed_variant_theme_settings(db, variant)
        _sync_legacy_theme_into_basic(db)
        _sync_shared_widget_names_into_variants(db)
    finally:
        db.close()
