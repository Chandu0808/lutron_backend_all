"""
HTTP remote ingest client for daemon processes (Phase 6).

Posts MonitoringEvent payloads to ``/monitoring/ingest``.
Never opens DB sessions or calls Storage.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from app.dependencies.monitoring_auth import INGEST_TOKEN_ENV, INGEST_TOKEN_HEADER
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
from app.monitoring.service import AcceptResult

logger = logging.getLogger("lutron_monitoring.remote")

DEFAULT_INGEST_URL = "http://127.0.0.1:8000/monitoring/ingest"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.5


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def event_to_ingest_payload(event: MonitoringEvent) -> Dict[str, Any]:
    """Serialize a typed event to the Phase 5 ingest JSON shape."""
    if isinstance(event, HeartbeatEvent):
        return {
            "event_type": "heartbeat",
            "component_code": event.component_code,
            "status": event.status,
            "observed_at": _dt(event.observed_at),
            "detail": event.detail,
        }
    if isinstance(event, ConnectivityEvent):
        return {
            "event_type": "connectivity",
            "processor_id": event.processor_id,
            "status": event.status,
            "connectivity_event_type": event.event_type,
            "observed_at": _dt(event.observed_at),
            "observer_component_code": event.observer_component_code,
            "detail": event.detail,
        }
    if isinstance(event, LeapPingEvent):
        return {
            "event_type": "leap_ping",
            "processor_id": event.processor_id,
            "success": event.success,
            "sampled_at": _dt(event.sampled_at),
            "rtt_ms": event.rtt_ms,
            "observer_component_code": event.observer_component_code,
            "detail": event.detail,
        }
    if isinstance(event, JobRunEvent):
        return {
            "event_type": "job_run",
            "job_key": event.job_key,
            "outcome": event.outcome,
            "started_at": _dt(event.started_at),
            "finished_at": _dt(event.finished_at),
            "duration_ms": event.duration_ms,
            "error_class": event.error_class,
            "error_message": event.error_message,
            "host_pid": event.host_pid,
            "trigger_source": event.trigger_source,
            "detail": event.detail,
        }
    if isinstance(event, HttpAggregateEvent):
        return {
            "event_type": "http_aggregate",
            "component_code": event.component_code,
            "bucket_start": _dt(event.bucket_start),
            "route_template": event.route_template,
            "method": event.method,
            "status_class": event.status_class,
            "request_count": event.request_count,
            "error_count": event.error_count,
            "sum_duration_ms": event.sum_duration_ms,
            "max_duration_ms": event.max_duration_ms,
        }
    if isinstance(event, MetricSampleEvent):
        return {
            "event_type": "metric",
            "metric_key": event.metric_key,
            "value": event.value,
            "sampled_at": _dt(event.sampled_at),
            "component_code": event.component_code,
            "processor_id": event.processor_id,
            "detail": event.detail,
        }
    if isinstance(event, LifecycleEvent):
        return {
            "event_type": "lifecycle",
            "component_code": event.component_code,
            "lifecycle_event_type": event.event_type,
            "event_at": _dt(event.event_at),
            "severity": event.severity,
            "detail": event.detail,
            "fingerprint": event.fingerprint,
        }
    raise TypeError(f"unsupported event type {type(event)!r}")


def _dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


class RemoteClient:
    """
    Best-effort HTTP client for monitoring ingest.

    Failures are logged and returned as rejected/dropped AcceptResult —
    callers must never let exceptions escape into LEAP/business loops.
    """

    def __init__(
        self,
        *,
        ingest_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        backoff_seconds: Optional[float] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        from app.installation_config import (
            get_monitoring_ingest_token,
            get_monitoring_ingest_url,
        )

        self.ingest_url = (
            (ingest_url or get_monitoring_ingest_url() or DEFAULT_INGEST_URL)
            .strip()
            or DEFAULT_INGEST_URL
        )
        self.token = (
            token
            if token is not None
            else get_monitoring_ingest_token()
        ).strip()
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _env_float("MONITORING_REMOTE_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else _env_int("MONITORING_REMOTE_RETRIES", DEFAULT_RETRIES)
        )
        self.backoff_seconds = (
            backoff_seconds
            if backoff_seconds is not None
            else _env_float("MONITORING_REMOTE_BACKOFF", DEFAULT_BACKOFF_SECONDS)
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.timeout_seconds,
            headers={INGEST_TOKEN_HEADER: self.token} if self.token else {},
        )

    @classmethod
    def from_env(cls) -> "RemoteClient":
        return cls()

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception as exc:
                logger.warning("[monitoring][remote] client close failed: %s", exc)

    def submit(self, event: MonitoringEvent) -> AcceptResult:
        """POST one event to ingest. Never raises to caller."""
        try:
            payload = event_to_ingest_payload(event)
        except Exception as exc:
            logger.warning("[monitoring][remote] serialize failed: %s", exc)
            return AcceptResult(accepted=False, rejected=True, reason="serialize_error")

        if not self.token:
            logger.warning("[monitoring][remote] ingest token not configured; drop")
            return AcceptResult(accepted=False, rejected=True, reason="token_missing")

        attempts = self.max_retries + 1
        last_reason = "unknown"
        for attempt in range(attempts):
            try:
                response = self._client.post(
                    self.ingest_url,
                    json=payload,
                    headers={INGEST_TOKEN_HEADER: self.token},
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 200:
                    body = {}
                    try:
                        body = response.json()
                    except Exception:
                        pass
                    if body.get("ok") is False and body.get("accepted_count", 0) == 0:
                        last_reason = "ingest_not_ok"
                    else:
                        return AcceptResult(accepted=True, reason="http_200")
                elif response.status_code in (401, 403):
                    logger.warning(
                        "[monitoring][remote] auth failed status=%s",
                        response.status_code,
                    )
                    return AcceptResult(
                        accepted=False, rejected=True, reason=f"http_{response.status_code}"
                    )
                elif response.status_code == 503:
                    last_reason = "http_503"
                else:
                    last_reason = f"http_{response.status_code}"
                    logger.warning(
                        "[monitoring][remote] ingest status=%s body=%s",
                        response.status_code,
                        (response.text or "")[:200],
                    )
            except httpx.TimeoutException:
                last_reason = "timeout"
                logger.warning(
                    "[monitoring][remote] timeout url=%s attempt=%s/%s",
                    self.ingest_url,
                    attempt + 1,
                    attempts,
                )
            except httpx.HTTPError as exc:
                last_reason = "http_error"
                logger.warning(
                    "[monitoring][remote] http error url=%s attempt=%s/%s err=%s",
                    self.ingest_url,
                    attempt + 1,
                    attempts,
                    exc,
                )
            except Exception as exc:
                last_reason = "unexpected"
                logger.warning("[monitoring][remote] unexpected error: %s", exc)

            if attempt + 1 < attempts:
                delay = self.backoff_seconds * (2**attempt)
                time.sleep(delay)

        return AcceptResult(accepted=False, rejected=True, reason=last_reason)
