"""
Installation-scoped runtime settings (installation_settings).

Env stays minimal:
  - DATABASE_HOST_URL (bootstrap)
  - Optional emergency overrides:
      MONITORING_ENABLED=false
      MONITORING_INGEST_TOKEN / MONITORING_INGEST_URL (if set, override DB)

Precedence for settings:
  1. installation_settings row when present
  2. optional env fallback (backward compatible / emergency)
  3. DEFAULT_SETTING_VALUES
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("lutron.installation_config")

_CACHE_TTL_SECONDS = 2.0
_cache_lock = threading.Lock()
# key -> (monotonic_expiry, raw_or_None_if_absent, db_ok)
_bool_cache: Dict[str, Tuple[float, Optional[bool], bool]] = {}
_str_cache: Dict[str, Tuple[float, Optional[str], bool]] = {}


def _env_truthy(name: str) -> Optional[bool]:
    """Return True/False if env is set to a known bool; None if unset/empty."""
    raw = os.getenv(name)
    if raw is None:
        return None
    text = raw.strip().lower()
    if text == "":
        return None
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return None


def _env_string(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    text = raw.strip()
    return text if text else None


def coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    return None


def coerce_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    text = str(value).strip()
    return text if text else None


def invalidate_installation_bool_cache(keys: Optional[Sequence[str]] = None) -> None:
    with _cache_lock:
        if keys is None:
            _bool_cache.clear()
            return
        for key in keys:
            _bool_cache.pop(key, None)


def invalidate_installation_string_cache(keys: Optional[Sequence[str]] = None) -> None:
    with _cache_lock:
        if keys is None:
            _str_cache.clear()
            return
        for key in keys:
            _str_cache.pop(key, None)


def invalidate_installation_settings_cache(
    keys: Optional[Sequence[str]] = None,
) -> None:
    invalidate_installation_bool_cache(keys)
    invalidate_installation_string_cache(keys)


def _read_bool_from_db(setting_key: str) -> Tuple[Optional[bool], bool]:
    try:
        from app.crud.installation_settings import get_setting
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            row = get_setting(db, setting_key)
            if row is None:
                return None, True
            coerced = coerce_bool(row.setting_value)
            if coerced is None:
                logger.warning(
                    "[installation_config] invalid bool for %s value=%r; treating absent",
                    setting_key,
                    row.setting_value,
                )
                return None, True
            return coerced, True
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "[installation_config] failed to read %s: %s", setting_key, exc
        )
        return None, False


def _read_string_from_db(setting_key: str) -> Tuple[Optional[str], bool]:
    try:
        from app.crud.installation_settings import get_setting
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            row = get_setting(db, setting_key)
            if row is None:
                return None, True
            return coerce_string(row.setting_value), True
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "[installation_config] failed to read %s: %s", setting_key, exc
        )
        return None, False


def get_bool_setting_raw(
    setting_key: str,
    *,
    use_cache: bool = True,
) -> Tuple[Optional[bool], bool]:
    """Return (raw_or_None_if_absent, db_ok) with short TTL cache."""
    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            cached = _bool_cache.get(setting_key)
            if cached is not None and cached[0] > now:
                return cached[1], cached[2]
    raw, db_ok = _read_bool_from_db(setting_key)
    with _cache_lock:
        _bool_cache[setting_key] = (now + _CACHE_TTL_SECONDS, raw, db_ok)
    return raw, db_ok


def get_bool_setting(
    setting_key: str,
    *,
    default: bool,
    env_fallbacks: Sequence[str] = (),
    use_cache: bool = True,
) -> bool:
    """
    Resolve a bool setting.

    When an env fallback is explicitly set, it wins (ops/test override).
    Else DB value when present, else default.
    """
    for env_name in env_fallbacks:
        env_val = _env_truthy(env_name)
        if env_val is not None:
            return bool(env_val)

    raw, db_ok = get_bool_setting_raw(setting_key, use_cache=use_cache)
    if db_ok and raw is not None:
        return bool(raw)

    return bool(default)


def get_string_setting(
    setting_key: str,
    *,
    default: str = "",
    env_fallbacks: Sequence[str] = (),
    use_cache: bool = True,
) -> str:
    """
    Resolve a string setting.

    Env fallbacks win when set (deploy override / tests), else DB, else default.
    """
    for env_name in env_fallbacks:
        env_val = _env_string(env_name)
        if env_val is not None:
            return env_val

    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            cached = _str_cache.get(setting_key)
            if cached is not None and cached[0] > now:
                raw, db_ok = cached[1], cached[2]
            else:
                cached = None
        if cached is None:
            raw, db_ok = _read_string_from_db(setting_key)
            with _cache_lock:
                _str_cache[setting_key] = (now + _CACHE_TTL_SECONDS, raw, db_ok)
    else:
        raw, db_ok = _read_string_from_db(setting_key)
        with _cache_lock:
            _str_cache[setting_key] = (now + _CACHE_TTL_SECONDS, raw, db_ok)

    if db_ok and raw is not None:
        return raw
    return str(default or "")


def set_bool_setting(
    setting_key: str,
    enabled: bool,
    *,
    updated_by: Optional[int] = None,
) -> bool:
    from app.crud.installation_settings import upsert_setting
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        upsert_setting(
            db,
            setting_key,
            bool(enabled),
            updated_by=updated_by,
        )
    finally:
        db.close()
    invalidate_installation_bool_cache([setting_key])
    logger.info(
        "[installation_config] persisted %s=%s updated_by=%s",
        setting_key,
        bool(enabled),
        updated_by,
    )
    return bool(enabled)


def set_string_setting(
    setting_key: str,
    value: str,
    *,
    updated_by: Optional[int] = None,
) -> str:
    from app.crud.installation_settings import upsert_setting
    from app.database.session import SessionLocal

    text = str(value or "").strip()
    db = SessionLocal()
    try:
        upsert_setting(db, setting_key, text, updated_by=updated_by)
    finally:
        db.close()
    invalidate_installation_string_cache([setting_key])
    logger.info(
        "[installation_config] persisted %s updated_by=%s (len=%s)",
        setting_key,
        updated_by,
        len(text),
    )
    return text


def ensure_default_bool_settings(
    keys_and_defaults: Dict[str, bool],
    *,
    updated_by: Optional[int] = None,
) -> List[str]:
    from app.crud.installation_settings import get_setting, upsert_setting
    from app.database.session import SessionLocal

    inserted: List[str] = []
    db = SessionLocal()
    try:
        for key, default in keys_and_defaults.items():
            if get_setting(db, key) is not None:
                continue
            upsert_setting(db, key, bool(default), updated_by=updated_by)
            inserted.append(key)
    finally:
        db.close()
    if inserted:
        invalidate_installation_bool_cache(inserted)
        logger.info(
            "[installation_config] seeded bool defaults for keys=%s", inserted
        )
    return inserted


def ensure_default_string_settings(
    keys_and_defaults: Dict[str, str],
    *,
    updated_by: Optional[int] = None,
    prefer_env: Optional[Dict[str, Sequence[str]]] = None,
) -> List[str]:
    """
    Insert missing string keys only.
    If prefer_env maps key -> env names, use first set env value as seed.
    """
    from app.crud.installation_settings import get_setting, upsert_setting
    from app.database.session import SessionLocal

    prefer_env = prefer_env or {}
    inserted: List[str] = []
    db = SessionLocal()
    try:
        for key, default in keys_and_defaults.items():
            if get_setting(db, key) is not None:
                continue
            value = default
            for env_name in prefer_env.get(key, ()):
                env_val = _env_string(env_name)
                if env_val is not None:
                    value = env_val
                    break
            upsert_setting(db, key, str(value), updated_by=updated_by)
            inserted.append(key)
    finally:
        db.close()
    if inserted:
        invalidate_installation_string_cache(inserted)
        logger.info(
            "[installation_config] seeded string defaults for keys=%s", inserted
        )
    return inserted


def is_energy_logger_manual() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_ENERGY_LOGGER_MANUAL,
    )

    return get_bool_setting(
        SETTING_KEY_ENERGY_LOGGER_MANUAL,
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_ENERGY_LOGGER_MANUAL, False)
        ),
        env_fallbacks=("energy_logger_manual", "energy_logger_mannual"),
    )


def get_monitoring_ingest_token() -> str:
    from app.crud.installation_settings import (
        DEFAULT_INGEST_TOKEN,
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_INGEST_TOKEN,
    )

    return get_string_setting(
        SETTING_KEY_MONITORING_INGEST_TOKEN,
        default=str(
            DEFAULT_SETTING_VALUES.get(
                SETTING_KEY_MONITORING_INGEST_TOKEN, DEFAULT_INGEST_TOKEN
            )
            or ""
        ),
        env_fallbacks=("MONITORING_INGEST_TOKEN",),
    )


def get_monitoring_ingest_url() -> str:
    from app.crud.installation_settings import (
        DEFAULT_INGEST_URL,
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_INGEST_URL,
    )

    return get_string_setting(
        SETTING_KEY_MONITORING_INGEST_URL,
        default=str(
            DEFAULT_SETTING_VALUES.get(
                SETTING_KEY_MONITORING_INGEST_URL, DEFAULT_INGEST_URL
            )
            or DEFAULT_INGEST_URL
        ),
        env_fallbacks=("MONITORING_INGEST_URL",),
    )
