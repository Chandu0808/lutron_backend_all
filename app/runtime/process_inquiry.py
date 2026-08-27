"""
OS process inquiry for Runtime ownership reconciliation (Phase M2B).

Uses mechanisms available after M2A: process metadata + diagnostic PID hint.
Does not implement heartbeat / runtime identity.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("lutron_runtime.process_inquiry")

# Command-line tokens that indicate a plausible energy_logger mutex holder.
HOLDER_CMDLINE_TOKENS = (
    "energy_logger_process_entrypoint",
    "app.energy_logger",
    "energy_logger",
    "acquire_energy_logger_mutex",
    r"Lutron.EnergyLogger",
)

DIAGNOSTIC_LOCK_NAME = "energy_logger.lock"


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    name: str
    cmdline: str
    create_time: Optional[float] = None
    ppid: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def diagnostic_pid_file_path() -> str:
    return os.path.abspath(
        os.path.join(tempfile.gettempdir(), DIAGNOSTIC_LOCK_NAME)
    )


def read_diagnostic_pid_hint() -> Optional[int]:
    """
    Best-effort PID from diagnostic file. Never authoritative for ownership.
    """
    path = diagnostic_pid_file_path()
    try:
        if not os.path.exists(path):
            return None
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("pid="):
            raw = line.split("=", 1)[1].strip()
            try:
                return int(raw)
            except ValueError:
                return None
        if line.isdigit():
            return int(line)
    return None


def pid_alive(pid: int) -> bool:
    if pid is None or int(pid) <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(int(pid)))
    except Exception:
        pass
    if sys.platform != "win32":
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return int(code.value) == STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def terminate_pid(pid: int, *, force: bool = True) -> Dict[str, Any]:
    """Terminate a process by PID. Returns a diagnostic result dict."""
    result: Dict[str, Any] = {"pid": int(pid), "method": None, "ok": False}
    if int(pid) <= 0:
        result["error"] = "invalid_pid"
        return result

    # Prefer psutil (bounded waits) — avoids rare taskkill hangs under pytest.
    try:
        import psutil

        p = psutil.Process(int(pid))
        if force:
            p.kill()
        else:
            p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                p.kill()
            except Exception:
                pass
        result["method"] = "psutil"
        result["ok"] = not pid_alive(int(pid))
        if result["ok"]:
            return result
    except Exception as exc:
        result["psutil_error"] = str(exc)

    if sys.platform == "win32" and force:
        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/PID", str(int(pid))],
                capture_output=True,
                text=True,
                timeout=10,
            )
            result["method"] = "taskkill"
            result["returncode"] = completed.returncode
            result["stdout"] = (completed.stdout or "")[:500]
            result["stderr"] = (completed.stderr or "")[:500]
            result["ok"] = completed.returncode == 0 or not pid_alive(int(pid))
            return result
        except subprocess.TimeoutExpired:
            result["method"] = "taskkill"
            result["error"] = "taskkill_timeout"
            result["ok"] = not pid_alive(int(pid))
            return result

    if result.get("method") is None:
        result["error"] = result.get("psutil_error") or "terminate_failed"
    return result


def cmdline_suggests_mutex_holder(cmdline: str) -> bool:
    text = (cmdline or "").lower()
    if not text:
        return False
    tokens = tuple(t.lower() for t in HOLDER_CMDLINE_TOKENS)
    return any(tok.lower() in text for tok in tokens)


def cmdline_matches_energy_logger_entrypoint(cmdline: str) -> bool:
    text = (cmdline or "").lower()
    return (
        "energy_logger_process_entrypoint" in text
        or ("energy_logger" in text and "spawn_main" in text)
        or ("app.energy_logger" in text)
    )


def list_python_processes() -> List[ProcessSnapshot]:
    """Enumerate python processes with cmdline (best effort)."""
    try:
        import psutil

        rows: List[ProcessSnapshot] = []
        for p in psutil.process_iter(
            ["pid", "ppid", "name", "cmdline", "create_time"]
        ):
            try:
                info = p.info
                name = (info.get("name") or "").lower()
                if "python" not in name:
                    continue
                cmd_list = info.get("cmdline") or []
                cmdline = " ".join(str(x) for x in cmd_list)
                rows.append(
                    ProcessSnapshot(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or ""),
                        cmdline=cmdline,
                        create_time=info.get("create_time"),
                        ppid=info.get("ppid"),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows
    except Exception as exc:
        logger.warning("[reconcile] psutil process list failed: %s", exc)
        return _list_python_processes_powershell()


def _list_python_processes_powershell() -> List[ProcessSnapshot]:
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "ForEach-Object { "
        "$_.ProcessId.ToString() + '|' + $_.ParentProcessId.ToString() + '|' + "
        "($_.CreationDate.ToString()) + '|' + ($_.Name) + '|' + ($_.CommandLine) "
        "}"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
    )
    rows: List[ProcessSnapshot] = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        try:
            rows.append(
                ProcessSnapshot(
                    pid=int(parts[0]),
                    ppid=int(parts[1]) if parts[1].isdigit() else None,
                    create_time=None,
                    name=parts[3],
                    cmdline=parts[4],
                )
            )
        except ValueError:
            continue
    return rows


def discover_mutex_holder_candidates(
    *,
    protected_pids: Optional[Set[int]] = None,
) -> List[ProcessSnapshot]:
    """
    Candidates that may hold the energy_logger mutex.

    Includes diagnostic PID hint if alive, plus cmdline-matched python processes.
    """
    protected = set(protected_pids or set())
    protected.add(os.getpid())
    seen: Set[int] = set()
    out: List[ProcessSnapshot] = []

    hint = read_diagnostic_pid_hint()
    if hint and hint not in protected and pid_alive(hint):
        snap = _snapshot_pid(hint)
        if snap is not None:
            out.append(snap)
            seen.add(hint)

    for proc in list_python_processes():
        if proc.pid in protected or proc.pid in seen:
            continue
        if cmdline_suggests_mutex_holder(proc.cmdline):
            out.append(proc)
            seen.add(proc.pid)
    return out


def _snapshot_pid(pid: int) -> Optional[ProcessSnapshot]:
    try:
        import psutil

        p = psutil.Process(int(pid))
        cmd = " ".join(p.cmdline())
        return ProcessSnapshot(
            pid=int(pid),
            name=p.name(),
            cmdline=cmd,
            create_time=p.create_time(),
            ppid=p.ppid(),
        )
    except Exception:
        if not pid_alive(pid):
            return None
        return ProcessSnapshot(
            pid=int(pid),
            name="unknown",
            cmdline="",
            create_time=None,
            ppid=None,
        )
