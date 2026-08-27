"""
Analytics aggregators — source reads → rollup upserts (Phase 11).

Writes only mon_metric_rollup via Storage. Never mutates source tables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from app.monitoring.analytics.bucket import bucket_end
from app.monitoring.registry import RegistrySnapshot
from app.monitoring.storage import MonitoringStorage

logger = logging.getLogger("lutron_monitoring.analytics.aggregators")

# Seeded metric keys for derived rollups
HTTP_REQUEST_COUNT = "analytics.http.request_count"
HTTP_AVG_LATENCY = "analytics.http.avg_latency_ms"
HTTP_MAX_LATENCY = "analytics.http.max_latency_ms"
HTTP_ERROR_RATE = "analytics.http.error_rate"
JOB_SUCCESS_COUNT = "analytics.job.success_count"
JOB_FAILURE_COUNT = "analytics.job.failure_count"
JOB_AVG_DURATION = "analytics.job.avg_duration_ms"
LEAP_AVG_RTT = "analytics.leap.avg_rtt_ms"
LEAP_MAX_RTT = "analytics.leap.max_rtt_ms"
ALERT_COUNT = "analytics.alert.count"


@dataclass
class AggregateResult:
    rows_written: int = 0
    skipped_empty: int = 0
    skipped_missing_metric: int = 0


def _require_metric(registry: RegistrySnapshot, key: str) -> Optional[UUID]:
    return registry.metric_id(key)


def _write_scalar(
    storage: MonitoringStorage,
    *,
    metric_id: UUID,
    bucket_start: datetime,
    bucket_size: str,
    value: float,
    sample_count: int = 1,
    component_id: Optional[UUID] = None,
    processor_id: Optional[int] = None,
) -> None:
    storage.metrics.upsert_metric_rollup(
        metric_definition_id=metric_id,
        bucket_start=bucket_start,
        bucket_size=bucket_size,
        sample_count=sample_count,
        sum_value=float(value) * float(sample_count),
        avg_value=float(value),
        min_value=float(value),
        max_value=float(value),
        component_id=component_id,
        processor_id=processor_id,
    )


def aggregate_http_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    result = AggregateResult()
    end = bucket_end(bucket_start, bucket_size)
    api_id = registry.component_id("api")
    rows = storage.http_agg.query_http_agg(
        component_id=api_id, since=bucket_start, until=end, limit=5000
    )
    # query uses until as bucket_start < until — good
    # Also filter in case until not supported historically - check signature
    total = sum(int(r.request_count) for r in rows)
    if total <= 0:
        result.skipped_empty += 1
        return result

    sum_dur = sum(int(r.sum_duration_ms) for r in rows)
    max_dur = max((int(r.max_duration_ms) for r in rows), default=0)
    errors = sum(
        int(r.request_count) for r in rows if (r.status_class or "").lower() == "5xx"
    )
    err_sum = sum(int(r.error_count or 0) for r in rows)
    if err_sum > errors:
        errors = err_sum
    avg_lat = sum_dur / float(total)
    err_rate = errors / float(total)

    mapping = [
        (HTTP_REQUEST_COUNT, float(total), total),
        (HTTP_AVG_LATENCY, avg_lat, total),
        (HTTP_MAX_LATENCY, float(max_dur), total),
        (HTTP_ERROR_RATE, err_rate, total),
    ]
    for key, value, count in mapping:
        mid = _require_metric(registry, key)
        if mid is None:
            result.skipped_missing_metric += 1
            continue
        _write_scalar(
            storage,
            metric_id=mid,
            bucket_start=bucket_start,
            bucket_size=bucket_size,
            value=value,
            sample_count=count,
            component_id=api_id,
        )
        result.rows_written += 1
    return result


def aggregate_jobs_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    result = AggregateResult()
    end = bucket_end(bucket_start, bucket_size)
    runs = storage.jobs.list_job_runs(since=bucket_start, limit=5000)
    # Filter until in Python (list_job_runs has since only)
    runs = [
        r
        for r in runs
        if r.started_at is not None
        and bucket_start <= _aware(r.started_at) < end
        and r.outcome in ("success", "failure")
    ]
    if not runs:
        result.skipped_empty += 1
        return result

    successes = [r for r in runs if r.outcome == "success"]
    failures = [r for r in runs if r.outcome == "failure"]
    durations = [int(r.duration_ms) for r in runs if r.duration_ms is not None]
    avg_dur = (sum(durations) / float(len(durations))) if durations else 0.0

    for key, value, count in [
        (JOB_SUCCESS_COUNT, float(len(successes)), len(runs)),
        (JOB_FAILURE_COUNT, float(len(failures)), len(runs)),
        (JOB_AVG_DURATION, avg_dur, len(durations) or 1),
    ]:
        mid = _require_metric(registry, key)
        if mid is None:
            result.skipped_missing_metric += 1
            continue
        _write_scalar(
            storage,
            metric_id=mid,
            bucket_start=bucket_start,
            bucket_size=bucket_size,
            value=value,
            sample_count=count if key != JOB_AVG_DURATION else (len(durations) or 1),
        )
        result.rows_written += 1
    return result


def _aware(ts: datetime) -> datetime:
    from app.monitoring.analytics.bucket import ensure_utc

    return ensure_utc(ts)


def aggregate_ping_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    result = AggregateResult()
    end = bucket_end(bucket_start, bucket_size)
    pings = storage.ping.list_leap_pings(since=bucket_start, limit=5000)
    pings = [
        p
        for p in pings
        if p.sampled_at is not None and bucket_start <= _aware(p.sampled_at) < end
    ]
    rtts = [int(p.rtt_ms) for p in pings if p.rtt_ms is not None]
    if not rtts:
        result.skipped_empty += 1
        return result
    avg_rtt = sum(rtts) / float(len(rtts))
    max_rtt = float(max(rtts))
    for key, value in ((LEAP_AVG_RTT, avg_rtt), (LEAP_MAX_RTT, max_rtt)):
        mid = _require_metric(registry, key)
        if mid is None:
            result.skipped_missing_metric += 1
            continue
        _write_scalar(
            storage,
            metric_id=mid,
            bucket_start=bucket_start,
            bucket_size=bucket_size,
            value=value,
            sample_count=len(rtts),
        )
        result.rows_written += 1
    return result


def aggregate_alerts_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    result = AggregateResult()
    end = bucket_end(bucket_start, bucket_size)
    alerts = storage.alerts.list_alert_instances(
        since=bucket_start, until=end, limit=5000
    )
    mid = _require_metric(registry, ALERT_COUNT)
    if mid is None:
        result.skipped_missing_metric += 1
        return result
    if not alerts:
        result.skipped_empty += 1
        # Still write zero for idempotent empty buckets? Spec says empty bucket test —
        # writing 0 is useful for dashboards; phase says "empty bucket" test.
        _write_scalar(
            storage,
            metric_id=mid,
            bucket_start=bucket_start,
            bucket_size=bucket_size,
            value=0.0,
            sample_count=0,
        )
        result.rows_written += 1
        return result
    _write_scalar(
        storage,
        metric_id=mid,
        bucket_start=bucket_start,
        bucket_size=bucket_size,
        value=float(len(alerts)),
        sample_count=len(alerts),
    )
    result.rows_written += 1
    return result


def aggregate_metric_samples_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    """
    Roll raw mon_metric_sample rows into min/avg/max for each metric_definition
    (same definition id — not analytics.* keys).
    """
    result = AggregateResult()
    end = bucket_end(bucket_start, bucket_size)
    # Skip analytics-derived keys to avoid feedback loops.
    skip_prefix = "analytics."
    definitions = [
        d
        for d in registry.metrics_by_key.values()
        if not d.metric_key.startswith(skip_prefix)
    ]
    any_data = False
    for definition in definitions:
        samples = storage.metrics.query_metric_samples(
            metric_definition_id=definition.id,
            since=bucket_start,
            until=end,
            limit=10000,
        )
        if not samples:
            continue
        any_data = True
        values = [float(s.value) for s in samples]
        storage.metrics.upsert_metric_rollup(
            metric_definition_id=definition.id,
            bucket_start=bucket_start,
            bucket_size=bucket_size,
            sample_count=len(values),
            sum_value=sum(values),
            avg_value=sum(values) / float(len(values)),
            min_value=min(values),
            max_value=max(values),
        )
        result.rows_written += 1
    if not any_data:
        result.skipped_empty += 1
    return result


def rollup_bucket(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    *,
    bucket_start: datetime,
    bucket_size: str,
) -> AggregateResult:
    """Run all aggregators for one bucket."""
    total = AggregateResult()
    for fn in (
        aggregate_http_bucket,
        aggregate_jobs_bucket,
        aggregate_ping_bucket,
        aggregate_alerts_bucket,
        aggregate_metric_samples_bucket,
    ):
        try:
            part = fn(
                storage,
                registry,
                bucket_start=bucket_start,
                bucket_size=bucket_size,
            )
            total.rows_written += part.rows_written
            total.skipped_empty += part.skipped_empty
            total.skipped_missing_metric += part.skipped_missing_metric
        except Exception as exc:
            logger.warning(
                "[monitoring][analytics] aggregator %s failed for %s/%s: %s",
                fn.__name__,
                bucket_size,
                bucket_start.isoformat(),
                exc,
            )
    return total
