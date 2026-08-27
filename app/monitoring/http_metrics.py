"""
HTTP request aggregate metrics (Phase 8).

In-memory aggregation + FastAPI/Starlette middleware.
Emits only via Instrumentation.http_aggregate — never Storage.

Privacy: method, route template, status class, durations only.
No query params, bodies, headers, JWT, user id, IP, or processor ids.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.monitoring import instrumentation
from app.monitoring.exception_capture import capture_exception
from app.monitoring.flags import (
    is_monitoring_enabled,
    is_monitoring_http_metrics_enabled,
)

logger = logging.getLogger("lutron_monitoring.http_metrics")

COMPONENT_CODE = "api"
_DEFAULT_FLUSH_SECONDS = 15.0
_MAX_BUFFER_KEYS = 2000
_MAX_LAST_EXCEPTIONS = 200
_ID_SEGMENT = re.compile(
    r"^(?:\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)

AggKey = Tuple[datetime, str, str, str]  # bucket, method, route, status_class


@dataclass
class AggCell:
    """In-memory aggregate for one (bucket, method, route, status_class)."""

    request_count: int = 0
    error_count: int = 0
    sum_duration_ms: int = 0
    min_duration_ms: int = 0
    max_duration_ms: int = 0

    def add(self, duration_ms: int, *, is_error: bool) -> None:
        d = max(0, int(duration_ms))
        if self.request_count == 0:
            self.min_duration_ms = d
            self.max_duration_ms = d
        else:
            if d < self.min_duration_ms:
                self.min_duration_ms = d
            if d > self.max_duration_ms:
                self.max_duration_ms = d
        self.request_count += 1
        self.sum_duration_ms += d
        if is_error:
            self.error_count += 1

    @property
    def average_latency_ms(self) -> float:
        if self.request_count <= 0:
            return 0.0
        return self.sum_duration_ms / float(self.request_count)


def is_http_metrics_active() -> bool:
    return is_monitoring_enabled() and is_monitoring_http_metrics_enabled()


def status_class_for(status_code: int) -> str:
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return "other"
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return "other"


def bucket_start_utc(now: Optional[datetime] = None) -> datetime:
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.replace(second=0, microsecond=0)


def sanitize_route_path(path: str) -> str:
    """Collapse numeric/UUID path segments to limit cardinality."""
    if not path:
        return "/unknown"
    raw = path.split("?", 1)[0]
    parts = []
    for seg in raw.split("/"):
        if seg == "":
            continue
        if _ID_SEGMENT.match(seg):
            parts.append("{id}")
        else:
            # Cap extreme segment length
            parts.append(seg[:64] if len(seg) > 64 else seg)
    out = "/" + "/".join(parts) if parts else "/"
    return out[:256]


_last_exceptions_lock = threading.Lock()
_last_exceptions: Dict[Tuple[str, str], Dict[str, Any]] = {}


def remember_api_exception(
    method: str,
    route_template: str,
    meta: Dict[str, Any],
) -> None:
    try:
        key = ((method or "GET").upper()[:16], (route_template or "/unknown")[:256])
        with _last_exceptions_lock:
            _last_exceptions[key] = dict(meta)
            while len(_last_exceptions) > _MAX_LAST_EXCEPTIONS:
                _last_exceptions.pop(next(iter(_last_exceptions)))
    except Exception:
        pass


def get_last_api_exception(method: str, route_template: str) -> Optional[Dict[str, Any]]:
    key = ((method or "GET").upper()[:16], (route_template or "/unknown")[:256])
    with _last_exceptions_lock:
        meta = _last_exceptions.get(key)
        return dict(meta) if meta else None


def resolve_route_template(scope: Scope) -> str:
    route = scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            return path[:256]
    return sanitize_route_path(scope.get("path") or "/unknown")


def _flush_interval_seconds() -> float:
    raw = (os.getenv("MONITORING_HTTP_METRICS_FLUSH_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_FLUSH_SECONDS
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_FLUSH_SECONDS
    except ValueError:
        return _DEFAULT_FLUSH_SECONDS


class HttpMetricsAggregator:
    """
    Thread-safe request → aggregate buffer with periodic Instrumentation flush.

    ``record`` is O(1) under lock and must never raise into the request path.
    """

    def __init__(
        self,
        *,
        flush_seconds: Optional[float] = None,
        max_keys: int = _MAX_BUFFER_KEYS,
        emit: Optional[Callable[..., Any]] = None,
        component_code: str = COMPONENT_CODE,
    ) -> None:
        self.flush_seconds = (
            flush_seconds if flush_seconds is not None else _flush_interval_seconds()
        )
        self.max_keys = max_keys
        self._emit = emit or instrumentation.http_aggregate
        self.component_code = component_code
        self._lock = threading.Lock()
        self._cells: Dict[AggKey, AggCell] = {}
        self._dropped_buffer = 0
        self._flush_errors = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_flusher,
            name="monitoring-http-metrics",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[monitoring][http] aggregator started (flush=%ss)", self.flush_seconds
        )

    def stop(self, *, flush: bool = True, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        if flush:
            try:
                self.flush()
            except Exception as exc:
                logger.warning("[monitoring][http] final flush failed: %s", exc)
        logger.info("[monitoring][http] aggregator stopped")

    def record(
        self,
        *,
        method: str,
        route_template: str,
        status_code: int,
        duration_ms: int,
        observed_at: Optional[datetime] = None,
    ) -> bool:
        """Record one request sample. Returns False if dropped/disabled path."""
        try:
            bucket = bucket_start_utc(observed_at)
            method_n = (method or "GET").upper()[:16]
            route = (route_template or "/unknown")[:256]
            sc = status_class_for(status_code)
            is_error = sc == "5xx"
            key: AggKey = (bucket, method_n, route, sc)
            with self._lock:
                cell = self._cells.get(key)
                if cell is None:
                    if len(self._cells) >= self.max_keys:
                        self._dropped_buffer += 1
                        return False
                    cell = AggCell()
                    self._cells[key] = cell
                cell.add(duration_ms, is_error=is_error)
            return True
        except Exception as exc:
            logger.warning("[monitoring][http] record failed: %s", exc)
            return False

    def snapshot(self) -> Dict[AggKey, AggCell]:
        with self._lock:
            return {
                k: AggCell(
                    request_count=v.request_count,
                    error_count=v.error_count,
                    sum_duration_ms=v.sum_duration_ms,
                    min_duration_ms=v.min_duration_ms,
                    max_duration_ms=v.max_duration_ms,
                )
                for k, v in self._cells.items()
            }

    def buffer_size(self) -> int:
        with self._lock:
            return len(self._cells)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "buffer_keys": len(self._cells),
                "dropped_buffer": self._dropped_buffer,
                "flush_errors": self._flush_errors,
            }

    def flush(self) -> int:
        """Emit buffered cells via Instrumentation. Returns emit attempt count."""
        with self._lock:
            pending = self._cells
            self._cells = {}
        emitted = 0
        for (bucket, method, route, sc), cell in pending.items():
            if cell.request_count <= 0:
                continue
            try:
                self._emit(
                    component_code=self.component_code,
                    bucket_start=bucket,
                    route_template=route,
                    method=method,
                    status_class=sc,
                    request_count=cell.request_count,
                    error_count=cell.error_count,
                    sum_duration_ms=cell.sum_duration_ms,
                    max_duration_ms=cell.max_duration_ms,
                )
                emitted += 1
            except Exception as exc:
                self._flush_errors += 1
                logger.warning("[monitoring][http] emit failed: %s", exc)
        return emitted

    def _run_flusher(self) -> None:
        while not self._stop.wait(self.flush_seconds):
            try:
                self.flush()
            except Exception as exc:
                self._flush_errors += 1
                logger.warning("[monitoring][http] flusher error: %s", exc)


class HttpMetricsMiddleware:
    """
    Pure ASGI middleware: measure duration, classify status, record aggregate.

    Never raises monitoring errors into the app. Recording is a short lock update;
    Instrumentation flush is off the request path.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        aggregator: Optional[HttpMetricsAggregator] = None,
        active_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.app = app
        self._aggregator = aggregator
        self._active_check = active_check or is_http_metrics_active

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._active_check():
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status") or 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            status_holder["code"] = 500
            try:
                meta = capture_exception(exc)
                if meta:
                    remember_api_exception(
                        scope.get("method") or "GET",
                        resolve_route_template(scope),
                        meta,
                    )
            except Exception:
                pass
            # Still record, then re-raise so LMS behavior is unchanged.
            self._safe_record(scope, status_holder["code"], start)
            raise
        else:
            self._safe_record(scope, status_holder["code"], start)

    def _safe_record(self, scope: Scope, status_code: int, start: float) -> None:
        try:
            agg = self._aggregator or get_http_metrics_aggregator()
            if agg is None:
                return
            duration_ms = int((time.perf_counter() - start) * 1000)
            if duration_ms < 0:
                duration_ms = 0
            agg.record(
                method=scope.get("method") or "GET",
                route_template=resolve_route_template(scope),
                status_code=status_code,
                duration_ms=duration_ms,
            )
        except Exception:
            pass


