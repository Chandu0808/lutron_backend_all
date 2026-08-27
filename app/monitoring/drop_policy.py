"""
Drop / retry / degraded policy for the Monitoring Service ingress queue.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Type

from app.monitoring.events import (
    ConnectivityEvent,
    HeartbeatEvent,
    HttpAggregateEvent,
    JobRunEvent,
    LeapPingEvent,
    LifecycleEvent,
    MetricSampleEvent,
    MonitoringEvent,
)

# Lower number = higher priority (keep under backpressure).
_PRIORITY: Dict[Type, int] = {
    LifecycleEvent: 0,
    ConnectivityEvent: 1,
    HeartbeatEvent: 2,
    JobRunEvent: 2,
    HttpAggregateEvent: 3,
    MetricSampleEvent: 3,
    LeapPingEvent: 4,
}


def event_priority(event: MonitoringEvent) -> int:
    return _PRIORITY.get(type(event), 5)


def is_low_priority(event: MonitoringEvent) -> bool:
    return event_priority(event) >= 3


@dataclass
class DropCounters:
    dropped_total: int = 0
    dropped_by_reason: Dict[str, int] = field(default_factory=dict)
    write_retry_total: int = 0
    writer_error_total: int = 0

    def incr_drop(self, reason: str) -> None:
        self.dropped_total += 1
        self.dropped_by_reason[reason] = self.dropped_by_reason.get(reason, 0) + 1

    def incr_retry(self) -> None:
        self.write_retry_total += 1

    def incr_writer_error(self) -> None:
        self.writer_error_total += 1


class DropPolicy:
    """
    Queue-full and write-retry policy.

    Under backpressure, prefer dropping low-priority events (ping/metrics/http)
    over lifecycle/connectivity/heartbeat/job events.
    """

    def __init__(
        self,
        *,
        max_write_retries: int = 2,
        degrade_after_drops: int = 10,
    ) -> None:
        self.max_write_retries = max_write_retries
        self.degrade_after_drops = degrade_after_drops
        self.counters = DropCounters()
        self._lock = threading.Lock()
        self._degraded = False

    @property
    def degraded(self) -> bool:
        with self._lock:
            return self._degraded

    def mark_degraded(self, reason: str = "policy") -> None:
        with self._lock:
            self._degraded = True

    def clear_degraded(self) -> None:
        with self._lock:
            self._degraded = False

    def record_drop(self, event: MonitoringEvent, reason: str) -> None:
        with self._lock:
            self.counters.incr_drop(reason)
            if self.counters.dropped_total >= self.degrade_after_drops:
                self._degraded = True

    def record_retry(self) -> None:
        with self._lock:
            self.counters.incr_retry()

    def record_writer_error(self) -> None:
        with self._lock:
            self.counters.incr_writer_error()
            self._degraded = True

    def should_drop_incoming_when_full(self, event: MonitoringEvent) -> bool:
        """If True, drop the incoming event immediately when the queue is full."""
        return is_low_priority(event)

    def should_evict_buffered(self, buffered: MonitoringEvent, incoming: MonitoringEvent) -> bool:
        """Evict buffered event to make room for higher-priority incoming."""
        return event_priority(buffered) > event_priority(incoming)

    def snapshot_counters(self) -> Dict[str, int]:
        with self._lock:
            out = {
                "dropped_total": self.counters.dropped_total,
                "write_retry_total": self.counters.write_retry_total,
                "writer_error_total": self.counters.writer_error_total,
                "degraded": 1 if self._degraded else 0,
            }
            for reason, count in self.counters.dropped_by_reason.items():
                out[f"dropped:{reason}"] = count
            return out
