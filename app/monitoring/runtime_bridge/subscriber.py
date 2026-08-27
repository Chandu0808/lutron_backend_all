"""
Subscribe to Runtime Event Bus and persist via MonitoringService.

Observer only — never calls RuntimeSupervisor restart APIs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from app.monitoring.runtime_bridge.mapper import RuntimeEventMapper

if TYPE_CHECKING:
    from app.monitoring.service import MonitoringService
    from app.runtime.events import RuntimeEvent, RuntimeEventBus
    from app.runtime.supervisor import RuntimeSupervisor

logger = logging.getLogger("lutron_monitoring.runtime_bridge")

_bridge_singleton: Optional["RuntimeMonitoringSubscriber"] = None
_FOREIGN_MUTEX_RULE = "runtime_foreign_mutex"


class RuntimeMonitoringSubscriber:
    """
    Monitoring-side subscriber for the Runtime Event Bus.

    Flow: RuntimeEvent → Mapper → MonitoringService.record_runtime_event()
    ForeignMutexDetected also opens a Monitoring alert instance when possible.
    """

    def __init__(
        self,
        service: "MonitoringService",
        *,
        event_bus: Optional["RuntimeEventBus"] = None,
        supervisor: Optional["RuntimeSupervisor"] = None,
        mapper: Optional[RuntimeEventMapper] = None,
    ) -> None:
        self._service = service
        self._event_bus = event_bus
        self._supervisor = supervisor
        self._mapper = mapper or RuntimeEventMapper()
        self._attached = False
        self._received = 0
        self._persisted = 0
        self._skipped = 0
        self._alerts_opened = 0

    @property
    def attached(self) -> bool:
        return self._attached

    @property
    def supervisor(self) -> Optional["RuntimeSupervisor"]:
        return self._supervisor

    def bind_supervisor(self, supervisor: Optional["RuntimeSupervisor"]) -> None:
        self._supervisor = supervisor

    def attach(self, event_bus: "RuntimeEventBus") -> None:
        if self._attached and self._event_bus is event_bus:
            return
        if self._attached and self._event_bus is not None:
            self.detach()
        self._event_bus = event_bus
        event_bus.subscribe(self)
        self._attached = True
        logger.info("[monitoring][runtime_bridge] attached to Runtime Event Bus")

    def detach(self) -> None:
        bus = self._event_bus
        if bus is not None and self._attached:
            try:
                bus.unsubscribe(self)
            except Exception as exc:
                logger.warning(
                    "[monitoring][runtime_bridge] unsubscribe failed: %s", exc
                )
        self._attached = False
        logger.info("[monitoring][runtime_bridge] detached")

    def __call__(self, event: "RuntimeEvent") -> None:
        """RuntimeEventBus handler — never raises into the bus."""
        try:
            self._received += 1
            mapped = self._mapper.map(event)
            if mapped is None:
                self._skipped += 1
                return
            result = self._service.record_runtime_event(
                component_code=mapped.component_code,
                event_type=mapped.event_type,
                severity=mapped.severity,
                detail=mapped.detail,
                fingerprint=mapped.fingerprint,
                event_at=mapped.event_at,
            )
            if result is not None and getattr(result, "accepted", False):
                self._persisted += 1
            else:
                self._skipped += 1

            if type(event).__name__ == "ForeignMutexDetected":
                self._open_foreign_mutex_alert(event, mapped.detail)
        except Exception as exc:
            self._skipped += 1
            logger.warning(
                "[monitoring][runtime_bridge] handle failed: %s", exc
            )

    def _open_foreign_mutex_alert(
        self, event: "RuntimeEvent", detail: dict
    ) -> None:
        """Best-effort Monitoring alert for foreign mutex (M2.5)."""
        try:
            from app.monitoring.storage import MonitoringStorage
            from app.monitoring.storage.session import monitoring_session

            child = getattr(event, "child_name", None) or "energy_logger"
            attempt = getattr(event, "attempt", 0)
            fp = f"runtime_foreign_mutex:{child}"
            with monitoring_session(commit=True) as session:
                storage = MonitoringStorage(session)
                rule = storage.alerts.get_alert_rule_by_code(_FOREIGN_MUTEX_RULE)
                if rule is None:
                    logger.info(
                        "[monitoring][runtime_bridge] alert rule %s missing — "
                        "event persisted only",
                        _FOREIGN_MUTEX_RULE,
                    )
                    return
                component_id = None
                try:
                    comp = storage.components.get_component_by_code(child)
                    if comp is not None:
                        component_id = comp.id
                except Exception:
                    component_id = None
                # Dedup: skip if already open for same fingerprint
                active = storage.alerts.list_active_alerts_for_rule(rule.id)
                if any(a.fingerprint == fp for a in active):
                    return
                from datetime import datetime, timezone

                storage.alerts.open_alert(
                    rule_id=rule.id,
                    fingerprint=fp,
                    title=f"Foreign mutex holder ({child})",
                    severity=rule.severity or "warning",
                    opened_at=datetime.now(timezone.utc),
                    message=(
                        "Energy logger mutex present with no proven Runtime "
                        f"Job member (attempt={attempt}). Waiting; no terminate."
                    ),
                    component_id=component_id,
                    detail_json=detail,
                )
                self._alerts_opened += 1
                logger.warning(
                    "[monitoring][runtime_bridge] opened alert rule=%s child=%s",
                    _FOREIGN_MUTEX_RULE,
                    child,
                )
        except Exception as exc:
            logger.warning(
                "[monitoring][runtime_bridge] foreign mutex alert failed: %s",
                exc,
            )

    def stats(self) -> dict:
        return {
            "attached": self._attached,
            "received": self._received,
            "persisted": self._persisted,
            "skipped": self._skipped,
            "alerts_opened": self._alerts_opened,
        }


def start_runtime_bridge(
    *,
    service: "MonitoringService",
    event_bus: "RuntimeEventBus",
    supervisor: Optional["RuntimeSupervisor"] = None,
) -> RuntimeMonitoringSubscriber:
    """Attach (or replace) the process-local runtime monitoring bridge."""
    global _bridge_singleton
    if _bridge_singleton is not None:
        _bridge_singleton.detach()
    bridge = RuntimeMonitoringSubscriber(
        service,
        event_bus=event_bus,
        supervisor=supervisor,
    )
    bridge.attach(event_bus)
    _bridge_singleton = bridge
    return bridge


def stop_runtime_bridge() -> None:
    global _bridge_singleton
    if _bridge_singleton is not None:
        _bridge_singleton.detach()
        _bridge_singleton = None


def get_runtime_bridge() -> Optional[RuntimeMonitoringSubscriber]:
    return _bridge_singleton
