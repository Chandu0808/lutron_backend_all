"""
Energy logger OS-backed singleton (Phase M2A + M2.5 ACL / install_id).

Authoritative ownership: Windows named mutex
  Global\\Lutron.EnergyLogger.<install_id>

install_id: LUTRON_INSTALL_ID env → registry → development fallback.
Mutex created with explicit SECURITY_ATTRIBUTES (no default DACL).

The mutex handle is held for the process lifetime. Release is performed by
the OS on process exit — no application cleanup is required.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional, Tuple

from app.runtime.install_id import (
    DEFAULT_INSTALL_ID,
    ENV_INSTALL_ID,
    resolve_install_id,
    sanitize_install_id,
)

logger = logging.getLogger("lutron_runtime.energy_logger_mutex")

MUTEX_NAME_PREFIX = r"Global\Lutron.EnergyLogger."

# Kept alive so the handle is not GC-closed before process exit.
_MUTEX_HANDLE = None
_MUTEX_NAME: Optional[str] = None
_ACQUIRED = False
_LAST_INSTALL_SOURCE: Optional[str] = None

ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
SYNCHRONIZE = 0x00100000
MUTEX_MODIFY_STATE = 0x0001
MUTEX_ACCESS = SYNCHRONIZE | MUTEX_MODIFY_STATE

# Mutex kernel-object probe results (OpenMutexW).
MUTEX_ABSENT = "absent"
MUTEX_PRESENT = "present"
MUTEX_INACCESSIBLE = "inaccessible"


class MutexAcquireError(RuntimeError):
    """Raised when the named mutex cannot be acquired."""


def install_id() -> str:
    """Resolve install_id (logs source once via resolve_install_id)."""
    global _LAST_INSTALL_SOURCE
    res = resolve_install_id()
    _LAST_INSTALL_SOURCE = res.source
    return sanitize_install_id(res.install_id)


def install_id_source() -> Optional[str]:
    return _LAST_INSTALL_SOURCE


def mutex_name(install: Optional[str] = None) -> str:
    if install is None:
        iid = install_id()
    else:
        iid = sanitize_install_id(install)
    if not iid:
        iid = DEFAULT_INSTALL_ID
    return f"{MUTEX_NAME_PREFIX}{iid}"


def is_acquired() -> bool:
    return bool(_ACQUIRED and _MUTEX_HANDLE)


def current_mutex_name() -> Optional[str]:
    return _MUTEX_NAME


def _current_user_sid_string() -> Optional[str]:
    """Return current process user SID as string (S-1-…)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        TOKEN_QUERY = 0x0008
        TokenUser = 1

        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("Sid", ctypes.c_void_p),
                ("Attributes", wintypes.DWORD),
            ]

        class TOKEN_USER(ctypes.Structure):
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        GetCurrentProcess = kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE

        OpenProcessToken = advapi.OpenProcessToken
        OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        OpenProcessToken.restype = wintypes.BOOL

        GetTokenInformation = advapi.GetTokenInformation
        GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        GetTokenInformation.restype = wintypes.BOOL

        ConvertSidToStringSidW = advapi.ConvertSidToStringSidW
        ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        ConvertSidToStringSidW.restype = wintypes.BOOL

        LocalFree = kernel32.LocalFree
        LocalFree.argtypes = [wintypes.HLOCAL]
        LocalFree.restype = wintypes.HLOCAL

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        if not OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            needed = wintypes.DWORD(0)
            GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(needed))
            buf = ctypes.create_string_buffer(needed.value)
            if not GetTokenInformation(
                token, TokenUser, buf, needed, ctypes.byref(needed)
            ):
                return None
            tu = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
            sid_str = wintypes.LPWSTR()
            if not ConvertSidToStringSidW(tu.User.Sid, ctypes.byref(sid_str)):
                return None
            try:
                return str(sid_str.value)
            finally:
                LocalFree(sid_str)
        finally:
            CloseHandle(token)
    except Exception as exc:
        logger.debug("[mutex] current user SID lookup failed: %s", exc)
        return None


def _build_mutex_security() -> Tuple[Any, Any, Any]:
    """
    Build SECURITY_ATTRIBUTES with explicit DACL.

    Allow SYNCHRONIZE|MUTEX_MODIFY_STATE to:
      - LOCAL SYSTEM (SY)
      - Builtin Administrators (BA)
      - current process user SID (service account / interactive)

    Returns (SECURITY_ATTRIBUTES ctypes instance, SD raw pointer owner, keep-alive).
    Caller must LocalFree the security descriptor after CreateMutexW.
    """
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    ConvertStringSecurityDescriptorToSecurityDescriptorW = (
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    )
    ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    # 0x100001 = SYNCHRONIZE | MUTEX_MODIFY_STATE
    ace = "A;;0x100001;;;"
    parts = [f"({ace}SY)", f"({ace}BA)"]
    user_sid = _current_user_sid_string()
    if user_sid:
        parts.append(f"({ace}{user_sid})")
    sddl = "D:" + "".join(parts)

    sd = ctypes.c_void_p()
    sd_size = wintypes.ULONG(0)
    SDDL_REVISION_1 = 1
    ok = ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(sd), ctypes.byref(sd_size)
    )
    if not ok:
        err = ctypes.get_last_error()
        raise MutexAcquireError(
            f"ConvertStringSecurityDescriptorToSecurityDescriptorW failed error={err}"
        )

    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = sd
    sa.bInheritHandle = False
    logger.info(
        "[mutex] security_descriptor applied sddl=%s user_sid=%s",
        sddl,
        user_sid or "none",
    )
    return sa, sd, SECURITY_ATTRIBUTES


