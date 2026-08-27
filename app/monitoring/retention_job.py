"""
Monitoring retention cleanup job (Phase 13).

Deletes expired historical rows per RetentionPolicy. Never touches current-state
tables (health_current, connectivity_current) or dimension tables.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.monitoring.analytics.retention import (
    RetentionPolicy,
    policy_from_env,
    retention_cutoff,
)
from app.monitoring.flags import (
    is_monitoring_analytics_enabled,
    is_monitoring_enabled,
)
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.session import monitoring_session

logger = logging.getLogger("lutron_monitoring.retention")

JOB_KEY = "monitoring_retention"
_MAX_ROUNDS = 50  # safety bound per table per cycle


@dataclass
class RetentionReport:
    deleted: Dict[str, int] = field(default_factory=dict)
    errors: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)


def is_retention_active() -> bool:
    # Standards: retention runs when analytics (or dedicated) flag is on.
    return is_monitoring_enabled() and is_monitoring_analytics_enabled()


def run_retention_once(
    *,
    policy: Optional[RetentionPolicy] = None,
    now: Optional[datetime] = None,
) -> RetentionReport:
    """Execute one retention cycle (batched deletes). Never raises."""
    report = RetentionReport()
    if not is_retention_active():
        report.detail["skipped"] = "disabled"
        return report
    pol = policy or policy_from_env()
    now = now or datetime.now(timezone.utc)
    batch = pol.delete_batch_size

    targets = [
        ("mon_event", lambda s: s.events.delete_before(
            before=retention_cutoff(now, days=pol.events_days), limit=batch
        )),
        ("mon_job_run", lambda s: s.jobs.delete_runs_before(
            before=retention_cutoff(now, days=pol.job_run_days), limit=batch
        )),
        ("mon_http_request_agg", lambda s: s.http_agg.delete_before(
            before=retention_cutoff(now, days=pol.http_agg_days), limit=batch
        )),
        ("mon_metric_sample", lambda s: s.metrics.delete_samples_before(
            before=retention_cutoff(now, days=pol.metric_sample_days), limit=batch
        )),
        ("mon_leap_ping_sample", lambda s: s.ping.delete_before(
            before=retention_cutoff(now, days=pol.ping_days), limit=batch
        )),
        ("mon_alert_instance", lambda s: s.alerts.delete_resolved_before(
            before=retention_cutoff(now, days=pol.alert_resolved_days), limit=batch
        )),
        ("mon_metric_rollup", lambda s: s.metrics.delete_rollups_before(
            before=retention_cutoff(now, days=pol.metric_rollup_days), limit=batch
        )),
    ]

    try:
        with monitoring_session(commit=True) as session:
            storage = MonitoringStorage(session)
            for name, fn in targets:
                total = 0
                try:
                    for _ in range(_MAX_ROUNDS):
                        n = int(fn(storage) or 0)
                        total += n
                        if n < batch:
                            break
                except Exception as exc:
                    report.errors += 1
                    logger.warning(
                        "[monitoring][retention] delete failed for %s: %s", name, exc
                    )
                report.deleted[name] = total
    except Exception as exc:
        report.errors += 1
        logger.warning("[monitoring][retention] cycle failed: %s", exc)
    return report


def run_retention_job() -> RetentionReport:
    """Scheduled entrypoint with job_run instrumentation."""
    report = RetentionReport()
    if not is_retention_active():
        return report
    try:
        from app.monitoring.job_wrapper import job_execution

        with job_execution(
            JOB_KEY,
            trigger_source="apscheduler",
            component_code="analytics_engine",
            emit_start=True,
        ) as status:
            report = run_retention_once()
            status.setdefault("detail", {})
            status["detail"].update(
                {"deleted": report.deleted, "errors": report.errors}
            )
            if report.errors:
                status["outcome"] = "failure"
                status["error_class"] = "RetentionErrors"
                status["error_message"] = f"{report.errors} retention error(s)"
        return report
    except Exception as exc:
        logger.warning("[monitoring][retention] job failed: %s", exc)
        report.errors += 1
        return report


_started = False
_lock = threading.Lock()


def start_retention_job() -> bool:
    """Schedule daily retention (03:30 UTC). Never raises."""
    global _started
    try:
        if not is_retention_active():
            return False
        with _lock:
            if _started:
                return True
            from apscheduler.triggers.cron import CronTrigger

            from app.scheduler import scheduler

            hour = int((os.getenv("MONITORING_RETENTION_HOUR") or "3").strip() or "3")
            minute = int(
                (os.getenv("MONITORING_RETENTION_MINUTE") or "30").strip() or "30"
            )
            scheduler.add_job(
                run_retention_job,
                CronTrigger(hour=hour, minute=minute),
                id=JOB_KEY,
                name="Monitoring retention cleanup",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _started = True
            logger.info(
                "[monitoring][retention] scheduled daily at %02d:%02d", hour, minute
            )
            return True
    except Exception as exc:
        logger.warning("[monitoring][retention] failed to start: %s", exc)
        return False


def stop_retention_job() -> None:
    global _started
    try:
        from apscheduler.jobstores.base import JobLookupError

        from app.scheduler import scheduler

        try:
            scheduler.remove_job(JOB_KEY)
        except JobLookupError:
            pass
        except Exception as exc:
            logger.warning("[monitoring][retention] remove job failed: %s", exc)
    finally:
        with _lock:
            _started = False
