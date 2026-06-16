"""Adapter: widget_configuration as source of truth with widget_titles fallback."""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.crud import widget_configuration as widget_config_crud
from app.crud.widget_title_defaults import DROPDOWN_DEFAULTS, KNOWN_WIDGET_KEYS, TITLE_DEFAULTS
from app.models.widget_configuration import WidgetConfiguration
from app.models.widget_title import WidgetTitle

DEFAULT_WIDGET_CONFIG = widget_config_crud.DEFAULT_WIDGET_CONFIG


def _legacy_row(db: Session, widget_key: str) -> Optional[WidgetTitle]:
    return (
        db.query(WidgetTitle)
        .filter(WidgetTitle.widget_key == widget_key)
        .one_or_none()
    )


def get_widget_title_fields(
    db: Session, widget_key: str
) -> Optional[Dict[str, str]]:
    """Return display_name + dropdown_name; configuration wins over legacy."""
    config = widget_config_crud.get_widget_configuration_by_key(db, widget_key)
    if config is not None:
        return {
            "display_name": config.display_name,
            "dropdown_name": config.dropdown_name or "",
        }
    legacy = _legacy_row(db, widget_key)
    if legacy is not None:
        return {
            "display_name": legacy.display_name,
            "dropdown_name": legacy.dropdown_name or "",
        }
    return None


def get_display_name_for_widget(db: Session, widget_key: str) -> Optional[str]:
    fields = get_widget_title_fields(db, widget_key)
    if fields and fields.get("display_name"):
        return fields["display_name"]
    default = TITLE_DEFAULTS.get(widget_key)
    return default if default else None


def get_merged_known_widget_titles(db: Session) -> Dict[str, Dict[str, str]]:
    """Read-only merge for GET /widgets/widget_titles (no DB writes)."""
    merged: Dict[str, Dict[str, str]] = {}
    for key in KNOWN_WIDGET_KEYS:
        fields = get_widget_title_fields(db, key)
        if fields:
            merged[key] = fields
        else:
            merged[key] = {
                "display_name": TITLE_DEFAULTS[key],
                "dropdown_name": DROPDOWN_DEFAULTS.get(key, ""),
            }
    return merged


def build_widget_titles_response_items(db: Session) -> list:
    merged = get_merged_known_widget_titles(db)
    return [
        {
            "key": key,
            "title": merged[key]["display_name"] or TITLE_DEFAULTS[key],
            "dropdown_name": merged[key]["dropdown_name"] or DROPDOWN_DEFAULTS.get(key, ""),
        }
        for key in KNOWN_WIDGET_KEYS
    ]


def rename_widget_via_configuration(
    db: Session,
    widget_key: str,
    display_name: str,
    updated_by: Optional[int],
) -> WidgetConfiguration:
    return widget_config_crud.upsert_widget_configuration_by_key(
        db,
        widget_key,
        display_name=display_name,
        updated_by=updated_by,
    )


def _upsert_configuration_default(
    db: Session,
    widget_key: str,
    display_name: Optional[str],
    dropdown_name: Optional[str],
) -> None:
    existing = widget_config_crud.get_widget_configuration_by_key(db, widget_key)
    if existing is None:
        row = WidgetConfiguration(
            widget_key=widget_key,
            display_name=display_name or widget_key,
            dropdown_name=dropdown_name,
            is_visible=True,
            config=deepcopy(DEFAULT_WIDGET_CONFIG),
            updated_by=None,
        )
        db.add(row)
        return

    if display_name and not (existing.display_name and existing.display_name.strip()):
        existing.display_name = display_name
    if dropdown_name and not (existing.dropdown_name and existing.dropdown_name.strip()):
        existing.dropdown_name = dropdown_name


def seed_widget_configuration_defaults(db: Session) -> int:
    """
    Ensure known widget keys exist in widget_configuration (only_if_absent names).
    Does not overwrite user renames. Returns number of new rows created.
    """
    seeded = 0
    for key in KNOWN_WIDGET_KEYS:
        if widget_config_crud.get_widget_configuration_by_key(db, key) is not None:
            continue
        _upsert_configuration_default(
            db,
            key,
            TITLE_DEFAULTS.get(key),
            DROPDOWN_DEFAULTS.get(key),
        )
        seeded += 1
    db.commit()
    return seeded


def get_widget_titles_map(db: Session) -> Dict[str, str]:
    """Merge defaults with configuration/legacy overrides (for append_widget_title)."""
    titles = TITLE_DEFAULTS.copy()
    for key in list(titles.keys()):
        name = get_display_name_for_widget(db, key)
        if name:
            titles[key] = name
    for legacy in db.query(WidgetTitle).all():
        if legacy.widget_key not in titles and legacy.display_name:
            titles[legacy.widget_key] = legacy.display_name
    for row in widget_config_crud.list_widget_configurations(db):
        if row.display_name:
            titles[row.widget_key] = row.display_name
    return titles
