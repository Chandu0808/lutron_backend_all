"""
Internal Runtime Event Bus (Phase 4).

In-process, synchronous, non-persistent, thread-safe.
Not the Monitoring event system — no Instrumentation / Storage.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

logger = logging.getLogger("lutron_runtime.events")


# ---------------------------------------------------------------------------
# Event model (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeEvent:
    """Base runtime event."""

    timestamp: float = field(default_factory=time.time)

    @property
    def event_type(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class ChildStarted(RuntimeEvent):
    child_name: str = ""
    generation: int = 0
    pid: Optional[int] = None
    from_restart: bool = False


@dataclass(frozen=True)
class ChildStopped(RuntimeEvent):
    child_name: str = ""
    exitcode: Optional[int] = None
    reason: str = "stopped"


@dataclass(frozen=True)
class ChildFailed(RuntimeEvent):
    child_name: str = ""
    exitcode: Optional[int] = None
    exit_class: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class RestartRequested(RuntimeEvent):
    child_name: str = ""
    exit_class: Optional[str] = None
    reason: str = "unexpected_exit"


@dataclass(frozen=True)
class RestartScheduled(RuntimeEvent):
    child_name: str = ""
    delay_seconds: float = 0.0
    attempt_index: int = 0


@dataclass(frozen=True)
class RestartStarted(RuntimeEvent):
    child_name: str = ""
    generation_before: int = 0


@dataclass(frozen=True)
class RestartSucceeded(RuntimeEvent):
    child_name: str = ""
    generation: int = 0
    pid: Optional[int] = None


@dataclass(frozen=True)
class RestartFailed(RuntimeEvent):
    child_name: str = ""
    error: Optional[str] = None


@dataclass(frozen=True)
class BackoffEntered(RuntimeEvent):
    child_name: str = ""
    delay_seconds: float = 0.0


@dataclass(frozen=True)
class Abandoned(RuntimeEvent):
    child_name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ReconcileStarted(RuntimeEvent):
    child_name: str = ""
    attempt: int = 0
    reason: str = "lock_busy"


@dataclass(frozen=True)
class ReconcileCompleted(RuntimeEvent):
    child_name: str = ""
    attempt: int = 0
    decision: str = ""
    success: bool = False
    error: Optional[str] = None
    foreign_mutex_holder: bool = False
    reconcile_reason: Optional[str] = None
    terminate_reason: Optional[str] = None
    proof_method: Optional[str] = None
    live_job_members: tuple = ()


@dataclass(frozen=True)
class ForeignMutexDetected(RuntimeEvent):
    """Mutex present with no proven Runtime Job member (M2.5)."""

    child_name: str = ""
    attempt: int = 0
    mutex_name: str = ""
    live_job_members: tuple = ()
    reconcile_reason: str = "foreign_mutex_holder"


@dataclass(frozen=True)
class SupervisorStarted(RuntimeEvent):
    child_count: int = 0


@dataclass(frozen=True)
class SupervisorStopped(RuntimeEvent):
    child_count: int = 0


@dataclass(frozen=True)
class ServiceStarted(RuntimeEvent):
    mode: str = "windows_service"
    service_name: str = ""


@dataclass(frozen=True)
class ServiceStopping(RuntimeEvent):
    reason: str = "stop"
    mode: str = "windows_service"


@dataclass(frozen=True)
class ServiceStopped(RuntimeEvent):
    reason: str = "stopped"
    mode: str = "windows_service"


EventHandler = Callable[[RuntimeEvent], None]
EventType = Type[RuntimeEvent]


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class RuntimeEventBus:
    """
    Lightweight publish/subscribe bus.

    - Synchronous delivery on the publisher's thread
    - Handler exceptions are isolated (never raise into publisher)
    - Thread-safe subscribe / unsubscribe / publish
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (handler, frozenset of event type names or empty = all)
        self._subscribers: List[Tuple[EventHandler, frozenset]] = []

    def subscribe(
        self,
        handler: EventHandler,
        event_types: Optional[Iterable[Union[EventType, str]]] = None,
    ) -> None:
        """
        Register ``handler``.

        ``event_types`` filters by class or class name; ``None`` receives all.
        Duplicate (handler, types) registrations are allowed (called N times).
        """
        type_filter = self._normalize_types(event_types)
        with self._lock:
            self._subscribers.append((handler, type_filter))

    def unsubscribe(
        self,
        handler: EventHandler,
        event_types: Optional[Iterable[Union[EventType, str]]] = None,
    ) -> bool:
        """Remove one matching subscription. Returns True if removed."""
        type_filter = self._normalize_types(event_types)
        with self._lock:
            for i, (h, types) in enumerate(self._subscribers):
                if h is handler and types == type_filter:
                    del self._subscribers[i]
                    return True
        return False

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: RuntimeEvent) -> None:
        """Deliver ``event`` synchronously to matching subscribers."""
        with self._lock:
            snapshot = list(self._subscribers)
        event_name = type(event).__name__
        for handler, type_filter in snapshot:
            if type_filter and event_name not in type_filter:
                continue
            try:
                handler(event)
            except Exception as exc:
                logger.warning(
                    "[runtime][events] handler failed type=%s error=%s",
                    event_name,
                    exc,
                )

    @staticmethod
    def _normalize_types(
        event_types: Optional[Iterable[Union[EventType, str]]],
    ) -> frozenset:
        if event_types is None:
            return frozenset()
        names = []
        for item in event_types:
            if isinstance(item, type):
                names.append(item.__name__)
            else:
                names.append(str(item))
        return frozenset(names)


