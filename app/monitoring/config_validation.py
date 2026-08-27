"""
Monitoring configuration validation (Phase 13).

Warns on invalid / incomplete feature-flag combinations. Never raises into LMS.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional

from app.monitoring.flags import (
    is_monitoring_alerts_enabled,
    is_monitoring_analytics_enabled,
    is_monitoring_enabled,
    is_monitoring_http_metrics_enabled,
    is_monitoring_ingest_enabled,
    is_monitoring_jobs_enabled,
    is_monitoring_leap_telemetry_enabled,
)

logger = logging.getLogger("lutron_monitoring.config")


@dataclass
class ConfigValidationReport:
    ok: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False


def validate_monitoring_config() -> ConfigValidationReport:
    report = ConfigValidationReport()
    enabled = is_monitoring_enabled()

    if not enabled:
        for name, flag in (
            ("MONITORING_INGEST_ENABLED", is_monitoring_ingest_enabled()),
            ("MONITORING_LEAP_TELEMETRY", is_monitoring_leap_telemetry_enabled()),
            ("MONITORING_HTTP_METRICS_ENABLED", is_monitoring_http_metrics_enabled()),
            ("MONITORING_JOBS_ENABLED", is_monitoring_jobs_enabled()),
            ("MONITORING_ALERTS_ENABLED", is_monitoring_alerts_enabled()),
            ("MONITORING_ANALYTICS_ENABLED", is_monitoring_analytics_enabled()),
        ):
            if flag:
                report.add_warning(
                    f"{name}=true but MONITORING_ENABLED is false (feature inert)"
                )
        return report

    token = ""
    try:
        from app.installation_config import get_monitoring_ingest_token

        token = (get_monitoring_ingest_token() or "").strip()
    except Exception:
        token = (os.getenv("MONITORING_INGEST_TOKEN") or "").strip()
    if is_monitoring_ingest_enabled() and not token:
        report.add_warning(
            "MONITORING_INGEST_ENABLED without MONITORING_INGEST_TOKEN "
            "(configure installation_settings.monitoring_ingest_token; "
            "daemon ingest will fail auth)"
        )

    if is_monitoring_leap_telemetry_enabled():
        if not is_monitoring_ingest_enabled():
            report.add_warning(
                "MONITORING_LEAP_TELEMETRY requires MONITORING_INGEST_ENABLED "
                "for daemon remote path"
            )
        if not token:
            report.add_warning(
                "MONITORING_LEAP_TELEMETRY without MONITORING_INGEST_TOKEN"
            )

    if is_monitoring_alerts_enabled() and not is_monitoring_jobs_enabled():
        report.add_warning(
            "MONITORING_ALERTS_ENABLED without MONITORING_JOBS_ENABLED "
            "(alert engine job_run may not persist)"
        )

    if is_monitoring_analytics_enabled() and not is_monitoring_jobs_enabled():
        report.add_warning(
            "MONITORING_ANALYTICS_ENABLED without MONITORING_JOBS_ENABLED "
            "(rollup/retention job_run may not persist)"
        )

    try:
        from app.scheduler import scheduler

        if not getattr(scheduler, "running", False):
            if is_monitoring_alerts_enabled() or is_monitoring_analytics_enabled():
                report.add_warning(
                    "APScheduler not running; alert/analytics/retention "
                    "jobs will not fire until scheduler starts"
                )
    except Exception as exc:
        report.add_warning(f"Unable to inspect scheduler: {exc}")

    return report


def log_config_validation(
    report: Optional[ConfigValidationReport] = None,
) -> ConfigValidationReport:
    report = report or validate_monitoring_config()
    for w in report.warnings:
        logger.warning("[monitoring][config] %s", w)
    for e in report.errors:
        logger.error("[monitoring][config] %s", e)
    if report.ok and not report.warnings:
        logger.info("[monitoring][config] validation OK")
    return report
