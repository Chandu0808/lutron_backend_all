"""
Windows Job Object helper for Runtime children (Phase M1 + M2.5).

M1: KILL_ON_JOB_CLOSE assign.
M2.5: live membership via IsProcessInJob + JobObjectBasicProcessIdList.
Historical assign log is diagnostic only — never used for adopt/kill.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lutron_runtime.job_object")

# Win32
JobObjectExtendedLimitInformation = 9
JobObjectBasicProcessIdList = 3
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_ASSIGN_ACCESS = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION
JOB_OBJECT_QUERY = 0x0004


class JobObjectError(RuntimeError):
    """Raised when Job Object create/configure/assign fails on Windows."""


@dataclass(frozen=True)
class ParentJobDiagnostics:
    """API process job nesting diagnostics (M2.5)."""

    api_in_any_job: bool
    nested_jobs_supported: bool
    platform: str
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JobObjectBase(ABC):
    @abstractmethod
    def assign_pid(self, pid: int) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @property
    @abstractmethod
    def active(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> Optional[str]:
        ...

    def list_live_member_pids(self) -> List[int]:
        """Live Job membership — authoritative for M2.5."""
        return []

    def is_pid_in_job(self, pid: int) -> bool:
        """True if pid is currently associated with this Job."""
        return False

    @property
    def assign_history(self) -> List[int]:
        """Diagnostic only — never use for adopt/kill."""
        return []

    # Back-compat alias (tests); must not be used for ownership decisions.
    @property
    def assigned_pids(self) -> List[int]:
        return self.assign_history


class NullJobObject(JobObjectBase):
    """No-op for non-Windows platforms / unit tests without Win32."""

    def __init__(self, *, reason: str = "non_windows") -> None:
        self._reason = reason
        self._closed = False
        self._history: List[int] = []
        self._live: set[int] = set()
        logger.warning(
            "[runtime][job] NullJobObject in use reason=%s — "
            "KILL_ON_JOB_CLOSE orphan prevention unavailable",
            reason,
        )

    def assign_pid(self, pid: int) -> None:
        if self._closed:
            raise JobObjectError("NullJobObject already closed")
        self._history.append(int(pid))
        self._live.add(int(pid))
        logger.debug("[runtime][job] null assign pid=%s", pid)

    def close(self) -> None:
        self._closed = True
        self._live.clear()

    @property
    def active(self) -> bool:
        return not self._closed

    @property
    def name(self) -> Optional[str]:
        return f"null:{self._reason}"

    def list_live_member_pids(self) -> List[int]:
        return sorted(self._live)

    def is_pid_in_job(self, pid: int) -> bool:
        return int(pid) in self._live

    @property
    def assign_history(self) -> List[int]:
        return list(self._history)

    def note_exited(self, pid: int) -> None:
        self._live.discard(int(pid))


class WindowsJobObject(JobObjectBase):
    """
    Owns a Win32 Job Object handle with KILL_ON_JOB_CLOSE.

    Closing this object (or the process dying and releasing the handle) causes
    Windows to terminate every process still assigned to the job.
    """

    def __init__(self, *, name: Optional[str] = None) -> None:
        if sys.platform != "win32":
            raise JobObjectError("WindowsJobObject requires win32")

        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._name = name or "LutronRuntimeChildren"
        self._handle = None
        self._closed = False
        # Diagnostic assign log only (M2.5 — not authority)
        self._assign_history: List[int] = []

        self._configure_apis()
        self._create_and_configure()

    def _configure_apis(self) -> None:
        ctypes = self._ctypes
        wintypes = self._wintypes
        k = self._kernel32

        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE

        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k.SetInformationJobObject.restype = wintypes.BOOL

        k.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryInformationJobObject.restype = wintypes.BOOL

        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL

        k.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        k.IsProcessInJob.restype = wintypes.BOOL

        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE

        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._JOBOBJECT_EXTENDED_LIMIT_INFORMATION = (
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        )

    def _create_and_configure(self) -> None:
        ctypes = self._ctypes
        k = self._kernel32

        handle = k.CreateJobObjectW(None, None)
        if not handle:
            err = ctypes.get_last_error()
            raise JobObjectError(f"CreateJobObjectW failed error={err}")

        info = self._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            k.CloseHandle(handle)
            raise JobObjectError(
                f"SetInformationJobObject KILL_ON_JOB_CLOSE failed error={err}"
            )

        self._handle = handle
        logger.info(
            "[runtime][job] created name=%s handle=%s kill_on_close=1",
            self._name,
            int(handle) if handle else None,
        )

    def assign_pid(self, pid: int) -> None:
        if self._closed or not self._handle:
            raise JobObjectError("Job Object is closed")
        if pid is None or int(pid) <= 0:
            raise JobObjectError(f"Invalid pid for job assign: {pid!r}")

        ctypes = self._ctypes
        k = self._kernel32
        proc_handle = k.OpenProcess(PROCESS_ASSIGN_ACCESS, False, int(pid))
        if not proc_handle:
            err = ctypes.get_last_error()
            raise JobObjectError(
                f"OpenProcess failed pid={pid} error={err}"
            )
        try:
            ok = k.AssignProcessToJobObject(self._handle, proc_handle)
            if not ok:
                err = ctypes.get_last_error()
                raise JobObjectError(
                    f"AssignProcessToJobObject failed pid={pid} error={err}"
                )
        finally:
            k.CloseHandle(proc_handle)

        self._assign_history.append(int(pid))
        logger.info(
            "[runtime][job] assigned pid=%s job=%s history_len=%s",
            pid,
            self._name,
            len(self._assign_history),
        )

    def list_live_member_pids(self) -> List[int]:
        """QueryInformationJobObject JobObjectBasicProcessIdList."""
        if self._closed or not self._handle:
            return []
        ctypes = self._ctypes
        wintypes = self._wintypes
        k = self._kernel32

        # Start with room for 64 PIDs; grow if needed
        capacity = 64
        for _ in range(4):
            class LIST(ctypes.Structure):
                _fields_ = [
                    ("NumberOfAssignedProcesses", wintypes.DWORD),
                    ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * capacity),
                ]

            buf = LIST()
            ret_len = wintypes.DWORD(0)
            ok = k.QueryInformationJobObject(
                self._handle,
                JobObjectBasicProcessIdList,
                ctypes.byref(buf),
                ctypes.sizeof(buf),
                ctypes.byref(ret_len),
            )
            if ok:
                n = int(buf.NumberOfProcessIdsInList)
                return [int(buf.ProcessIdList[i]) for i in range(n)]
            err = ctypes.get_last_error()
            # ERROR_MORE_DATA = 234
            if err == 234:
                capacity *= 2
                continue
            logger.warning(
                "[runtime][job] QueryInformationJobObject process list failed err=%s",
                err,
            )
            return []
        return []

    def is_pid_in_job(self, pid: int) -> bool:
        """IsProcessInJob against this Job handle."""
        if self._closed or not self._handle or pid is None or int(pid) <= 0:
            return False
        ctypes = self._ctypes
        wintypes = self._wintypes
        k = self._kernel32
        access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION
        h = k.OpenProcess(access, False, int(pid))
        if not h:
            # Fallback: membership list
            return int(pid) in self.list_live_member_pids()
        try:
            result = wintypes.BOOL(False)
            ok = k.IsProcessInJob(h, self._handle, ctypes.byref(result))
            if not ok:
                err = ctypes.get_last_error()
                logger.debug(
                    "[runtime][job] IsProcessInJob failed pid=%s err=%s",
                    pid,
                    err,
                )
                return int(pid) in self.list_live_member_pids()
            return bool(result.value)
        finally:
            k.CloseHandle(h)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        handle = self._handle
        self._handle = None
        if handle:
            self._kernel32.CloseHandle(handle)
            logger.info(
                "[runtime][job] closed name=%s (KILL_ON_JOB_CLOSE terminates members)",
                self._name,
            )

    @property
    def active(self) -> bool:
        return not self._closed and self._handle is not None

    @property
    def name(self) -> Optional[str]:
        return self._name

    @property
    def assign_history(self) -> List[int]:
        return list(self._assign_history)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def nested_jobs_supported() -> bool:
    """Windows 8 / Server 2012+ support nested jobs (documented)."""
    if sys.platform != "win32":
        return False
    try:
        v = sys.getwindowsversion()
        # Win8 = 6.2, Win10 = 10.0
        return (v.major, v.minor) >= (6, 2)
    except Exception:
        return True  # assume modern if unknown


def detect_parent_job_state() -> ParentJobDiagnostics:
    """IsProcessInJob(GetCurrentProcess(), NULL) diagnostics."""
    platform = sys.platform
    nested = nested_jobs_supported()
    if platform != "win32":
        return ParentJobDiagnostics(
            api_in_any_job=False,
            nested_jobs_supported=False,
            platform=platform,
            note="non_windows",
        )
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    k.IsProcessInJob.restype = wintypes.BOOL

    result = wintypes.BOOL(False)
    ok = k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(result))
    if not ok:
        err = ctypes.get_last_error()
        return ParentJobDiagnostics(
            api_in_any_job=False,
            nested_jobs_supported=nested,
            platform=platform,
            note=f"IsProcessInJob_failed error={err}",
        )
    in_job = bool(result.value)
    note = (
        "api_already_in_job — nested assign rules apply (Win8+)"
        if in_job
        else "api_not_in_job"
    )
    logger.info(
        "[runtime][job] parent_job_diag in_any_job=%s nested_supported=%s note=%s",
        in_job,
        nested,
        note,
    )
    return ParentJobDiagnostics(
        api_in_any_job=in_job,
        nested_jobs_supported=nested,
        platform=platform,
        note=note,
    )


def create_runtime_job_object(*, name: Optional[str] = None) -> JobObjectBase:
    """
    Create the Supervisor-owned Job Object.

    On Windows: real Job with KILL_ON_JOB_CLOSE.
    Elsewhere: NullJobObject (tests / non-Windows).
    """
    if sys.platform != "win32":
        return NullJobObject(reason="non_windows")
    return WindowsJobObject(name=name)
