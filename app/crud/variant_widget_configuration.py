from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.crud.variant_config_defaults import iter_seed_widget_rows, normalize_variant_slug
from app.models.variant_widget_configuration import VariantWidgetConfiguration


def list_variant_widget_configurations(
    db: Session,
    variant: str,
) -> List[VariantWidgetConfiguration]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantWidgetConfiguration)
        .filter(VariantWidgetConfiguration.variant == slug)
        .order_by(
            VariantWidgetConfiguration.sort_order,
            VariantWidgetConfiguration.widget_key,
        )
        .all()
    )


def get_variant_widget_configuration_by_key(
    db: Session,
    variant: str,
    widget_key: str,
) -> Optional[VariantWidgetConfiguration]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantWidgetConfiguration)
        .filter(
            VariantWidgetConfiguration.variant == slug,
            VariantWidgetConfiguration.widget_key == widget_key,
        )
        .first()
    )


def create_variant_widget_configuration(
    db: Session,
    *,
    variant: str,
    widget_key: str,
    display_name: str,
    dropdown_name: Optional[str] = None,
    is_available: bool = True,
    default_visible: bool = False,
    is_visible: bool = False,
    sort_order: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> VariantWidgetConfiguration:
    row = VariantWidgetConfiguration(
        variant=normalize_variant_slug(variant),
        widget_key=widget_key,
        display_name=display_name,
        dropdown_name=dropdown_name,
        is_available=is_available,
        default_visible=default_visible,
        is_visible=is_visible,
        sort_order=sort_order,
        config=deepcopy(config) if config is not None else {"seeded": True},
        updated_by=updated_by,
    )
    db.add(row)
    db.flush()
    return row


def _is_user_modified(row: VariantWidgetConfiguration) -> bool:
    """True when a Superadmin (or other editor) changed preferences."""
    if row.updated_by is not None:
        return True
    cfg = row.config if isinstance(row.config, dict) else {}
    if cfg.get("user_modified") is True:
        return True
    return False


def _mark_user_modified_config(
    existing: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    next_cfg = deepcopy(existing) if isinstance(existing, dict) else {}
    next_cfg["seeded"] = False
    next_cfg["user_modified"] = True
    return next_cfg


def upsert_variant_widget_configuration_by_key(
    db: Session,
    variant: str,
    widget_key: str,
    *,
    display_name: Optional[str] = None,
    dropdown_name: Optional[str] = None,
    is_available: Optional[bool] = None,
    default_visible: Optional[bool] = None,
    is_visible: Optional[bool] = None,
    sort_order: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> VariantWidgetConfiguration:
    row = get_variant_widget_configuration_by_key(db, variant, widget_key)
    if row is None:
        if display_name is None:
            raise ValueError("display_name is required when creating a widget configuration")
        seeded_cfg = (
            _mark_user_modified_config(config)
            if updated_by is not None or is_visible is not None
            else (deepcopy(config) if config is not None else {"seeded": True})
        )
        row = create_variant_widget_configuration(
            db,
            variant=variant,
            widget_key=widget_key,
            display_name=display_name,
            dropdown_name=dropdown_name,
            is_available=True if is_available is None else is_available,
            default_visible=False if default_visible is None else default_visible,
            is_visible=False if is_visible is None else is_visible,
            sort_order=sort_order,
            config=seeded_cfg,
            updated_by=updated_by,
        )
    else:
        if display_name is not None:
            row.display_name = display_name
        if dropdown_name is not None:
            row.dropdown_name = dropdown_name
        if is_available is not None:
            row.is_available = is_available
        if default_visible is not None:
            row.default_visible = default_visible
        if is_visible is not None:
            row.is_visible = is_visible
        if sort_order is not None:
            row.sort_order = sort_order
        if config is not None:
            row.config = deepcopy(config)
        elif is_visible is not None or updated_by is not None:
            row.config = _mark_user_modified_config(
                row.config if isinstance(row.config, dict) else None
            )
        if updated_by is not None:
            row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def seed_variant_widget_configurations(db: Session, variant: str) -> int:
    """
    Ensure all seed widget keys exist for the variant.
    - Inserts missing keys from product defaults.
    - Refreshes default_visible from seed.
    - Realigns is_visible to seed only when the row was never user-modified.
    Returns number of inserted rows.
    """
    slug = normalize_variant_slug(variant)
    existing_rows = list_variant_widget_configurations(db, slug)
    existing = {row.widget_key: row for row in existing_rows}

    inserted = 0
    changed = False
    for seed in iter_seed_widget_rows(slug):
        key = seed["widget_key"]
        row = existing.get(key)
        if row is None:
            create_variant_widget_configuration(db, **seed)
            inserted += 1
            changed = True
            continue
        if row.default_visible != seed["default_visible"]:
            row.default_visible = seed["default_visible"]
            changed = True
        if not _is_user_modified(row):
            if row.is_visible != seed["is_visible"]:
                row.is_visible = seed["is_visible"]
                changed = True
            cfg = row.config if isinstance(row.config, dict) else {}
            if cfg.get("seeded") is not True or cfg.get("user_modified") is True:
                row.config = {"seeded": True}
                changed = True
        if seed.get("sort_order") is not None and row.sort_order != seed["sort_order"]:
            # Only bump sort_order for non-user-modified rows to avoid fighting custom order.
            if not _is_user_modified(row):
                row.sort_order = seed["sort_order"]
                changed = True
    if changed:
        db.commit()
    return inserted
