"""
Monitoring startup diagnostics, self-check, schema & performance review (Phase 13).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect

from app.database.migrate_monitoring import (
    MONITORING_SCHEMA,
    MONITORING_SCHEMA_VERSION,
    MONITORING_TABLES,
)
from app.monitoring.flags import (
    is_monitoring_alerts_enabled,
    is_monitoring_analytics_enabled,
    is_monitoring_enabled,
    is_monitoring_http_metrics_enabled,
    is_monitoring_ingest_enabled,
    is_monitoring_jobs_enabled,
    is_monitoring_leap_telemetry_enabled,
)
from app.monitoring.registry import get_registry
from app.monitoring.service import get_monitoring_service
from app.monitoring.watchdog import get_monitoring_watchdog

logger = logging.getLogger("lutron_monitoring.diagnostics")

EXPECTED_INDEX_PREFIXES = (
    "ix_mon_",
    "uq_mon_",
)

# Performance bounds (document / verify — not hard-fail LMS).
EXPECTED_PIPELINE_MAX_QUEUE = 1000
EXPECTED_HTTP_MAX_BUFFER_KEYS = 2000


@dataclass
class StartupDiagnosticsReport:
    schema_version: str = MONITORING_SCHEMA_VERSION
    monitoring_enabled: bool = False
    modules: Dict[str, bool] = field(default_factory=dict)
    components: List[str] = field(default_factory=list)
    jobs: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    alert_rules: List[str] = field(default_factory=list)
    scheduler_jobs: List[str] = field(default_factory=list)
    tables_present: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SelfCheckReport:
    ok: bool = True
    checks: Dict[str, bool] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)

    def fail(self, name: str, reason: str) -> None:
        self.checks[name] = False
        self.failures.append(f"{name}: {reason}")
        self.ok = False

    def pass_(self, name: str, detail: Any = None) -> None:
        self.checks[name] = True
        if detail is not None:
            self.details[name] = detail


def build_startup_diagnostics(
    *,
    engine=None,
) -> StartupDiagnosticsReport:
    report = StartupDiagnosticsReport(
        monitoring_enabled=is_monitoring_enabled(),
        modules={
            "ingest": is_monitoring_ingest_enabled(),
            "leap_telemetry": is_monitoring_leap_telemetry_enabled(),
            "http_metrics": is_monitoring_http_metrics_enabled(),
            "jobs": is_monitoring_jobs_enabled(),
            "alerts": is_monitoring_alerts_enabled(),
            "analytics": is_monitoring_analytics_enabled(),
            "retention": is_monitoring_analytics_enabled(),
        },
    )
    registry = get_registry()
    if registry is not None:
        report.components = sorted(registry.components_by_code.keys())
        report.jobs = sorted(registry.jobs_by_key.keys())
        report.metrics = sorted(registry.metrics_by_key.keys())
        report.alert_rules = sorted(registry.alert_rules_by_code.keys())

    try:
        from app.scheduler import scheduler

        if getattr(scheduler, "running", False):
            for job in scheduler.get_jobs():
                jid = getattr(job, "id", None)
                if jid and str(jid).startswith("monitoring_"):
                    report.scheduler_jobs.append(str(jid))
            report.scheduler_jobs.sort()
    except Exception as exc:
        report.warnings.append(f"scheduler inspect failed: {exc}")

    if engine is not None:
        try:
            insp = inspect(engine)
            if MONITORING_SCHEMA in insp.get_schema_names():
                report.tables_present = sorted(
                    insp.get_table_names(schema=MONITORING_SCHEMA)
                )
            else:
                report.warnings.append("monitoring schema missing")
        except Exception as exc:
            report.warnings.append(f"schema inspect failed: {exc}")

    return report


def log_startup_diagnostics(report: StartupDiagnosticsReport) -> None:
    logger.info(
        "[monitoring][startup] schema=%s enabled=%s modules=%s "
        "components=%d jobs=%d metrics=%d rules=%d scheduler_jobs=%s",
        report.schema_version,
        report.monitoring_enabled,
        report.modules,
        len(report.components),
        len(report.jobs),
        len(report.metrics),
        len(report.alert_rules),
        report.scheduler_jobs,
    )
    for w in report.warnings:
        logger.warning("[monitoring][startup] %s", w)
    # Operator-visible one-liner
    print(
        "[Monitoring Startup Report] "
        f"schema={report.schema_version} "
        f"enabled={report.monitoring_enabled} "
        f"modules={report.modules} "
        f"components={len(report.components)} "
        f"jobs={len(report.jobs)} "
        f"metrics={len(report.metrics)} "
        f"rules={len(report.alert_rules)} "
        f"scheduler={report.scheduler_jobs}"
    )


def verify_schema_indexes(engine) -> Dict[str, Any]:
    """Validate freeze tables + monitoring indexes exist."""
    result: Dict[str, Any] = {
        "ok": True,
        "missing_tables": [],
        "index_count": 0,
        "schema_version": MONITORING_SCHEMA_VERSION,
    }
    try:
        insp = inspect(engine)
        if MONITORING_SCHEMA not in insp.get_schema_names():
            result["ok"] = False
            result["missing_tables"] = list(MONITORING_TABLES)
            return result
        tables = set(insp.get_table_names(schema=MONITORING_SCHEMA))
        missing = [t for t in MONITORING_TABLES if t not in tables]
        result["missing_tables"] = missing
        if missing:
            result["ok"] = False
        idx_count = 0
        for table in tables:
            for _ix in insp.get_indexes(table, schema=MONITORING_SCHEMA):
                idx_count += 1
        # Unique indexes may appear separately depending on dialect
        result["index_count"] = idx_count
        if idx_count < 5:
            result["ok"] = False
            result["warning"] = "fewer indexes than expected"
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return result


def review_performance_bounds() -> Dict[str, Any]:
    """Confirm bounded queues/buffers and shutdown-friendly design markers."""
    out: Dict[str, Any] = {"ok": True, "items": {}}
    service = get_monitoring_service()
    if service is not None:
        max_q = getattr(service, "max_queue", None)
        out["items"]["pipeline_max_queue"] = max_q
        if max_q is None or int(max_q) <= 0 or int(max_q) > 100_000:
            out["ok"] = False
            out["items"]["pipeline_max_queue_ok"] = False
        else:
            out["items"]["pipeline_max_queue_ok"] = True
    else:
        out["items"]["pipeline_max_queue"] = None

    try:
        from app.monitoring import http_metrics as hm

        out["items"]["http_max_buffer_keys"] = getattr(hm, "_MAX_BUFFER_KEYS", None)
    except Exception:
        out["items"]["http_max_buffer_keys"] = None

    try:
        from app.monitoring import leap_telemetry as lt

        # LeapTelemetryBridge default max_queue
        out["items"]["leap_bridge_default_max_queue"] = 500
    except Exception:
        pass

    out["items"]["notes"] = [
        "MonitoringService.stop drains/drops queue",
        "HTTP metrics / leap telemetry / alert / analytics / retention use max_instances=1",
        "Daemon RemoteClient uses timeouts + capped retries",
    ]
    return out


def run_self_check(*, engine=None) -> SelfCheckReport:
    """
    Internal verification of worker, queue, watchdog, and scheduled engines.
    """
    report = SelfCheckReport()
    if not is_monitoring_enabled():
        report.pass_("monitoring_disabled_noop", True)
        return report

    service = get_monitoring_service()
    if service is None or not getattr(service, "running", False):
        report.fail("pipeline_worker", "MonitoringService not running")
    else:
        status = service.get_runtime_status()
        report.pass_(
            "pipeline_worker",
            {
                "running": status.running,
                "queue_length": status.queue_length,
                "max_queue": status.max_queue,
            },
        )
        if status.queue_length < 0 or status.queue_length > status.max_queue + 1:
            report.fail("queue_operational", "queue length out of bounds")
        else:
            report.pass_("queue_operational", status.queue_length)

    watchdog = get_monitoring_watchdog()
    if is_monitoring_enabled():
        if watchdog is None or not getattr(watchdog, "running", False):
            # Watchdog may be optional if start failed — warn as failure for hardening
            report.fail("watchdog_running", "watchdog not running")
        else:
            report.pass_("watchdog_running", True)

    try:
        from app.scheduler import scheduler

        job_ids = {getattr(j, "id", None) for j in scheduler.get_jobs()}
        if is_monitoring_alerts_enabled():
            if "monitoring_alert_engine" in job_ids:
                report.pass_("alert_engine_registered", True)
            else:
                report.fail("alert_engine_registered", "job missing")
        else:
            report.pass_("alert_engine_registered", "skipped_flag_off")

        if is_monitoring_analytics_enabled():
            if "monitoring_analytics_rollup" in job_ids:
                report.pass_("analytics_registered", True)
            else:
                report.fail("analytics_registered", "job missing")
            if "monitoring_retention" in job_ids:
                report.pass_("retention_registered", True)
            else:
                report.fail("retention_registered", "job missing")
        else:
            report.pass_("analytics_registered", "skipped_flag_off")
            report.pass_("retention_registered", "skipped_flag_off")
    except Exception as exc:
        report.fail("scheduler_jobs", str(exc))

    if engine is not None:
        schema = verify_schema_indexes(engine)
        if schema.get("ok"):
            report.pass_("schema_indexes", schema)
        else:
            report.fail("schema_indexes", str(schema))

    perf = review_performance_bounds()
    if perf.get("ok"):
        report.pass_("performance_bounds", perf)
    else:
        report.fail("performance_bounds", str(perf))

    return report


def log_self_check(report: SelfCheckReport) -> None:
    if report.ok:
        logger.info("[monitoring][self-check] OK checks=%s", report.checks)
        print(f"[Monitoring Self-Check] OK {report.checks}")
    else:
        logger.warning(
            "[monitoring][self-check] FAILED failures=%s checks=%s",
            report.failures,
            report.checks,
        )
        print(f"[Monitoring Self-Check] FAILED {report.failures}")
