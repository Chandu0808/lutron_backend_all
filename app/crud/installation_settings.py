from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.installation_settings import InstallationSettings

# Reserved keys — not wired to runtime until later phases.
SETTING_KEY_FEATURE_FLAGS = "feature_flags"
SETTING_KEY_TIMEZONE = "timezone"
SETTING_KEY_UI_PRESET = "ui_preset"
SETTING_KEY_UI_VARIANT = "ui_variant"

DEFAULT_SETTING_VALUES: Dict[str, Any] = {
    SETTING_KEY_FEATURE_FLAGS: {},
    SETTING_KEY_TIMEZONE: "UTC",
    SETTING_KEY_UI_PRESET: None,
    SETTING_KEY_UI_VARIANT: "basic",
}


def get_setting(db: Session, setting_key: str) -> Optional[InstallationSettings]:
    return (
        db.query(InstallationSettings)
        .filter(InstallationSettings.setting_key == setting_key)
        .first()
    )


def list_settings(db: Session) -> List[InstallationSettings]:
    return db.query(InstallationSettings).order_by(InstallationSettings.setting_key).all()


def upsert_setting(
    db: Session,
    setting_key: str,
    setting_value: Any,
    updated_by: Optional[int] = None,
) -> InstallationSettings:
    row = get_setting(db, setting_key)
    if row is None:
        row = InstallationSettings(
            setting_key=setting_key,
            setting_value=setting_value,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.setting_value = setting_value
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def reset_setting(
    db: Session,
    setting_key: str,
    default_value: Optional[Any] = None,
    updated_by: Optional[int] = None,
) -> InstallationSettings:
    value = (
        deepcopy(default_value)
        if default_value is not None
        else deepcopy(DEFAULT_SETTING_VALUES.get(setting_key, None))
    )
    return upsert_setting(db, setting_key, value, updated_by=updated_by)


def reset_all_settings(
    db: Session,
    defaults: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> List[InstallationSettings]:
    source = defaults if defaults is not None else DEFAULT_SETTING_VALUES
    rows: List[InstallationSettings] = []
    for key, value in source.items():
        rows.append(reset_setting(db, key, default_value=value, updated_by=updated_by))
    return rows


def validate_setting_key(setting_key: str) -> str:
    key = setting_key.strip()
    if not key:
        raise ValueError("setting_key must not be empty")
    if len(key) > 64:
        raise ValueError("setting_key must be at most 64 characters")
    return key


def get_settings_map(db: Session) -> Dict[str, Any]:
    return {row.setting_key: row.setting_value for row in list_settings(db)}


def merge_settings(
    db: Session,
    updates: Dict[str, Any],
    updated_by: Optional[int] = None,
) -> Dict[str, Any]:
    for key, value in updates.items():
        upsert_setting(db, validate_setting_key(key), value, updated_by=updated_by)
    return get_settings_map(db)
