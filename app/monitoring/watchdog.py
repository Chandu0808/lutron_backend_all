"""
API-process monitoring watchdog (Phase 4).

Emits heartbeats for ``api`` and ``monitoring_pipeline`` via Instrumentation only.
Also persists stale→down for components whose heartbeats have expired.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from app.monitoring import instrumentation
from app.monitoring.health_stale import heartbeat_stale_seconds, is_heartbeat_stale
from app.monitoring.registry import get_registry
from app.monitoring.service import MonitoringService
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.session import monitoring_session

logger = logging.getLogger("lutron_monitoring.watchdog")

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
COMPONENT_API = "api"
COMPONENT_PIPELINE = "monitoring_pipeline"


def _heartbeat_interval_seconds() -> float:
    raw = (os.getenv("MONITORING_HEARTBEAT_INTERVAL_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[monitoring][watchdog] invalid MONITORING_HEARTBEAT_INTERVAL_SECONDS=%r; using %s",
            raw,
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        )
        return DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    if value <= 0:
        return DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    return value


class MonitoringWatchdog:
    """
    Periodic heartbeat emitter for the API process and monitoring pipeline.

    Exception-isolated: tick failures never raise into the host process.
    """

    def __init__(
        self,
        *,
        service: Optional[MonitoringService] = None,
        interval_seconds: Optional[float] = None,
        emit_immediately: bool = True,
    ) -> None:
        self._service = service
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else _heartbeat_interval_seconds()
        )
        self.emit_immediately = emit_immediately

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._tick_lock = threading.Lock()
        self._last_emit_at: dict[str, float] = {}
        self._last_cert_emit_at: float = 0.0
        # Minimum gap between emits per component (duplicate prevention).
        self._min_emit_gap_seconds = max(0.05, self.interval_seconds * 0.25)
        self._cert_emit_gap_seconds = 3600.0

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="monitoring-watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[monitoring][watchdog] started (interval=%.2fs stale=%.2fs)",
            self.interval_seconds,
            heartbeat_stale_seconds(),
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._running = False
        self._thread = None
        logger.info("[monitoring][watchdog] stopped")

    def _run(self) -> None:
        if self.emit_immediately:
            self._safe_tick()
        while not self._stop.wait(timeout=self.interval_seconds):
            self._safe_tick()

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception as exc:
            logger.warning("[monitoring][watchdog] tick failed: %s", exc)

    def tick(self) -> None:
        """Emit one heartbeat cycle (also used by tests)."""
        from app.monitoring.flags import is_monitoring_enabled

        # Effective OFF: keep thread alive but do not emit or mutate health.
        if not is_monitoring_enabled():
            return
        if not self._tick_lock.acquire(blocking=False):
            # Previous tick still running — skip (duplicate prevention).
            return
        try:
            now = time.monotonic()
            self._emit_component(
                COMPONENT_API,
                status="up",
                detail={"source": "watchdog"},
                now=now,
            )
            pipeline_status, pipeline_detail = self._pipeline_heartbeat_payload()
            self._emit_component(
                COMPONENT_PIPELINE,
                status=pipeline_status,
                detail=pipeline_detail,
                now=now,
            )
            self._reconcile_stale_components(now=now)
            self._emit_cert_expiry(now=now)
        finally:
            self._tick_lock.release()

    def _pipeline_heartbeat_payload(self) -> tuple[str, dict]:
        service = self._service
        detail = {"source": "watchdog"}
        if service is None:
            return "unknown", detail
        try:
            status = service.get_runtime_status()
            detail.update(
                {
                    "queue_length": status.queue_length,
                    "degraded": status.degraded,
                    "running": status.running,
                }
            )
            if not status.running:
                return "down", detail
            return status.health_status or "up", detail
        except Exception as exc:
            detail["error"] = str(exc)
            return "degraded", detail

    def _reconcile_stale_components(self, *, now: float) -> None:
        """Persist status=down for components with expired heartbeats still marked live."""
        registry = get_registry()
        if registry is None:
            return
        stale_seconds = heartbeat_stale_seconds()
        utc_now = datetime.now(timezone.utc)
        try:
            with monitoring_session(commit=False) as session:
                storage = MonitoringStorage(session)
                rows = storage.health.get_all_health_current()
        except Exception as exc:
            logger.warning("[monitoring][watchdog] stale read failed: %s", exc)
            return

        id_to_code = {comp.id: code for code, comp in registry.components_by_code.items()}
        for row in rows:
            code = id_to_code.get(row.component_id)
            if not code or code in (COMPONENT_API, COMPONENT_PIPELINE):
                continue
            status = (row.status or "").strip().lower()
            if status not in ("up", "degraded", "starting"):
                continue
            if not is_heartbeat_stale(
                row.last_heartbeat_at, now=utc_now, stale_seconds=stale_seconds
            ):
                continue
            self._emit_component(
                code,
                status="down",
                detail={
                    "source": "watchdog_stale",
                    "stale_seconds": stale_seconds,
                    "previous_status": status,
                    "previous_heartbeat_at": (
                        row.last_heartbeat_at.isoformat()
                        if row.last_heartbeat_at is not None
                        else None
                    ),
                },
                now=now,
            )

    def _emit_cert_expiry(self, *, now: float) -> None:
        if (now - self._last_cert_emit_at) < self._cert_emit_gap_seconds:
            return
        try:
            from app.monitoring.cert_expiry import emit_cert_expiry_metrics

            emit_cert_expiry_metrics()
            self._last_cert_emit_at = now
        except Exception as exc:
            logger.warning("[monitoring][watchdog] cert expiry emit failed: %s", exc)

    def _emit_component(
        self,
        component_code: str,
        *,
        status: str,
        detail: dict,
        now: float,
    ) -> None:
        last = self._last_emit_at.get(component_code)
        if last is not None and (now - last) < self._min_emit_gap_seconds:
            return
        instrumentation.heartbeat(
            component_code,
            status,
            detail=detail,
            observed_at=datetime.now(timezone.utc),
        )
        self._last_emit_at[component_code] = now


# Process-local handle for main.py
_watchdog_singleton: Optional[MonitoringWatchdog] = None


def get_monitoring_watchdog() -> Optional[MonitoringWatchdog]:
    return _watchdog_singleton


def set_monitoring_watchdog(watchdog: Optional[MonitoringWatchdog]) -> None:
    global _watchdog_singleton
    _watchdog_singleton = watchdog
