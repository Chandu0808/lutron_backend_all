"""
Retention policy constants and cutoffs (Phase 11 helpers; Phase 13 deletes).

Current-state tables are never deleted:
mon_component_health_current, mon_processor_connectivity_current,
dimension tables (component, job_definition, metric_definition, alert_rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class RetentionPolicy:
    """Freeze-aligned retention windows."""

    ping_days: int = 7
    http_agg_days: int = 14
    events_days: int = 90
    job_run_days: int = 30
    metric_sample_days: int = 7
    alert_resolved_days: int = 90
    metric_rollup_days: int = 180
    # Analytics reprocess window (not a delete target).
    rollup_lookback_hours: int = 48
    rollup_lookback_days: int = 14
    delete_batch_size: int = 2000


DEFAULT_RETENTION = RetentionPolicy()


def lookback_timedelta(
    policy: Optional[RetentionPolicy] = None,
) -> tuple[timedelta, timedelta]:
    p = policy or DEFAULT_RETENTION
    return (
        timedelta(hours=p.rollup_lookback_hours),
        timedelta(days=p.rollup_lookback_days),
    )


def retention_cutoff(
    now: Optional[datetime] = None,
    *,
    days: int,
) -> datetime:
    """Return timestamp before which raw rows are eligible for retention."""
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts - timedelta(days=days)


def policy_from_env() -> RetentionPolicy:
    """Optional overrides via MONITORING_RETENTION_*_DAYS env vars."""
    import os

    def _days(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return max(1, int(raw))
        except ValueError:
            return default

    base = DEFAULT_RETENTION
    return RetentionPolicy(
        ping_days=_days("MONITORING_RETENTION_PING_DAYS", base.ping_days),
        http_agg_days=_days("MONITORING_RETENTION_HTTP_DAYS", base.http_agg_days),
        events_days=_days("MONITORING_RETENTION_EVENTS_DAYS", base.events_days),
        job_run_days=_days("MONITORING_RETENTION_JOB_RUN_DAYS", base.job_run_days),
        metric_sample_days=_days(
            "MONITORING_RETENTION_SAMPLES_DAYS", base.metric_sample_days
        ),
        alert_resolved_days=_days(
            "MONITORING_RETENTION_ALERT_DAYS", base.alert_resolved_days
        ),
        metric_rollup_days=_days(
            "MONITORING_RETENTION_ROLLUP_DAYS", base.metric_rollup_days
        ),
        rollup_lookback_hours=base.rollup_lookback_hours,
        rollup_lookback_days=base.rollup_lookback_days,
        delete_batch_size=base.delete_batch_size,
    )
