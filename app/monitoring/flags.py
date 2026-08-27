"""
Monitoring enablement flags and effective-state resolution (Phase 7+).

Env (optional overrides only):
  - MONITORING_ENABLED=false → emergency OFF (if unset, DB controls)
  - MONITORING_INGEST_TOKEN / MONITORING_INGEST_URL → override DB when set

All primary config lives in installation_settings.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.installation_config import (
    get_bool_setting,
    invalidate_installation_settings_cache,
    set_bool_setting,
)

logger = logging.getLogger("lutron_monitoring.flags")


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("true", "1", "yes")


def is_monitoring_environment_enabled() -> bool:
    """
    Emergency / bootstrap gate.

    - MONITORING_ENABLED unset/empty → True (DB monitoring_enabled controls)
    - MONITORING_ENABLED=false → False (hard kill switch)
    - MONITORING_ENABLED=true → True
    """
    raw = os.getenv("MONITORING_ENABLED")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("true", "1", "yes")


def invalidate_monitoring_configured_cache() -> None:
    """Invalidate monitoring-related installation_settings cache."""
    from app.crud.installation_settings import (
        RUNTIME_BOOL_SETTING_DEFAULTS,
        RUNTIME_STRING_SETTING_DEFAULTS,
    )

    keys = list(RUNTIME_BOOL_SETTING_DEFAULTS.keys()) + list(
        RUNTIME_STRING_SETTING_DEFAULTS.keys()
    )
    invalidate_installation_settings_cache(keys)


def get_configured_monitoring_enabled(
    *,
    default_when_absent: bool = True,
    use_cache: bool = True,
) -> Tuple[Optional[bool], bool, bool]:
    """
    Read persisted monitoring_enabled.

    Returns:
      (configured_value_or_None_if_absent,
       resolved_bool_with_default,
       db_ok)
    """
    from app.crud.installation_settings import SETTING_KEY_MONITORING_ENABLED
    from app.installation_config import get_bool_setting_raw

    raw, db_ok = get_bool_setting_raw(
        SETTING_KEY_MONITORING_ENABLED, use_cache=use_cache
    )
    if not db_ok:
        return None, default_when_absent, False
    if raw is None:
        return None, default_when_absent, True
    return raw, raw, True


def is_monitoring_enabled() -> bool:
    """
    Effective master switch for monitoring *work*.

    Environment OFF always wins. When environment is ON, the installation
    setting controls enablement (absent → True for backward compatibility).
    """
    if not is_monitoring_environment_enabled():
        return False
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_ENABLED,
    )

    return get_bool_setting(
        SETTING_KEY_MONITORING_ENABLED,
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_ENABLED, True)
        ),
        env_fallbacks=(),
    )


def resolve_monitoring_status_fields() -> Dict[str, Any]:
    """
    Fields for GET/PATCH /monitoring/status (no fabricated OFF on DB failure).
    """
    env_enabled = is_monitoring_environment_enabled()
    configured_raw, configured_resolved, db_ok = get_configured_monitoring_enabled(
        default_when_absent=True, use_cache=True
    )

    if not env_enabled:
        return {
            "enabled": False,
            "monitoring_enabled": False,
            "source": "environment",
            "writable": False,
            "environment_enabled": False,
            "configured_enabled": configured_raw if db_ok else None,
            "configured_defaulted": bool(db_ok and configured_raw is None),
            "settings_available": db_ok,
        }

    if not db_ok:
        return {
            "enabled": True,
            "monitoring_enabled": True,
            "source": "environment",
            "writable": False,
            "environment_enabled": True,
            "configured_enabled": None,
            "configured_defaulted": False,
            "settings_available": False,
        }

    effective = bool(configured_resolved)
    return {
        "enabled": effective,
        "monitoring_enabled": effective,
        "source": "installation_settings",
        "writable": True,
        "environment_enabled": True,
        "configured_enabled": (
            configured_raw if configured_raw is not None else True
        ),
        "configured_defaulted": configured_raw is None,
        "settings_available": True,
    }


def set_configured_monitoring_enabled(
    enabled: bool,
    *,
    updated_by: Optional[int] = None,
) -> bool:
    """Persist monitoring_enabled and invalidate cache. Raises on DB errors."""
    from app.crud.installation_settings import SETTING_KEY_MONITORING_ENABLED

    set_bool_setting(
        SETTING_KEY_MONITORING_ENABLED,
        bool(enabled),
        updated_by=updated_by,
    )
    return bool(enabled)


def _feature_flag(setting_key: str, *, env_name: str, default: bool) -> bool:
    return get_bool_setting(
        setting_key,
        default=default,
        env_fallbacks=(env_name,),
    )


def is_monitoring_ingest_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_INGEST_ENABLED,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_INGEST_ENABLED,
        env_name="MONITORING_INGEST_ENABLED",
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_INGEST_ENABLED, True)
        ),
    )


def is_monitoring_leap_telemetry_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_LEAP_TELEMETRY,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_LEAP_TELEMETRY,
        env_name="MONITORING_LEAP_TELEMETRY",
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_LEAP_TELEMETRY, True)
        ),
    )


def is_monitoring_http_metrics_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_HTTP_METRICS_ENABLED,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_HTTP_METRICS_ENABLED,
        env_name="MONITORING_HTTP_METRICS_ENABLED",
        default=bool(
            DEFAULT_SETTING_VALUES.get(
                SETTING_KEY_MONITORING_HTTP_METRICS_ENABLED, True
            )
        ),
    )


def is_monitoring_jobs_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_JOBS_ENABLED,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_JOBS_ENABLED,
        env_name="MONITORING_JOBS_ENABLED",
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_JOBS_ENABLED, True)
        ),
    )


def is_monitoring_alerts_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_ALERTS_ENABLED,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_ALERTS_ENABLED,
        env_name="MONITORING_ALERTS_ENABLED",
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_ALERTS_ENABLED, True)
        ),
    )


def is_monitoring_analytics_enabled() -> bool:
    from app.crud.installation_settings import (
        DEFAULT_SETTING_VALUES,
        SETTING_KEY_MONITORING_ANALYTICS_ENABLED,
    )

    return _feature_flag(
        SETTING_KEY_MONITORING_ANALYTICS_ENABLED,
        env_name="MONITORING_ANALYTICS_ENABLED",
        default=bool(
            DEFAULT_SETTING_VALUES.get(SETTING_KEY_MONITORING_ANALYTICS_ENABLED, True)
        ),
    )


def ensure_monitoring_setting_defaults() -> list:
    """
    Seed missing monitoring / energy_logger settings (idempotent).

    Prefer ensure_central_config_tables() during DB creation; this remains
    for explicit calls / older startup paths.
    """
    from app.crud.installation_settings import seed_installation_runtime_defaults

    return seed_installation_runtime_defaults()
