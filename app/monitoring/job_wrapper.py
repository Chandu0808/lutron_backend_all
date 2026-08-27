"""
Job execution instrumentation (Phase 9).

Observational wrappers around existing scheduled/background jobs.
Emits only via Instrumentation.job_run — never Storage.

Does not alter scheduling, retries, timing, results, or exception propagation
beyond recording telemetry.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar

from app.monitoring import instrumentation
from app.monitoring.exception_capture import merge_exception_into_detail
from app.monitoring.flags import is_monitoring_enabled, is_monitoring_jobs_enabled

logger = logging.getLogger("lutron_monitoring.job_wrapper")

F = TypeVar("F", bound=Callable[..., Any])


def is_job_instrumentation_active() -> bool:
    return is_monitoring_enabled() and is_monitoring_jobs_enabled()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _host_pid() -> int:
    return os.getpid()


def _safe_job_run(job_key: str, outcome: str, **kwargs: Any) -> None:
    try:
        instrumentation.job_run(job_key, outcome, **kwargs)
    except Exception as exc:
        logger.warning("[monitoring][jobs] job_run emit failed: %s", exc)


def emit_job_started(
    job_key: str,
    *,
    started_at: Optional[datetime] = None,
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort Job Started (outcome=running). No-op when jobs flag off."""
    if not is_job_instrumentation_active():
        return
    started = started_at or _utcnow()
    payload = dict(detail or {})
    if component_code:
        payload.setdefault("component_code", component_code)
    _safe_job_run(
        job_key,
        "running",
        started_at=started,
        finished_at=None,
        duration_ms=None,
        host_pid=_host_pid(),
        trigger_source=trigger_source,
        detail=payload or None,
    )


def emit_job_finished(
    job_key: str,
    outcome: str,
    *,
    started_at: datetime,
    finished_at: Optional[datetime] = None,
    duration_ms: Optional[int] = None,
    error_class: Optional[str] = None,
    error_message: Optional[str] = None,
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort completion emit. No-op when jobs flag off."""
    if not is_job_instrumentation_active():
        return
    finished = finished_at or _utcnow()
    if duration_ms is None:
        duration_ms = max(0, int((finished - started_at).total_seconds() * 1000))
    payload = dict(detail or {})
    if component_code:
        payload.setdefault("component_code", component_code)
    _safe_job_run(
        job_key,
        outcome,
        started_at=started_at,
        finished_at=finished,
        duration_ms=duration_ms,
        error_class=error_class,
        error_message=error_message,
        host_pid=_host_pid(),
        trigger_source=trigger_source,
        detail=payload or None,
    )


@contextmanager
def job_execution(
    job_key: str,
    *,
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    emit_start: bool = True,
) -> Iterator[Dict[str, Any]]:
    """
    Context manager for observational job spans.

    Yields a mutable status dict. Callers that swallow exceptions should set::

        status["outcome"] = "failure"
        status["error_class"] = type(exc).__name__
        status["error_message"] = str(exc)

    Uncaught exceptions are recorded as failure and re-raised unchanged.
    """
    status: Dict[str, Any] = {
        "outcome": "success",
        "error_class": None,
        "error_message": None,
        "detail": dict(detail or {}),
    }
    if not is_job_instrumentation_active():
        yield status
        return

    started_at = _utcnow()
    t0 = time.perf_counter()
    if emit_start:
        emit_job_started(
            job_key,
            started_at=started_at,
            trigger_source=trigger_source,
            component_code=component_code,
            detail=status["detail"],
        )
    try:
        yield status
    except Exception as exc:
        duration_ms = max(0, int((time.perf_counter() - t0) * 1000))
        # Best-effort exception metadata; never mask the original failure.
        detail = merge_exception_into_detail(status.get("detail"), exc)
        status["detail"] = detail
        emit_job_finished(
            job_key,
            "failure",
            started_at=started_at,
            duration_ms=duration_ms,
            error_class=type(exc).__name__,
            error_message=str(exc),
            trigger_source=trigger_source,
            component_code=component_code,
            detail=detail,
        )
        raise
    else:
        duration_ms = max(0, int((time.perf_counter() - t0) * 1000))
        outcome = str(status.get("outcome") or "success")
        emit_job_finished(
            job_key,
            outcome,
            started_at=started_at,
            duration_ms=duration_ms,
            error_class=status.get("error_class"),
            error_message=status.get("error_message"),
            trigger_source=trigger_source,
            component_code=component_code,
            detail=status.get("detail"),
        )


def report_job_skipped(
    job_key: str,
    *,
    outcome: str = "skipped_lock",
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    """Record skip/cancel only when existing job logic already skipped work."""
    if not is_job_instrumentation_active():
        return
    now = _utcnow()
    payload = dict(detail or {})
    if component_code:
        payload.setdefault("component_code", component_code)
    _safe_job_run(
        job_key,
        outcome,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        host_pid=_host_pid(),
        trigger_source=trigger_source,
        detail=payload or None,
    )


def wrap_callable(
    job_key: str,
    fn: F,
    *,
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
    result_failure: Optional[Callable[[Any], bool]] = None,
) -> F:
    """
    Wrap a sync callable. When monitoring is off, behavior matches ``fn``.

    ``result_failure(result)`` → True means soft failure (job returned error
    without raising), e.g. device_refresh ``{"status": "error"}``.
    """

    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_job_instrumentation_active():
            return fn(*args, **kwargs)
        with job_execution(
            job_key,
            trigger_source=trigger_source,
            component_code=component_code,
        ) as status:
            result = fn(*args, **kwargs)
            if result_failure is not None:
                try:
                    if result_failure(result):
                        status["outcome"] = "failure"
                        if isinstance(result, dict):
                            msg = result.get("message")
                            if msg is not None:
                                status["error_message"] = str(msg)
                            status["error_class"] = "SoftFailure"
                except Exception:
                    pass
            return result

    return _wrapped  # type: ignore[return-value]


def wrap_async_callable(
    job_key: str,
    fn: F,
    *,
    trigger_source: Optional[str] = None,
    component_code: Optional[str] = None,
) -> F:
    """Wrap an async callable. Exceptions are recorded then re-raised."""

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        if not is_job_instrumentation_active():
            return await fn(*args, **kwargs)
        with job_execution(
            job_key,
            trigger_source=trigger_source,
            component_code=component_code,
        ) as status:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                # Outer job may swallow; mark failure if caller catches after await.
                # Re-raise so context manager records + propagates when not swallowed.
                status["outcome"] = "failure"
                status["error_class"] = type(exc).__name__
                status["error_message"] = str(exc)
                raise

    return _wrapped  # type: ignore[return-value]
