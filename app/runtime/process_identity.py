"""
Process identity helpers for M2.5 (PID-reuse defense).

Authority: retained HANDLE and/or (pid + create_time). Never PID alone.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger("lutron_runtime.process_identity")


@dataclass(frozen=True)
class ProcessIdentity:
    """Spawn-time identity record — not derived from TEMP / cmdline."""

    pid: int
    create_time: Optional[float]
    proof_method: str  # handle | create_time | handle+create_time

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_process_create_time(pid: int) -> Optional[float]:
    """Return process create time as epoch seconds, or None."""
    if pid is None or int(pid) <= 0:
        return None
    try:
        import psutil

        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        pass
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        k.GetProcessTimes.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            if not k.GetProcessTimes(
                h,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            ):
                return None
            # FILETIME = 100-ns intervals since 1601-01-01
            high = creation.dwHighDateTime
            low = creation.dwLowDateTime
            val = (int(high) << 32) | int(low)
            # Convert to Unix epoch
            return (val / 10_000_000.0) - 11644473600.0
        finally:
            k.CloseHandle(h)
    except Exception as exc:
        logger.debug("[identity] create_time failed pid=%s err=%s", pid, exc)
        return None


def verify_create_time(
    pid: int,
    expected: Optional[float],
    *,
    tolerance_seconds: float = 1.0,
) -> bool:
    """True if live create_time matches expected within tolerance."""
    if expected is None:
        return False
    live = get_process_create_time(pid)
    if live is None:
        return False
    return abs(float(live) - float(expected)) <= max(0.0, float(tolerance_seconds))


def build_identity_at_spawn(pid: int) -> ProcessIdentity:
    ct = get_process_create_time(pid)
    method = "create_time" if ct is not None else "pid_only_insufficient"
    if ct is None:
        logger.warning(
            "[identity] spawn create_time unavailable pid=%s — "
            "PID-reuse defense degraded",
            pid,
        )
    else:
        method = "create_time"
    return ProcessIdentity(pid=int(pid), create_time=ct, proof_method=method)


def identity_still_matches(identity: Optional[ProcessIdentity]) -> bool:
    if identity is None:
        return False
    if identity.create_time is None:
        return False
    return verify_create_time(identity.pid, identity.create_time)
