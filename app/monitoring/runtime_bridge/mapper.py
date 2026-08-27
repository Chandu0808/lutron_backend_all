"""
Map Runtime Event Bus events → Monitoring LifecycleEvent fields.

Produces mon_event rows with event_type prefix ``runtime.*`` and
payload ``category: runtime``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from app.runtime.events import (
    Abandoned,
    BackoffEntered,
    ChildFailed,
    ChildStarted,
    ChildStopped,
    ForeignMutexDetected,
    ReconcileCompleted,
    ReconcileStarted,
    RestartFailed,
    RestartRequested,
    RestartScheduled,
    RestartStarted,
    RestartSucceeded,
    RuntimeEvent,
    ServiceStarted,
    ServiceStopped,
    ServiceStopping,
    SupervisorStarted,
    SupervisorStopped,
)

# Components that exist in monitoring seeds (safe registry lookups).
_KNOWN_COMPONENTS: Set[str] = {
    "api",
    "listener",
    "energy_logger",
    "loadcontroller_listener",
    "scheduler",
    "monitoring_pipeline",
    "alert_engine",
    "analytics_engine",
    "database",
    "certificates",
}

RUNTIME_EVENT_PREFIX = "runtime."
RUNTIME_CATEGORY = "runtime"

_SEVERITY: Dict[str, str] = {
    "ChildFailed": "warning",
    "RestartFailed": "warning",
    "Abandoned": "error",
    "ForeignMutexDetected": "warning",
    "ServiceStopping": "info",
    "BackoffEntered": "info",
    "ReconcileCompleted": "info",
}


@dataclass(frozen=True)
class MappedRuntimeEvent:
    """Fields for MonitoringService.record_runtime_event / LifecycleEvent."""

    component_code: str
    event_type: str
    severity: str
    detail: Dict[str, Any]
    fingerprint: str
    event_at: datetime


class RuntimeEventMapper:
    """Pure mapper — no Storage / Service I/O."""

    def map(self, event: RuntimeEvent) -> Optional[MappedRuntimeEvent]:
        name = type(event).__name__
        if name not in _PERSISTED_TYPES:
            return None

        detail = self._detail_payload(event)
        detail["category"] = RUNTIME_CATEGORY
        detail["runtime_event"] = name

        component = self._component_for(event)
        severity = _SEVERITY.get(name, "info")
        event_type = f"{RUNTIME_EVENT_PREFIX}{name}"
        event_at = self._event_at(event)
        fingerprint = self._fingerprint(event, event_type)

        return MappedRuntimeEvent(
            component_code=component,
            event_type=event_type,
            severity=severity,
            detail=detail,
            fingerprint=fingerprint,
            event_at=event_at,
        )

    def _component_for(self, event: RuntimeEvent) -> str:
        child = getattr(event, "child_name", None)
        if isinstance(child, str) and child.strip():
            code = child.strip()
            if code in _KNOWN_COMPONENTS:
                return code
            return "api"
        # Supervisor / service lifecycle → api process
        return "api"

    def _detail_payload(self, event: RuntimeEvent) -> Dict[str, Any]:
        raw = asdict(event)
        # Drop noisy/redundant timestamp from payload body (kept as event_at)
        raw.pop("timestamp", None)
        # Flatten for JSON safety
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, (list, tuple)):
                out[key] = list(value)
            else:
                out[key] = str(value)
        return out

    def _event_at(self, event: RuntimeEvent) -> datetime:
        ts = getattr(event, "timestamp", None)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.now(timezone.utc)

    def _fingerprint(self, event: RuntimeEvent, event_type: str) -> str:
        child = getattr(event, "child_name", None) or "api"
        gen = getattr(event, "generation", None)
        if gen is not None:
            return f"{event_type}:{child}:g{gen}"
        return f"{event_type}:{child}:{getattr(event, 'timestamp', '')}"


_PERSISTED_TYPES = {
    "SupervisorStarted",
    "SupervisorStopped",
    "ChildStarted",
    "ChildStopped",
    "ChildFailed",
    "RestartRequested",
    "RestartScheduled",
    "RestartStarted",
    "RestartSucceeded",
    "RestartFailed",
    "BackoffEntered",
    "Abandoned",
    "ReconcileStarted",
    "ReconcileCompleted",
    "ForeignMutexDetected",
    "ServiceStarted",
    "ServiceStopping",
    "ServiceStopped",
}

# Keep imports referenced for type checkers / explicit surface
_ = (
    Abandoned,
    BackoffEntered,
    ChildFailed,
    ChildStarted,
    ChildStopped,
    ForeignMutexDetected,
    ReconcileCompleted,
    ReconcileStarted,
    RestartFailed,
    RestartRequested,
    RestartScheduled,
    RestartStarted,
    RestartSucceeded,
    ServiceStarted,
    ServiceStopped,
    ServiceStopping,
    SupervisorStarted,
    SupervisorStopped,
)