def acquire_energy_logger_mutex(*, install: Optional[str] = None) -> str:
    """
    Acquire the process-lifetime singleton mutex.

    Returns the mutex name on success.
    Raises MutexAcquireError if another instance holds it (or on Win32 failure).
    """
    global _MUTEX_HANDLE, _MUTEX_NAME, _ACQUIRED

    if _ACQUIRED and _MUTEX_HANDLE:
        return _MUTEX_NAME or mutex_name(install)

    name = mutex_name(install)
    if sys.platform != "win32":
        return _acquire_non_windows_stub(name)

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    sa = None
    sd = None
    try:
        sa, sd, _ = _build_mutex_security()
    except MutexAcquireError as exc:
        logger.error("[mutex] ACL build failed — refusing default DACL: %s", exc)
        raise

    try:
        # Create or open; do not take ownership via bInitialOwner — wait explicitly.
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(ctypes.byref(sa), False, name)
        if not handle:
            err = ctypes.get_last_error()
            raise MutexAcquireError(f"CreateMutexW failed name={name!r} error={err}")

        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        wait = kernel32.WaitForSingleObject(handle, 0)
        if wait == WAIT_TIMEOUT:
            kernel32.CloseHandle(handle)
            logger.warning(
                "[mutex] acquire denied name=%s already_exists=%s wait=TIMEOUT",
                name,
                already,
            )
            raise MutexAcquireError(
                f"mutex_busy name={name} already_exists={already}"
            )
        if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            err = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise MutexAcquireError(
                f"WaitForSingleObject failed name={name!r} wait={wait} error={err}"
            )

        _MUTEX_HANDLE = handle
        _MUTEX_NAME = name
        _ACQUIRED = True
        logger.info(
            "[mutex] acquired name=%s already_existed=%s abandoned=%s "
            "handle=%s install_source=%s acl=custom",
            name,
            already,
            wait == WAIT_ABANDONED,
            int(handle),
            _LAST_INSTALL_SOURCE,
        )
        return name
    finally:
        if sd:
            kernel32.LocalFree(sd)


def probe_mutex_object(*, install: Optional[str] = None) -> str:
    """
    Read-only probe of the named mutex kernel object.

    Returns one of:
      - MUTEX_ABSENT: object does not exist
      - MUTEX_PRESENT: object exists and is openable (SYNCHRONIZE)
      - MUTEX_INACCESSIBLE: object likely exists but OpenMutexW was denied (error=5)

    ACCESS_DENIED must NOT be treated as absent — that caused reconcile to
    spawn-storm every ~2s while CreateMutexW also failed with error=5.
    """
    name = mutex_name(install)
    if sys.platform != "win32":
        return MUTEX_PRESENT if (_ACQUIRED and _MUTEX_NAME == name) else MUTEX_ABSENT

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.SetLastError(0)
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return MUTEX_PRESENT

    err = ctypes.get_last_error()
    if err == ERROR_ACCESS_DENIED:
        logger.warning(
            "[mutex] probe inaccessible name=%s error=%s "
            "(treating as present/foreign — do not spawn)",
            name,
            err,
        )
        return MUTEX_INACCESSIBLE
    # ERROR_FILE_NOT_FOUND (2) and similar → absent
    return MUTEX_ABSENT


def is_mutex_object_present(*, install: Optional[str] = None) -> bool:
    """
    True if the named mutex kernel object exists or is inaccessible.

    Does **not** acquire ownership. Inaccessible (OpenMutex ACCESS_DENIED)
    counts as present so ownership reconcile will not assume holder_gone.
    """
    return probe_mutex_object(install=install) != MUTEX_ABSENT


def wait_until_mutex_absent(
    *,
    timeout_seconds: float = 5.0,
    poll_seconds: float = 0.1,
    install: Optional[str] = None,
) -> bool:
    """Return True if mutex object disappears within timeout."""
    import time

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if not is_mutex_object_present(install=install):
            return True
        time.sleep(max(0.01, float(poll_seconds)))
    return not is_mutex_object_present(install=install)


def _acquire_non_windows_stub(name: str) -> str:
    """
    Non-Windows: in-process lock only (unit tests). Not production for LMS.
    """
    global _MUTEX_HANDLE, _MUTEX_NAME, _ACQUIRED
    import threading

    lock = getattr(_acquire_non_windows_stub, "_lock", None)
    if lock is None:
        lock = threading.Lock()
        _acquire_non_windows_stub._lock = lock  # type: ignore[attr-defined]
        _acquire_non_windows_stub._held = False  # type: ignore[attr-defined]

    if _acquire_non_windows_stub._held:  # type: ignore[attr-defined]
        raise MutexAcquireError(f"mutex_busy name={name} (non_windows_stub)")
    if not lock.acquire(blocking=False):
        raise MutexAcquireError(f"mutex_busy name={name} (non_windows_stub)")
    _acquire_non_windows_stub._held = True  # type: ignore[attr-defined]
    _MUTEX_HANDLE = lock
    _MUTEX_NAME = name
    _ACQUIRED = True
    logger.info("[mutex] acquired (non_windows stub) name=%s", name)
    return name


# Re-export for callers that imported ENV from this module historically
__all__ = [
    "DEFAULT_INSTALL_ID",
    "ENV_INSTALL_ID",
    "MUTEX_ABSENT",
    "MUTEX_INACCESSIBLE",
    "MUTEX_PRESENT",
    "MutexAcquireError",
    "acquire_energy_logger_mutex",
    "current_mutex_name",
    "install_id",
    "install_id_source",
    "is_acquired",
    "is_mutex_object_present",
    "mutex_name",
    "probe_mutex_object",
    "wait_until_mutex_absent",
]
