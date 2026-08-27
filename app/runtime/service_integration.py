"""
Windows Service integration (Phase 5).

Detects console vs Windows Service execution and publishes service lifecycle
events on the Runtime Event Bus. Does NOT contain RuntimeSupervisor restart
logic — Windows Service Manager owns API process recovery.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
from enum import Enum
from typing import Callable, Optional

from app.runtime.events import (
    RuntimeEventBus,
    ServiceStarted,
    ServiceStopped,
    ServiceStopping,
)

logger = logging.getLogger("lutron_runtime.service")

ENV_FEATURE = "RUNTIME_WINDOWS_SERVICE_ENABLED"
ENV_RUNNING = "RUNTIME_RUNNING_AS_SERVICE"
ENV_SERVICE_NAME = "RUNTIME_WINDOWS_SERVICE_NAME"
DEFAULT_SERVICE_NAME = "LutronLMSBackend"


def _env_truthy(name: str) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


class ExecutionMode(str, Enum):
    CONSOLE = "console"
    WINDOWS_SERVICE = "windows_service"


class ServiceIntegration:
    """
    Service abstraction for the API host process.

    Public API:
      is_service() / running_as_service()
      prepare_startup() / prepare_shutdown()
    """

    def __init__(
        self,
        *,
        event_bus: Optional[RuntimeEventBus] = None,
        service_name: Optional[str] = None,
    ) -> None:
        self._event_bus = event_bus
        self._service_name = (
            (service_name or os.getenv(ENV_SERVICE_NAME) or DEFAULT_SERVICE_NAME)
            .strip()
            or DEFAULT_SERVICE_NAME
        )
        self._startup_prepared = False
        self._shutdown_prepared = False
        self._shutdown_completed = False
        self._signals_installed = False
        self._stop_callback: Optional[Callable[[str], None]] = None
        self._lock = threading.Lock()

    # ----- detection -----

    def feature_enabled(self) -> bool:
        """True when RUNTIME_WINDOWS_SERVICE_ENABLED is set (packaging intent)."""
        return _env_truthy(ENV_FEATURE)

    def running_as_service(self) -> bool:
        """
        True when this process is executing under a Windows Service host.

        Primary signal: RUNTIME_RUNNING_AS_SERVICE (set by install script).
        Optional heuristic: Windows + session 0 (services often run in session 0).
        """
        if _env_truthy(ENV_RUNNING):
            return True
        if sys.platform.startswith("win") and self.feature_enabled():
            try:
                # Session 0 is typical for services; interactive consoles are > 0.
                import ctypes

                pid = os.getpid()
                session_id = ctypes.c_ulong()
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                if kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id)):
                    if session_id.value == 0:
                        return True
            except Exception:
                pass
        return False

    def is_service(self) -> bool:
        """Alias for running_as_service() — execution mode is Windows Service."""
        return self.running_as_service()

    def execution_mode(self) -> ExecutionMode:
        if self.running_as_service():
            return ExecutionMode.WINDOWS_SERVICE
        return ExecutionMode.CONSOLE

    @property
    def service_name(self) -> str:
        return self._service_name

    # ----- lifecycle hooks (no supervisor logic) -----

    def prepare_startup(self) -> None:
        """
        Called at application startup.

        Publishes ServiceStarted when running as a Windows Service.
        Installs console/service stop signal handlers when a stop callback is set.
        """
        with self._lock:
            if self._startup_prepared:
                return
            self._startup_prepared = True

        mode = self.execution_mode()
        logger.info(
            "[runtime][service] prepare_startup mode=%s feature_enabled=%s "
            "service_name=%s",
            mode.value,
            self.feature_enabled(),
            self._service_name,
        )
        if mode == ExecutionMode.WINDOWS_SERVICE:
            self._emit(
                ServiceStarted(
                    mode=mode.value,
                    service_name=self._service_name,
                )
            )
        self._install_signal_handlers()

    def prepare_shutdown(self, reason: str = "shutdown") -> None:
        """
        Begin graceful shutdown: publish ServiceStopping.

        Callers must stop HealthMonitor / RuntimeSupervisor / children, then
        call ``finalize_shutdown``.
        """
        with self._lock:
            if self._shutdown_prepared:
                return
            self._shutdown_prepared = True

        mode = self.execution_mode()
        logger.info(
            "[runtime][service] prepare_shutdown mode=%s reason=%s",
            mode.value,
            reason,
        )
        # Always publish stopping/stopped on the shutdown path (mode in payload).
        self._emit(
            ServiceStopping(
                reason=reason,
                mode=mode.value,
            )
        )

    def finalize_shutdown(self, reason: str = "stopped") -> None:
        """Publish ServiceStopped after children have been joined."""
        with self._lock:
            if self._shutdown_completed:
                return
            self._shutdown_completed = True

        mode = self.execution_mode()
        self._emit(
            ServiceStopped(
                reason=reason,
                mode=mode.value,
            )
        )
        logger.info(
            "[runtime][service] finalize_shutdown mode=%s reason=%s",
            mode.value,
            reason,
        )

    def set_stop_callback(self, callback: Callable[[str], None]) -> None:
        """Optional callback invoked on SIGINT/SIGTERM (reason string)."""
        self._stop_callback = callback

    def _install_signal_handlers(self) -> None:
        if self._signals_installed:
            return
        self._signals_installed = True

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)

                def _make_handler(sig_label: str, prev: object):
                    def _handler(signum: int, frame: object) -> None:
                        logger.info("[runtime][service] received %s", sig_label)
                        cb = self._stop_callback
                        if cb is not None:
                            try:
                                cb(sig_label)
                            except Exception as exc:
                                logger.warning(
                                    "[runtime][service] stop callback failed: %s",
                                    exc,
                                )
                        if callable(prev) and prev not in (
                            signal.SIG_DFL,
                            signal.SIG_IGN,
                        ):
                            prev(signum, frame)

                    return _handler

                signal.signal(sig, _make_handler(sig_name, previous))
            except Exception as exc:
                logger.debug(
                    "[runtime][service] cannot install %s: %s", sig_name, exc
                )

        atexit.register(self._atexit_hook)

    def _atexit_hook(self) -> None:
        if not self._shutdown_prepared:
            try:
                self.prepare_shutdown(reason="atexit")
            except Exception:
                pass
        if not self._shutdown_completed:
            try:
                self.finalize_shutdown(reason="atexit")
            except Exception:
                pass

    def _emit(self, event: object) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            bus.publish(event)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("[runtime][service] event publish failed: %s", exc)


def is_windows_service_enabled() -> bool:
    """Module-level helper for RUNTIME_WINDOWS_SERVICE_ENABLED (default false)."""
    return _env_truthy(ENV_FEATURE)
