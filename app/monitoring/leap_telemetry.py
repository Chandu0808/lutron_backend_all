"""
LEAP connectivity / ping telemetry bridge (Phase 7).

Queues observational events onto a background worker that calls Instrumentation.
Never blocks the LEAP asyncio receive/reconnect loop.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.monitoring import instrumentation
from app.monitoring.flags import (
    is_monitoring_enabled,
    is_monitoring_ingest_enabled,
    is_monitoring_leap_telemetry_enabled,
)

logger = logging.getLogger("lutron_monitoring.leap_telemetry")

OBSERVER_COMPONENT = "listener"
_DEFAULT_SUPPRESS_SECONDS = 5.0
_PING_TIMEOUT_SECONDS = 10.0


@dataclass
class _ConnectivityItem:
    processor_id: int
    status: str
    event_type: str
    detail: Dict[str, Any]


@dataclass
class _PingItem:
    processor_id: int
    success: bool
    rtt_ms: Optional[int]
    detail: Dict[str, Any]


class LeapTelemetryBridge:
    """
    Background queue → Instrumentation.connectivity / leap_ping.

    Safe to call ``report_*`` from the LEAP asyncio thread (non-blocking put).
    """

    def __init__(
        self,
        *,
        suppress_seconds: float = _DEFAULT_SUPPRESS_SECONDS,
        max_queue: int = 500,
    ) -> None:
        self.suppress_seconds = suppress_seconds
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # (processor_id, event_type) -> (status, monotonic_ts)
        self._last_connectivity: Dict[Tuple[int, str], Tuple[str, float]] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="monitoring-leap-telemetry",
            daemon=True,
        )
        self._thread.start()
        logger.info("[monitoring][leap] telemetry bridge started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("[monitoring][leap] telemetry bridge stopped")

    def report_connectivity(
        self,
        processor_id: int,
        *,
        status: str,
        event_type: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Enqueue connectivity event. Returns False if suppressed/disabled/full."""
        if not self.running:
            return False
        if not self._should_emit_connectivity(processor_id, status, event_type):
            return False
        item = _ConnectivityItem(
            processor_id=processor_id,
            status=status,
            event_type=event_type,
            detail=detail or {},
        )
        return self._enqueue(item)

    def report_leap_ping(
        self,
        processor_id: int,
        *,
        success: bool,
        rtt_ms: Optional[int] = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.running:
            return False
        item = _PingItem(
            processor_id=processor_id,
            success=success,
            rtt_ms=rtt_ms,
            detail=detail or {},
        )
        return self._enqueue(item)

    def _should_emit_connectivity(
        self, processor_id: int, status: str, event_type: str
    ) -> bool:
        key = (processor_id, event_type)
        now = time.monotonic()
        with self._lock:
            prev = self._last_connectivity.get(key)
            if (
                prev is not None
                and prev[0] == status
                and (now - prev[1]) < self.suppress_seconds
            ):
                return False
            self._last_connectivity[key] = (status, now)
            return True

    def _enqueue(self, item: Any) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            logger.warning("[monitoring][leap] telemetry queue full; drop %s", type(item).__name__)
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                continue
            try:
                self._dispatch(item)
            except Exception as exc:
                logger.warning("[monitoring][leap] dispatch failed: %s", exc)

    def _dispatch(self, item: Any) -> None:
        if isinstance(item, _ConnectivityItem):
            instrumentation.connectivity(
                item.processor_id,
                item.status,
                item.event_type,
                observer_component_code=OBSERVER_COMPONENT,
                detail=item.detail,
            )
        elif isinstance(item, _PingItem):
            instrumentation.leap_ping(
                item.processor_id,
                item.success,
                rtt_ms=item.rtt_ms,
                observer_component_code=OBSERVER_COMPONENT,
                detail=item.detail,
            )


_bridge: Optional[LeapTelemetryBridge] = None
_owned_remote = None  # RemoteClient created solely for LEAP telemetry


def is_leap_telemetry_active() -> bool:
    return (
        is_monitoring_enabled()
        and is_monitoring_ingest_enabled()
        and is_monitoring_leap_telemetry_enabled()
    )


def start_leap_telemetry() -> Optional[LeapTelemetryBridge]:
    """Start bridge when LEAP telemetry flags are on. Never raises."""
    global _bridge, _owned_remote
    try:
        if not is_leap_telemetry_active():
            return None
        if _bridge is not None and _bridge.running:
            return _bridge

        # Ensure Instrumentation can submit (remote or local service).
        if (
            instrumentation.get_attached_remote() is None
            and instrumentation.get_attached_service() is None
        ):
            from app.monitoring.remote_client import RemoteClient

            remote = RemoteClient.from_env()
            if not remote.token:
                logger.warning(
                    "[monitoring][leap] ingest token missing; skip LEAP telemetry"
                )
                remote.close()
                return None
            instrumentation.attach_remote(remote)
            _owned_remote = remote

        bridge = LeapTelemetryBridge()
        bridge.start()
        _bridge = bridge
        return bridge
    except Exception as exc:
        logger.warning("[monitoring][leap] failed to start telemetry: %s", exc)
        return None


def stop_leap_telemetry(timeout: float = 5.0) -> None:
    global _bridge, _owned_remote
    bridge = _bridge
    _bridge = None
    if bridge is not None:
        try:
            bridge.stop(timeout=timeout)
        except Exception as exc:
            logger.warning("[monitoring][leap] stop failed: %s", exc)
    owned = _owned_remote
    _owned_remote = None
    if owned is not None:
        try:
            instrumentation.detach_remote()
            owned.close()
        except Exception as exc:
            logger.warning("[monitoring][leap] remote cleanup failed: %s", exc)


def get_leap_telemetry() -> Optional[LeapTelemetryBridge]:
    return _bridge


def report_connectivity(
    processor_id: int,
    *,
    status: str,
    event_type: str,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    bridge = _bridge
    if bridge is None:
        return
    try:
        bridge.report_connectivity(
            processor_id, status=status, event_type=event_type, detail=detail
        )
    except Exception:
        pass


def report_leap_ping(
    processor_id: int,
    *,
    success: bool,
    rtt_ms: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    bridge = _bridge
    if bridge is None:
        return
    try:
        bridge.report_leap_ping(
            processor_id, success=success, rtt_ms=rtt_ms, detail=detail
        )
    except Exception:
        pass


def ping_timeout_seconds() -> float:
    return _PING_TIMEOUT_SECONDS
