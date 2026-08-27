"""
Monitoring Service — ingress queue, worker, Storage dispatch (Phase 3).

Single-writer path for telemetry facts via Storage. No Alert/Analytics writes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Any

from app.monitoring.drop_policy import DropPolicy, event_priority
from app.monitoring.events import (
    ConnectivityEvent,
    HeartbeatEvent,
    HttpAggregateEvent,
    JobRunEvent,
    LeapPingEvent,
    LifecycleEvent,
    MetricSampleEvent,
    MonitoringEvent,
    build_lifecycle,
)
from app.monitoring.registry import RegistrySnapshot, get_registry
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.exceptions import StorageError, StorageTransientError
from app.monitoring.storage.session import monitoring_session

logger = logging.getLogger("lutron_monitoring.pipeline")


@dataclass
class AcceptResult:
    accepted: bool
    dropped: bool = False
    rejected: bool = False
    reason: Optional[str] = None


@dataclass
class PipelineStatus:
    running: bool
    degraded: bool
    queue_length: int
    max_queue: int
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    counters: Dict[str, int] = field(default_factory=dict)
    health_status: str = "unknown"


class MonitoringService:
    """
    Runtime pipeline: submit → bounded queue → worker → Storage.

    Designed for a single leader/worker in the API process (Phase 3).
    """

    def __init__(
        self,
        *,
        max_queue: int = 1000,
        drop_policy: Optional[DropPolicy] = None,
        registry: Optional[RegistrySnapshot] = None,
        worker_poll_seconds: float = 0.1,
        drain_timeout_seconds: float = 5.0,
    ) -> None:
        self.max_queue = max_queue
        self.drop_policy = drop_policy or DropPolicy()
        self._registry_override = registry
        self.worker_poll_seconds = worker_poll_seconds
        self.drain_timeout_seconds = drain_timeout_seconds

        self._queue: Deque[MonitoringEvent] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._running = False

        self._last_success_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._processed_total = 0

    # ----- lifecycle -----------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop.clear()
            self._running = True
            self._worker = threading.Thread(
                target=self._run_worker,
                name="monitoring-pipeline",
                daemon=True,
            )
            self._worker.start()
        logger.info("[monitoring][pipeline] started (max_queue=%s)", self.max_queue)

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal stop, allow worker to drain, then join."""
        timeout = self.drain_timeout_seconds if timeout is None else timeout
        self._stop.set()
        with self._not_empty:
            self._not_empty.notify_all()

        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)

        with self._lock:
            # If worker did not finish draining, drop leftovers.
            while self._queue:
                leftover = self._queue.popleft()
                self.drop_policy.record_drop(leftover, "shutdown_timeout")
            self._running = False
            self._worker = None
        logger.info("[monitoring][pipeline] stopped")

    @property
    def running(self) -> bool:
        return self._running

    def get_runtime_status(self) -> PipelineStatus:
        with self._lock:
            qlen = len(self._queue)
        degraded = self.drop_policy.degraded
        if not self._running:
            health = "down"
        elif degraded:
            health = "degraded"
        else:
            health = "up"
        return PipelineStatus(
            running=self._running,
            degraded=degraded,
            queue_length=qlen,
            max_queue=self.max_queue,
            last_success_at=self._last_success_at,
            last_error=self._last_error,
            counters=self.drop_policy.snapshot_counters(),
            health_status=health,
        )

    # ----- submit --------------------------------------------------------

    def submit(self, event: MonitoringEvent) -> AcceptResult:
        if self._stop.is_set() or not self._running:
            return AcceptResult(accepted=False, rejected=True, reason="not_running")

        with self._not_empty:
            if len(self._queue) < self.max_queue:
                self._queue.append(event)
                self._not_empty.notify()
                return AcceptResult(accepted=True)

            # Queue full — apply drop policy
            if self.drop_policy.should_drop_incoming_when_full(event):
                self.drop_policy.record_drop(event, "queue_full")
                return AcceptResult(
                    accepted=False, dropped=True, reason="queue_full_drop_incoming"
                )

            evicted = self._try_evict_for(event)
            if evicted is not None:
                self.drop_policy.record_drop(evicted, "queue_full_evict")
                self._queue.append(event)
                self._not_empty.notify()
                return AcceptResult(accepted=True, reason="accepted_after_evict")

            self.drop_policy.record_drop(event, "queue_full_no_evict")
            return AcceptResult(
                accepted=False, dropped=True, reason="queue_full_drop_incoming"
            )

    def record_runtime_event(
        self,
        *,
        component_code: str,
        event_type: str,
        severity: str = "info",
        detail: Optional[Dict[str, Any]] = None,
        fingerprint: Optional[str] = None,
        event_at: Optional[datetime] = None,
    ) -> AcceptResult:
        """
        Persist a Runtime Recovery observation as a LifecycleEvent (mon_event).

        Used only by the Monitoring runtime bridge (observer). Does not
        control RuntimeSupervisor.
        """
        payload: Dict[str, Any] = dict(detail or {})
        if payload.get("category") != "runtime":
            payload["category"] = "runtime"
        try:
            event = build_lifecycle(
                component_code=component_code,
                event_type=event_type,
                severity=severity,
                detail=payload,
                fingerprint=fingerprint,
                event_at=event_at,
            )
        except Exception as exc:
            logger.warning(
                "[monitoring][pipeline] invalid runtime event: %s", exc
            )
            return AcceptResult(
                accepted=False, rejected=True, reason=f"invalid:{exc}"
            )
        return self.submit(event)

    def queue_length(self) -> int:
        with self._lock:
            return len(self._queue)

    def _try_evict_for(self, incoming: MonitoringEvent) -> Optional[MonitoringEvent]:
        """Remove one lower-priority buffered event if present. Caller holds lock."""
        if not self._queue:
            return None
        items: List[MonitoringEvent] = list(self._queue)
        incoming_pri = event_priority(incoming)
        worst_idx = None
        worst_pri = -1
        for idx, buffered in enumerate(items):
            pri = event_priority(buffered)
            if pri > incoming_pri and pri >= worst_pri:
                worst_pri = pri
                worst_idx = idx
        if worst_idx is None:
            return None
        evicted = items.pop(worst_idx)
        self._queue = deque(items)
        return evicted

    # ----- worker --------------------------------------------------------

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            event = self._wait_event()
            if event is None:
                continue
            self._process_event(event)

        # Final drain after stop signal
        while True:
            with self._lock:
                if not self._queue:
                    break
                event = self._queue.popleft()
            self._process_event(event)

    def _wait_event(self) -> Optional[MonitoringEvent]:
        with self._not_empty:
            if not self._queue:
                self._not_empty.wait(timeout=self.worker_poll_seconds)
            if not self._queue:
                return None
            return self._queue.popleft()

    def _process_event(self, event: MonitoringEvent) -> None:
        attempts = 0
        while True:
            try:
                self._dispatch(event)
                self._last_success_at = datetime.now(timezone.utc)
                self._last_error = None
                self._processed_total += 1
                return
            except StorageTransientError as exc:
                attempts += 1
                self.drop_policy.record_retry()
                if attempts > self.drop_policy.max_write_retries:
                    self.drop_policy.record_writer_error()
                    self.drop_policy.record_drop(event, "write_retry_exhausted")
                    self._last_error = str(exc)
                    logger.warning(
                        "[monitoring][pipeline] drop after retries: %s (%s)",
                        type(event).__name__,
                        exc,
                    )
                    return
                time.sleep(0.05 * attempts)
            except StorageError as exc:
                self.drop_policy.record_writer_error()
                self.drop_policy.record_drop(event, "storage_error")
                self._last_error = str(exc)
                logger.warning(
                    "[monitoring][pipeline] storage error dropping %s: %s",
                    type(event).__name__,
                    exc,
                )
                return
            except Exception as exc:
                self.drop_policy.record_writer_error()
                self.drop_policy.record_drop(event, "dispatch_error")
                self._last_error = str(exc)
                logger.warning(
                    "[monitoring][pipeline] unexpected dispatch error: %s",
                    exc,
                )
                return

    def _registry(self) -> Optional[RegistrySnapshot]:
        return self._registry_override or get_registry()

    def _dispatch(self, event: MonitoringEvent) -> None:
        registry = self._registry()
        with monitoring_session(commit=True) as session:
            storage = MonitoringStorage(session)
            if isinstance(event, HeartbeatEvent):
                self._dispatch_heartbeat(storage, registry, event)
            elif isinstance(event, ConnectivityEvent):
                self._dispatch_connectivity(storage, registry, event)
            elif isinstance(event, LeapPingEvent):
                self._dispatch_ping(storage, registry, event)
            elif isinstance(event, JobRunEvent):
                self._dispatch_job_run(storage, registry, event)
            elif isinstance(event, HttpAggregateEvent):
                self._dispatch_http(storage, registry, event)
            elif isinstance(event, MetricSampleEvent):
                self._dispatch_metric(storage, registry, event)
            elif isinstance(event, LifecycleEvent):
                self._dispatch_lifecycle(storage, registry, event)
            else:
                raise StorageError(f"unsupported event type {type(event)!r}")

    def _require_component_id(self, registry: Optional[RegistrySnapshot], code: str):
        if registry is None:
            raise StorageError("monitoring registry not loaded")
        cid = registry.component_id(code)
        if cid is None:
            raise StorageError(f"unknown component_code={code!r}")
        return cid

    def _dispatch_heartbeat(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: HeartbeatEvent,
    ) -> None:
        component_id = self._require_component_id(registry, event.component_code)
        storage.health.upsert_component_health(
            component_id=component_id,
            status=event.status,
            last_heartbeat_at=event.observed_at,
            detail_json=event.detail,
        )

    def _dispatch_connectivity(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: ConnectivityEvent,
    ) -> None:
        observer_id = None
        if event.observer_component_code:
            observer_id = self._require_component_id(
                registry, event.observer_component_code
            )
        now = event.observed_at
        last_ok = now if event.status == "up" else None
        # Stamp last_error_at only when entering a bad state from a good/unknown
        # state. Retry storms (connection_failed every 5s) must not reset the
        # outage clock or processor_leap_down (min_down_seconds) never fires.
        last_err = None
        if event.status in ("down", "degraded"):
            prev = storage.connectivity.get_connectivity_for_processor(
                event.processor_id
            )
            prev_status = ((prev.status if prev else None) or "").lower()
            if prev_status not in ("down", "degraded"):
                last_err = now
        storage.connectivity.upsert_processor_connectivity(
            processor_id=event.processor_id,
            status=event.status,
            observer_component_id=observer_id,
            last_ok_at=last_ok,
            last_error_at=last_err,
            detail_json=event.detail,
        )
        storage.events.insert_event(
            event_at=event.observed_at,
            event_type=event.event_type,
            severity="warning" if event.status != "up" else "info",
            component_id=observer_id,
            processor_id=event.processor_id,
            fingerprint=f"connectivity:{event.processor_id}:{event.event_type}",
            payload_json={"status": event.status, **event.detail},
        )

    def _dispatch_ping(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: LeapPingEvent,
    ) -> None:
        observer_id = None
        if event.observer_component_code:
            observer_id = self._require_component_id(
                registry, event.observer_component_code
            )
        storage.ping.insert_leap_ping(
            processor_id=event.processor_id,
            sampled_at=event.sampled_at,
            success=event.success,
            rtt_ms=event.rtt_ms,
            observer_component_id=observer_id,
            detail_json=event.detail,
        )

    def _dispatch_job_run(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: JobRunEvent,
    ) -> None:
        if registry is None:
            raise StorageError("monitoring registry not loaded")
        job_id = registry.job_id(event.job_key)
        if job_id is None:
            raise StorageError(f"unknown job_key={event.job_key!r}")
        outcome = (event.outcome or "").strip().lower()
        if outcome == "running":
            storage.jobs.insert_job_run(
                job_definition_id=job_id,
                started_at=event.started_at,
                finished_at=None,
                outcome="running",
                duration_ms=None,
                error_class=event.error_class,
                error_message=event.error_message,
                host_pid=event.host_pid,
                trigger_source=event.trigger_source,
                detail_json=event.detail,
            )
        else:
            # Terminal outcomes update the matching running row (one lifecycle).
            storage.jobs.complete_job_run(
                job_definition_id=job_id,
                started_at=event.started_at,
                finished_at=event.finished_at,
                outcome=outcome,
                duration_ms=event.duration_ms,
                error_class=event.error_class,
                error_message=event.error_message,
                host_pid=event.host_pid,
                trigger_source=event.trigger_source,
                detail_json=event.detail,
            )

    def _dispatch_http(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: HttpAggregateEvent,
    ) -> None:
        component_id = self._require_component_id(registry, event.component_code)
        storage.http_agg.upsert_http_agg(
            component_id=component_id,
            bucket_start=event.bucket_start,
            route_template=event.route_template,
            method=event.method,
            status_class=event.status_class,
            request_count=event.request_count,
            error_count=event.error_count,
            sum_duration_ms=event.sum_duration_ms,
            max_duration_ms=event.max_duration_ms,
        )

    def _dispatch_metric(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: MetricSampleEvent,
    ) -> None:
        if registry is None:
            raise StorageError("monitoring registry not loaded")
        metric_id = registry.metric_id(event.metric_key)
        if metric_id is None:
            raise StorageError(f"unknown metric_key={event.metric_key!r}")
        component_id = None
        if event.component_code:
            component_id = self._require_component_id(registry, event.component_code)
        storage.metrics.insert_metric_sample(
            metric_definition_id=metric_id,
            sampled_at=event.sampled_at,
            value=event.value,
            component_id=component_id,
            processor_id=event.processor_id,
        )

    def _dispatch_lifecycle(
        self,
        storage: MonitoringStorage,
        registry: Optional[RegistrySnapshot],
        event: LifecycleEvent,
    ) -> None:
        component_id = self._require_component_id(registry, event.component_code)
        storage.events.insert_event(
            event_at=event.event_at,
            event_type=event.event_type,
            severity=event.severity,
            component_id=component_id,
            fingerprint=event.fingerprint,
            payload_json=event.detail,
        )


# Process-local service handle for main.py / tests
_service_singleton: Optional[MonitoringService] = None


def get_monitoring_service() -> Optional[MonitoringService]:
    return _service_singleton


def set_monitoring_service(service: Optional[MonitoringService]) -> None:
    global _service_singleton
    _service_singleton = service
