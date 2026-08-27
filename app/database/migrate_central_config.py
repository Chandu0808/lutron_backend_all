"""Idempotent central configuration tables (Phase 3)."""

from __future__ import annotations

from typing import List

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.models.dashboard_layout import DashboardLayout
from app.models.installation_settings import InstallationSettings
from app.models.widget_configuration import WidgetConfiguration

_CENTRAL_CONFIG_MODELS = (
    InstallationSettings,
    WidgetConfiguration,
    DashboardLayout,
)


def _table_exists(engine: Engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def ensure_central_config_tables(engine: Engine) -> None:
    """Create central config tables if missing, then seed installation defaults."""
    for model in _CENTRAL_CONFIG_MODELS:
        model.__table__.create(engine, checkfirst=True)
    inserted = seed_installation_settings_on_create()
    if inserted:
        print(
            "[DB] installation_settings defaults seeded: "
            + ", ".join(inserted)
        )


def seed_installation_settings_on_create() -> List[str]:
    from app.crud.installation_settings import seed_installation_runtime_defaults

    return seed_installation_runtime_defaults()


def central_config_tables_present(engine: Engine) -> bool:
    return all(_table_exists(engine, model.__tablename__) for model in _CENTRAL_CONFIG_MODELS)
