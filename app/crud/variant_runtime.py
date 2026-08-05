from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.crud.installation_settings import SETTING_KEY_UI_VARIANT, get_setting
from app.crud.variant_config_defaults import normalize_variant_slug


def get_active_variant(db: Session) -> str:
    row = get_setting(db, SETTING_KEY_UI_VARIANT)
    value = row.setting_value if row is not None else None
    return normalize_variant_slug(value)


def resolve_variant(db: Session, requested_variant: Optional[str]) -> str:
    if requested_variant is not None and str(requested_variant).strip():
        return normalize_variant_slug(requested_variant)
    return get_active_variant(db)
