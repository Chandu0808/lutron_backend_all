from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.crud.variant_config_defaults import iter_seed_theme_rows, normalize_variant_slug
from app.models.variant_theme_setting import VariantThemeSetting


def list_variant_theme_settings(db: Session, variant: str) -> List[VariantThemeSetting]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantThemeSetting)
        .filter(VariantThemeSetting.variant == slug)
        .order_by(VariantThemeSetting.config_group, VariantThemeSetting.config_key)
        .all()
    )


def get_variant_theme_setting(
    db: Session,
    variant: str,
    config_group: str,
    config_key: str,
) -> Optional[VariantThemeSetting]:
    slug = normalize_variant_slug(variant)
    return (
        db.query(VariantThemeSetting)
        .filter(
            VariantThemeSetting.variant == slug,
            VariantThemeSetting.config_group == config_group,
            VariantThemeSetting.config_key == config_key,
        )
        .first()
    )


def get_variant_theme_settings_map(db: Session, variant: str) -> Dict[str, Dict[str, Any]]:
    rows = list_variant_theme_settings(db, variant)
    data: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        data.setdefault(row.config_group, {})[row.config_key] = deepcopy(row.config_value)
    return data


def upsert_variant_theme_setting(
    db: Session,
    variant: str,
    config_group: str,
    config_key: str,
    config_value: Any,
    *,
    updated_by: Optional[int] = None,
) -> VariantThemeSetting:
    row = get_variant_theme_setting(db, variant, config_group, config_key)
    slug = normalize_variant_slug(variant)
    if row is None:
        row = VariantThemeSetting(
            variant=slug,
            config_group=config_group,
            config_key=config_key,
            config_value=deepcopy(config_value),
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.config_value = deepcopy(config_value)
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def seed_variant_theme_settings(db: Session, variant: str) -> int:
    slug = normalize_variant_slug(variant)
    existing = (
        db.query(VariantThemeSetting.id)
        .filter(VariantThemeSetting.variant == slug)
        .first()
    )
    if existing is not None:
        return 0

    count = 0
    for row in iter_seed_theme_rows(slug):
        db.add(VariantThemeSetting(**row))
        count += 1
    db.commit()
    return count
