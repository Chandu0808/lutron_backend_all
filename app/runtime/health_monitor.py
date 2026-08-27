"""
Health monitor thread — detection only (Phase 3).

Never evaluates policy, never calculates backoff, never restarts children.
Delegates each tick to RuntimeSupervisor.handle_monitor_tick().
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("lutron_runtime.health_monitor")

DEFAULT_INTERVAL_SECONDS = 2.0


class HealthMonitor:
    """
    Single lightweight detection thread owned by RuntimeSupervisor.

    Every ``interval_seconds`` invokes ``on_tick`` (supervisor handler).
    """

    def __init__(
        self,
        *,
        on_tick: Callable[[], None],
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            interval_seconds = DEFAULT_INTERVAL_SECONDS
        self._on_tick = on_tick
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="runtime-health-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[runtime] health monitor started interval=%.2fs",
            self._interval,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("[runtime] health monitor stopped")

    def tick_once(self) -> None:
        """Run one detection cycle (tests / harness)."""
        self._safe_tick()

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._interval):
            self._safe_tick()

    def _safe_tick(self) -> None:
        try:
            self._on_tick()
        except Exception as exc:
            logger.warning("[runtime] health monitor tick failed: %s", exc)
