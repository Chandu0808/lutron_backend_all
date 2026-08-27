"""
Monitoring Analytics rollup engine (Phase 11).

Incremental, idempotent hour/day rollups into mon_metric_rollup.
Reads source telemetry via Storage; writes only rollups.
Instruments itself with Instrumentation.job_run only.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.monitoring.analytics.aggregators import AggregateResult, rollup_bucket
from app.monitoring.analytics.bucket import (
    BUCKET_DAY,
    BUCKET_HOUR,
    buckets_for_size,
    complete_day_range,
    complete_hour_range,
)
from app.monitoring.analytics.retention import DEFAULT_RETENTION
from app.monitoring.flags import (
    is_monitoring_analytics_enabled,
    is_monitoring_enabled,
)
from app.monitoring.registry import (
    RegistrySnapshot,
    build_registry_from_storage,
    get_registry,
    set_registry,
)
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.session import monitoring_session

logger = logging.getLogger("lutron_monitoring.analytics.engine")

JOB_KEY = "monitoring_analytics_rollup"


@dataclass
class RollupCycleReport:
    buckets_processed: int = 0
    rows_written: int = 0
    skipped_empty: int = 0
    errors: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)


def is_analytics_engine_active() -> bool:
    return is_monitoring_enabled() and is_monitoring_analytics_enabled()


def _lookback_hours() -> int:
    raw = (os.getenv("MONITORING_ANALYTICS_LOOKBACK_HOURS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_RETENTION.rollup_lookback_hours


def _lookback_days() -> int:
    raw = (os.getenv("MONITORING_ANALYTICS_LOOKBACK_DAYS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_RETENTION.rollup_lookback_days


_cycle_lock = threading.Lock()


class RollupEngine:
    """Process completed hour and day buckets (idempotent upserts)."""

    def run_once(
        self,
        *,
        registry: Optional[RegistrySnapshot] = None,
        now: Optional[datetime] = None,
        bucket_sizes: Optional[List[str]] = None,
    ) -> RollupCycleReport:
        report = RollupCycleReport()
        now = now or datetime.now(timezone.utc)
        sizes = bucket_sizes or [BUCKET_HOUR, BUCKET_DAY]

        # Concurrent execution guard (scheduler max_instances=1 + process lock).
        if not _cycle_lock.acquire(blocking=False):
            report.detail["skipped"] = "already_running"
            return report

        try:
            reg = registry or get_registry()
            if reg is None:
                try:
                    with monitoring_session(commit=False) as session:
                        reg = set_registry(
                            build_registry_from_storage(MonitoringStorage(session))
                        )
                except Exception as exc:
                    logger.warning(
                        "[monitoring][analytics] registry unavailable: %s", exc
                    )
                    report.errors += 1
                    return report

            bucket_list: List[tuple[str, datetime]] = []
            if BUCKET_HOUR in sizes:
                start, end = complete_hour_range(now, lookback_hours=_lookback_hours())
                for b in buckets_for_size(BUCKET_HOUR, start, end):
                    bucket_list.append((BUCKET_HOUR, b))
            if BUCKET_DAY in sizes:
                start, end = complete_day_range(now, lookback_days=_lookback_days())
                for b in buckets_for_size(BUCKET_DAY, start, end):
                    bucket_list.append((BUCKET_DAY, b))

            with monitoring_session(commit=True) as session:
                storage = MonitoringStorage(session)
                for size, bucket_start in bucket_list:
                    try:
                        part: AggregateResult = rollup_bucket(
                            storage,
                            reg,
                            bucket_start=bucket_start,
                            bucket_size=size,
                        )
                        report.buckets_processed += 1
                        report.rows_written += part.rows_written
                        report.skipped_empty += part.skipped_empty
                    except Exception as exc:
                        report.errors += 1
                        logger.warning(
                            "[monitoring][analytics] bucket %s %s failed: %s",
                            size,
                            bucket_start.isoformat(),
                            exc,
                        )
            return report
        finally:
            _cycle_lock.release()


def run_analytics_rollup() -> RollupCycleReport:
    """Scheduled entrypoint. Never raises into APScheduler."""
    report = RollupCycleReport()
    if not is_analytics_engine_active():
        return report
    try:
        from app.monitoring.job_wrapper import job_execution

        with job_execution(
            JOB_KEY,
            trigger_source="apscheduler",
            component_code="analytics_engine",
            emit_start=True,
        ) as status:
            engine = RollupEngine()
            report = engine.run_once()
            status.setdefault("detail", {})
            status["detail"].update(
                {
                    "buckets_processed": report.buckets_processed,
                    "rows_written": report.rows_written,
                    "skipped_empty": report.skipped_empty,
                    "errors": report.errors,
                }
            )
            if report.errors:
                status["outcome"] = "failure"
                status["error_class"] = "RollupErrors"
                status["error_message"] = f"{report.errors} bucket error(s)"
            if report.detail.get("skipped") == "already_running":
                status["outcome"] = "skipped_overlap"
        return report
    except Exception as exc:
        logger.warning("[monitoring][analytics] rollup cycle failed: %s", exc)
        report.errors += 1
        return report


_engine_started = False
_lock = threading.Lock()


def start_analytics_engine() -> bool:
    """Schedule hourly monitoring_analytics_rollup. Never raises."""
    global _engine_started
    try:
        if not is_analytics_engine_active():
            return False
        with _lock:
            if _engine_started:
                return True
            from apscheduler.triggers.cron import CronTrigger

            from app.scheduler import scheduler

            # 5 minutes past each hour — prior hour is complete.
            scheduler.add_job(
                run_analytics_rollup,
                CronTrigger(minute=5),
                id=JOB_KEY,
                name="Monitoring analytics rollup",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _engine_started = True
            logger.info("[monitoring][analytics] rollup scheduled hourly at :05")
            return True
    except Exception as exc:
        logger.warning("[monitoring][analytics] failed to start: %s", exc)
        return False


def stop_analytics_engine() -> None:
    global _engine_started
    try:
        from apscheduler.jobstores.base import JobLookupError

        from app.scheduler import scheduler

        try:
            scheduler.remove_job(JOB_KEY)
        except JobLookupError:
            pass
        except Exception as exc:
            logger.warning("[monitoring][analytics] remove job failed: %s", exc)
    finally:
        with _lock:
            _engine_started = False
        logger.info("[monitoring][analytics] engine stopped")


def get_rollup_engine() -> RollupEngine:
    return RollupEngine()
