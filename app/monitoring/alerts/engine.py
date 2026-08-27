"""
Monitoring Alert Engine — scheduled evaluation loop (Phase 10).

Reads telemetry facts via Storage; writes only mon_alert_instance.
Emits its own Instrumentation.job_run only (no other telemetry).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.monitoring.alerts.evaluator import evaluate_rule
from app.monitoring.alerts.conditions import SUPPORTED_RULE_TYPES
from app.monitoring.alerts.state_manager import AlertStateManager, TransitionReport
from app.monitoring.flags import (
    is_monitoring_alerts_enabled,
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

logger = logging.getLogger("lutron_monitoring.alerts.engine")

JOB_KEY = "monitoring_alert_engine"
_DEFAULT_INTERVAL_SECONDS = 60.0


@dataclass
class EvaluationCycleReport:
    rules_evaluated: int = 0
    rules_skipped: int = 0
    opened: int = 0
    resolved: int = 0
    suppressed: int = 0
    errors: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)


def is_alert_engine_active() -> bool:
    return is_monitoring_enabled() and is_monitoring_alerts_enabled()


def _interval_seconds() -> float:
    raw = (os.getenv("MONITORING_ALERT_EVAL_INTERVAL_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_INTERVAL_SECONDS
    except ValueError:
        return _DEFAULT_INTERVAL_SECONDS


class AlertEngine:
    """One evaluation cycle over enabled alert rules."""

    def evaluate_once(
        self,
        *,
        registry: Optional[RegistrySnapshot] = None,
        now: Optional[datetime] = None,
    ) -> EvaluationCycleReport:
        report = EvaluationCycleReport()
        now = now or datetime.now(timezone.utc)
        reg = registry or get_registry()
        if reg is None:
            try:
                with monitoring_session(commit=False) as session:
                    reg = set_registry(
                        build_registry_from_storage(MonitoringStorage(session))
                    )
            except Exception as exc:
                logger.warning("[monitoring][alerts] registry unavailable: %s", exc)
                report.errors += 1
                return report

        with monitoring_session(commit=True) as session:
            storage = MonitoringStorage(session)
            rules = storage.alerts.list_alert_rules(enabled_only=True)
            state = AlertStateManager(storage)
            for rule in rules:
                try:
                    findings = evaluate_rule(storage, reg, rule, now=now)
                    if (rule.rule_type or "").lower() not in SUPPORTED_RULE_TYPES:
                        report.rules_skipped += 1
                        continue
                    tr: TransitionReport = state.apply_findings(rule, findings, now=now)
                    report.rules_evaluated += 1
                    report.opened += len(tr.opened)
                    report.resolved += len(tr.resolved)
                    report.suppressed += tr.suppressed
                except Exception as exc:
                    report.errors += 1
                    logger.warning(
                        "[monitoring][alerts] rule %s failed: %s", rule.code, exc
                    )
        return report


def run_alert_evaluation() -> EvaluationCycleReport:
    """
    Scheduled entrypoint. Instruments job_run; never raises into the scheduler.
    """
    report = EvaluationCycleReport()
    if not is_alert_engine_active():
        return report
    try:
        from app.monitoring.job_wrapper import job_execution

        with job_execution(
            JOB_KEY,
            trigger_source="apscheduler",
            component_code="alert_engine",
            emit_start=True,
        ) as status:
            engine = AlertEngine()
            report = engine.evaluate_once()
            status.setdefault("detail", {})
            status["detail"].update(
                {
                    "rules_evaluated": report.rules_evaluated,
                    "opened": report.opened,
                    "resolved": report.resolved,
                    "suppressed": report.suppressed,
                    "errors": report.errors,
                }
            )
            if report.errors:
                status["outcome"] = "failure"
                status["error_class"] = "EvaluationErrors"
                status["error_message"] = f"{report.errors} rule evaluation error(s)"
        return report
    except Exception as exc:
        logger.warning("[monitoring][alerts] evaluation cycle failed: %s", exc)
        report.errors += 1
        return report


_engine_started = False
_lock = threading.Lock()


def start_alert_engine() -> bool:
    """
    Register APScheduler job ``monitoring_alert_engine`` (every minute).
    Returns True if scheduled. Never raises.
    """
    global _engine_started
    try:
        if not is_alert_engine_active():
            return False
        with _lock:
            if _engine_started:
                return True
            from app.scheduler import scheduler

            interval = _interval_seconds()
            scheduler.add_job(
                run_alert_evaluation,
                "interval",
                seconds=interval,
                id=JOB_KEY,
                name="Monitoring alert engine evaluation",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            _engine_started = True
            logger.info(
                "[monitoring][alerts] engine scheduled every %ss", interval
            )
            return True
    except Exception as exc:
        logger.warning("[monitoring][alerts] failed to start engine: %s", exc)
        return False


def stop_alert_engine() -> None:
    global _engine_started
    try:
        from app.scheduler import scheduler
        from apscheduler.jobstores.base import JobLookupError

        try:
            scheduler.remove_job(JOB_KEY)
        except JobLookupError:
            pass
        except Exception as exc:
            logger.warning("[monitoring][alerts] remove job failed: %s", exc)
    finally:
        with _lock:
            _engine_started = False
        logger.info("[monitoring][alerts] engine stopped")


def get_alert_engine() -> AlertEngine:
    return AlertEngine()
