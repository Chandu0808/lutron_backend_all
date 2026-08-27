"""
LUTRON_INSTALL_ID resolution (M2.5).

Priority: env → registry → development fallback.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("lutron_runtime.install_id")

ENV_INSTALL_ID = "LUTRON_INSTALL_ID"
DEFAULT_INSTALL_ID = "default"
REG_PATHS = (
    r"SOFTWARE\Lutron\LMS",
    r"SOFTWARE\WOW6432Node\Lutron\LMS",
)
REG_VALUE_NAMES = ("InstallId", "install_id", "LUTRON_INSTALL_ID")


@dataclass(frozen=True)
class InstallIdResolution:
    install_id: str
    source: str  # env | registry | development_fallback

    @property
    def is_production_default(self) -> bool:
        return self.install_id == DEFAULT_INSTALL_ID


def _from_env() -> Optional[str]:
    raw = (os.getenv(ENV_INSTALL_ID) or "").strip()
    return raw or None


def _from_registry() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in REG_PATHS:
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            try:
                for value_name in REG_VALUE_NAMES:
                    try:
                        val, _ = winreg.QueryValueEx(key, value_name)
                        text = str(val).strip()
                        if text:
                            return text
                    except OSError:
                        continue
                # Optional subkey InstallName\InstallId — scan one level
                try:
                    i = 0
                    while True:
                        sub = winreg.EnumKey(key, i)
                        i += 1
                        try:
                            sk = winreg.OpenKey(key, sub)
                        except OSError:
                            continue
                        try:
                            for value_name in REG_VALUE_NAMES:
                                try:
                                    val, _ = winreg.QueryValueEx(sk, value_name)
                                    text = str(val).strip()
                                    if text:
                                        return text
                                except OSError:
                                    continue
                        finally:
                            winreg.CloseKey(sk)
                except OSError:
                    pass
            finally:
                winreg.CloseKey(key)
    return None


_cached: Optional[InstallIdResolution] = None


def resolve_install_id(*, force_refresh: bool = False) -> InstallIdResolution:
    """
    Resolve install_id with source logging.

    1. LUTRON_INSTALL_ID env
    2. Registry HKLM/HKCU SOFTWARE\\Lutron\\LMS
    3. development fallback \"default\"
    """
    global _cached
    if _cached is not None and not force_refresh:
        return _cached

    env_val = _from_env()
    if env_val:
        res = InstallIdResolution(install_id=env_val, source="env")
        logger.info(
            "[install_id] source=env value=%s",
            res.install_id,
        )
        _cached = res
        return res

    reg_val = _from_registry()
    if reg_val:
        res = InstallIdResolution(install_id=reg_val, source="registry")
        logger.info(
            "[install_id] source=registry value=%s",
            res.install_id,
        )
        _cached = res
        return res

    res = InstallIdResolution(
        install_id=DEFAULT_INSTALL_ID, source="development_fallback"
    )
    logger.warning(
        "[install_id] source=development_fallback value=%s — "
        "set LUTRON_INSTALL_ID or registry for production multi-install safety",
        res.install_id,
    )
    _cached = res
    return res


def clear_install_id_cache() -> None:
    """Test helper."""
    global _cached
    _cached = None


def sanitize_install_id(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "_").replace("/", "_")
    return text or DEFAULT_INSTALL_ID