# ---------------------------------------------------------------------------
# Built-in subscribers
# ---------------------------------------------------------------------------


class LoggingSubscriber:
    """Logs every runtime event at INFO."""

    def __init__(
        self,
        *,
        log: Optional[logging.Logger] = None,
        bus: Optional[RuntimeEventBus] = None,
    ) -> None:
        self._log = log or logging.getLogger("lutron_runtime.events.logging")
        if bus is not None:
            bus.subscribe(self)

    def __call__(self, event: RuntimeEvent) -> None:
        detail = {
            k: v
            for k, v in event.__dict__.items()
            if k != "timestamp"
        }
        self._log.info(
            "[runtime][event] %s %s",
            event.event_type,
            detail,
        )


class DiagnosticsSubscriber:
    """Keeps a bounded in-memory ring of recent events."""

    def __init__(
        self,
        *,
        maxlen: int = 200,
        bus: Optional[RuntimeEventBus] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._events: Deque[RuntimeEvent] = deque(maxlen=max(1, maxlen))
        if bus is not None:
            bus.subscribe(self)

    def __call__(self, event: RuntimeEvent) -> None:
        with self._lock:
            self._events.append(event)

    def recent(self, limit: Optional[int] = None) -> List[RuntimeEvent]:
        with self._lock:
            items = list(self._events)
        if limit is None:
            return items
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def as_dicts(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        out = []
        for event in self.recent(limit):
            payload = dict(event.__dict__)
            payload["event_type"] = event.event_type
            out.append(payload)
        return out


class RecordingSubscriber:
    """Test helper: records events in order."""

    def __init__(self, bus: Optional[RuntimeEventBus] = None) -> None:
        self._lock = threading.Lock()
        self.events: List[RuntimeEvent] = []
        if bus is not None:
            bus.subscribe(self)

    def __call__(self, event: RuntimeEvent) -> None:
        with self._lock:
            self.events.append(event)

    def clear(self) -> None:
        with self._lock:
            self.events.clear()

    def types(self) -> List[str]:
        with self._lock:
            return [e.event_type for e in self.events]

    def of_type(self, event_type: Union[EventType, str]) -> List[RuntimeEvent]:
        name = event_type if isinstance(event_type, str) else event_type.__name__
        with self._lock:
            return [e for e in self.events if e.event_type == name]