_aggregator: Optional[HttpMetricsAggregator] = None
_middleware_installed = False


def get_http_metrics_aggregator() -> Optional[HttpMetricsAggregator]:
    return _aggregator


def install_http_metrics_middleware(app: Any) -> None:
    """
    Register middleware on the FastAPI app (idempotent).

    Safe when flags are off: middleware no-ops until activated.
    """
    global _middleware_installed
    try:
        if _middleware_installed:
            return
        app.add_middleware(HttpMetricsMiddleware)
        _middleware_installed = True
        logger.info("[monitoring][http] middleware installed")
    except Exception as exc:
        logger.warning("[monitoring][http] middleware install failed: %s", exc)


def start_http_metrics(
    *,
    flush_seconds: Optional[float] = None,
) -> Optional[HttpMetricsAggregator]:
    """Start aggregator when flags are on. Never raises."""
    global _aggregator
    try:
        if not is_http_metrics_active():
            return None
        if _aggregator is not None and _aggregator.running:
            return _aggregator
        agg = HttpMetricsAggregator(flush_seconds=flush_seconds)
        agg.start()
        _aggregator = agg
        return agg
    except Exception as exc:
        logger.warning("[monitoring][http] start failed: %s", exc)
        return None


def stop_http_metrics(timeout: float = 5.0) -> None:
    global _aggregator
    agg = _aggregator
    _aggregator = None
    if agg is None:
        return
    try:
        agg.stop(flush=True, timeout=timeout)
    except Exception as exc:
        logger.warning("[monitoring][http] stop failed: %s", exc)
