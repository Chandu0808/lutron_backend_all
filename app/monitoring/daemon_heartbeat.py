"""
Daemon heartbeat helper (Phase 6).

Starts a background timer that emits Instrumentation heartbeats for one
component. In daemon processes Instrumentation is attached to RemoteClient.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from app.monitoring import instrumentation
from app.monitoring.flags import is_monitoring_enabled, is_monitoring_ingest_enabled
from app.monitoring.remote_client import RemoteClient
from app.monitoring.watchdog import _heartbeat_interval_seconds

logger = logging.getLogger("lutron_monitoring.daemon_heartbeat")


@dataclass
class DaemonMonitoringHandle:
    """Owns remote client + heartbeat thread for a daemon process."""

    component_code: str
    client: RemoteClient
    _stop: threading.Event
    _thread: Optional[threading.Thread]
    interval_seconds: float

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        try:
            instrumentation.detach_remote()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception as exc:
            logger.warning("[monitoring][daemon] client close failed: %s", exc)
        logger.info(
            "[monitoring][daemon] heartbeat stopped component=%s",
            self.component_code,
        )


def start_daemon_heartbeat(
    component_code: str,
    *,
    interval_seconds: Optional[float] = None,
    client: Optional[RemoteClient] = None,
) -> Optional[DaemonMonitoringHandle]:
    """
    Start remote heartbeat emission when ingest monitoring flags are enabled.

    Returns None when disabled or misconfigured — never raises.
    """
    try:
        if not is_monitoring_enabled() or not is_monitoring_ingest_enabled():
            return None

        remote = client or RemoteClient.from_env()
        if not remote.token:
            logger.warning(
                "[monitoring][daemon] MONITORING_INGEST_TOKEN missing; "
                "skip heartbeat for %s",
                component_code,
            )
            remote.close()
            return None

        interval = (
            interval_seconds
            if interval_seconds is not None
            else _heartbeat_interval_seconds()
        )
        stop = threading.Event()

        def _loop() -> None:
            # Immediate first beat
            _emit(component_code)
            while not stop.wait(timeout=interval):
                _emit(component_code)

        thread = threading.Thread(
            target=_loop,
            name=f"monitoring-hb-{component_code}",
            daemon=True,
        )
        instrumentation.attach_remote(remote)
        thread.start()
        handle = DaemonMonitoringHandle(
            component_code=component_code,
            client=remote,
            _stop=stop,
            _thread=thread,
            interval_seconds=interval,
        )
        logger.info(
            "[monitoring][daemon] heartbeat started component=%s interval=%.2fs url=%s",
            component_code,
            interval,
            remote.ingest_url,
        )
        return handle
    except Exception as exc:
        logger.warning(
            "[monitoring][daemon] failed to start heartbeat for %s: %s",
            component_code,
            exc,
        )
        return None


def _emit(component_code: str) -> None:
    try:
        instrumentation.heartbeat(
            component_code,
            "up",
            detail={"source": "daemon_heartbeat"},
        )
    except Exception as exc:
        logger.warning(
            "[monitoring][daemon] heartbeat emit failed component=%s err=%s",
            component_code,
            exc,
        )
