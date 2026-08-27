"""
Phase 5 — narrow, safe exception metadata capture for monitoring.

Best-effort only: capture failures must never mask or alter the original
application/job exception. Stores bounded, redacted primitives suitable for
mon_job_run.detail_json (no schema migration, no new issue stream).
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from types import TracebackType
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("lutron_monitoring.exception_capture")

# Tunable via existing env-flag style; defaults are conservative.
def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


MAX_EXCEPTION_MESSAGE_CHARS = _int_env("MONITORING_EXCEPTION_MESSAGE_MAX", 1000)
MAX_TRACEBACK_CHARS = _int_env("MONITORING_EXCEPTION_TRACEBACK_MAX", 8000)
MAX_TRACEBACK_FRAMES = _int_env("MONITORING_EXCEPTION_FRAME_MAX", 40)

# Narrow secret-ish patterns (case-insensitive). Avoid over-redacting.
_REDACT_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|"
        r"access[_-]?key|private[_-]?key|credential|conn(?:ection)?[_-]?str(?:ing)?)"
        r"(\s*[=:]\s*)([^\s,;\"']+)"
    ),
    re.compile(
        r"(?i)(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)"
    ),
    re.compile(
        r"(?i)(postgres(?:ql)?|mysql|mongodb|redis|amqp)://[^\s\"']+"
    ),
)

EXCEPTION_DETAIL_KEY = "exception"


def redact_text(text: str) -> str:
    """Best-effort redaction of common secret patterns."""
    if not text:
        return text
    out = text
    for pattern in _REDACT_PATTERNS:
        try:
            if pattern.pattern.startswith("(?i)(Bearer"):
                out = pattern.sub(r"\1[REDACTED]", out)
            elif "://" in pattern.pattern:
                out = pattern.sub("[REDACTED_URI]", out)
            else:
                out = pattern.sub(r"\1\2[REDACTED]", out)
        except Exception:
            continue
    return out


def _safe_str(value: Any, *, limit: int) -> str:
    try:
        text = str(value)
    except Exception:
        try:
            text = repr(value)
        except Exception:
            text = "<unprintable>"
    # Replace lone surrogates / non-text noise conservatively.
    text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    text = redact_text(text)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _frame_location(
    tb: Optional[TracebackType],
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    if tb is None:
        return None, None, None
    # Prefer the innermost application frame (last in the chain).
    cur: Optional[TracebackType] = tb
    last = cur
    while cur is not None:
        last = cur
        cur = cur.tb_next
    if last is None or last.tb_frame is None:
        return None, None, None
    code = last.tb_frame.f_code
    filename = getattr(code, "co_filename", None)
    func = getattr(code, "co_name", None)
    lineno = getattr(last, "tb_lineno", None)
    file_out = _safe_str(filename, limit=500) if filename else None
    func_out = _safe_str(func, limit=200) if func else None
    line_out: Optional[int] = None
    if isinstance(lineno, int):
        line_out = lineno
    else:
        try:
            line_out = int(lineno) if lineno is not None else None
        except (TypeError, ValueError):
            line_out = None
    return file_out, line_out, func_out


def _format_traceback(
    exc: BaseException,
    *,
    max_frames: int,
    max_chars: int,
) -> Optional[str]:
    try:
        frames = traceback.extract_tb(exc.__traceback__)
        if max_frames > 0 and len(frames) > max_frames:
            frames = frames[-max_frames:]
        summary = traceback.StackSummary.from_list(frames)
        lines: List[str] = summary.format()
        # Include exception type/message footer similar to format_exception_only.
        lines.extend(traceback.format_exception_only(type(exc), exc))
        text = "".join(lines)
        text = redact_text(
            text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        )
        if len(text) > max_chars:
            return text[: max(0, max_chars - 3)] + "..."
        return text
    except Exception:
        return None


def capture_exception(
    exc: BaseException,
    *,
    max_message_chars: int = MAX_EXCEPTION_MESSAGE_CHARS,
    max_traceback_chars: int = MAX_TRACEBACK_CHARS,
    max_frames: int = MAX_TRACEBACK_FRAMES,
) -> Optional[Dict[str, Any]]:
    """
    Capture bounded exception metadata.

    Returns None on capture failure (caller must ignore and continue).
    """
    try:
        exc_type = type(exc).__name__
        message = _safe_str(exc, limit=max_message_chars)
        try:
            file_name, line_no, function = _frame_location(exc.__traceback__)
        except Exception:
            file_name, line_no, function = None, None, None
        try:
            tb_text = _format_traceback(
                exc, max_frames=max_frames, max_chars=max_traceback_chars
            )
        except Exception:
            tb_text = None
        # Nested cause (best-effort, shallow).
        cause_meta = None
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, BaseException) and cause is not exc:
            try:
                cause_meta = {
                    "type": type(cause).__name__,
                    "message": _safe_str(cause, limit=min(300, max_message_chars)),
                }
            except Exception:
                cause_meta = None

        out: Dict[str, Any] = {
            "type": exc_type,
            "message": message,
            "file": file_name,
            "line": line_no,
            "function": function,
            "traceback": tb_text,
        }
        if cause_meta:
            out["cause"] = cause_meta
        return out
    except Exception as capture_exc:
        logger.warning(
            "[monitoring][exception_capture] capture failed: %s", capture_exc
        )
        return None


def merge_exception_into_detail(
    detail: Optional[Dict[str, Any]],
    exc: BaseException,
) -> Dict[str, Any]:
    """
    Return a new detail dict with exception metadata merged.

    Never raises; on failure returns a shallow copy of detail (or {}).
    """
    try:
        base = dict(detail or {})
    except Exception:
        base = {}
    try:
        meta = capture_exception(exc)
        if meta is not None:
            base[EXCEPTION_DETAIL_KEY] = meta
    except Exception:
        pass
    return base


def extract_exception_fields(
    *sources: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Pull Issue DTO error location fields from detail dicts.

    Prefer the first source that contains a usable ``exception`` object
    (typically mon_job_run.detail_json, then alert.detail_json).
    """
    empty = {
        "file": None,
        "line": None,
        "function": None,
        "traceback": None,
        "type": None,
        "message": None,
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        raw = source.get(EXCEPTION_DETAIL_KEY)
        if not isinstance(raw, dict):
            continue
        file_name = raw.get("file")
        line = raw.get("line")
        function = raw.get("function")
        tb = raw.get("traceback")
        exc_type = raw.get("type")
        message = raw.get("message")
        return {
            "file": file_name if isinstance(file_name, str) else None,
            "line": line if isinstance(line, int) else None,
            "function": function if isinstance(function, str) else None,
            "traceback": tb if isinstance(tb, str) else None,
            "type": exc_type if isinstance(exc_type, str) else None,
            "message": message if isinstance(message, str) else None,
        }
    return empty
