"""
Monitoring Bootstrap — dimension seeding and registry load (Phase 2).

Does not start runtime monitoring (no Service / Instrumentation / Alerts loop).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.monitoring.registry import (
    RegistrySnapshot,
    build_registry_from_storage,
    set_registry,
)
from app.monitoring.seeds import (
    ALERT_RULE_SEEDS,
    COMPONENT_SEEDS,
    JOB_SEEDS,
    METRIC_SEEDS,
)
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.session import monitoring_session

logger = logging.getLogger("lutron_monitoring.bootstrap")


@dataclass
class BootstrapReport:
    ok: bool
    components_seeded: int = 0
    jobs_seeded: int = 0
    metrics_seeded: int = 0
    alert_rules_seeded: int = 0
    warnings: list = field(default_factory=list)
    registry: Optional[RegistrySnapshot] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "components_seeded": self.components_seeded,
            "jobs_seeded": self.jobs_seeded,
            "metrics_seeded": self.metrics_seeded,
            "alert_rules_seeded": self.alert_rules_seeded,
            "warnings": list(self.warnings),
            "error": self.error,
            "registry_counts": {
                "components": len(self.registry.components_by_code) if self.registry else 0,
                "jobs": len(self.registry.jobs_by_key) if self.registry else 0,
                "metrics": len(self.registry.metrics_by_key) if self.registry else 0,
                "alert_rules": (
                    len(self.registry.alert_rules_by_code) if self.registry else 0
                ),
            },
        }


def _enable_catalog_if_all_disabled(storage: MonitoringStorage) -> None:
    """
    Legacy freeze seeded every rule disabled. If the whole catalog is still
    off, enable it once so Application Issues can fire. Does not override a
    mixed enabled/disabled ops configuration.
    """
    try:
        catalog_codes = {seed["code"] for seed in ALERT_RULE_SEEDS}
        rules = [
            r for r in storage.alerts.list_alert_rules() if r.code in catalog_codes
        ]
        if not rules:
            return
        if any(r.enabled for r in rules):
            return
        storage.alerts.set_rules_enabled([r.code for r in rules], enabled=True)
    except Exception as exc:
        logger.warning("[monitoring][bootstrap] catalog enable skipped: %s", exc)


def ensure_dimensions(storage: MonitoringStorage) -> BootstrapReport:
    """
    Idempotently upsert seed dimensions via Storage only.

    Alert rules are seeded with enabled=True on INSERT.
    Re-seed does not overwrite an ops toggle of enabled.
    If every catalog rule is still disabled (legacy freeze), enable them once.
    """
    report = BootstrapReport(ok=True)

    for seed in COMPONENT_SEEDS:
        storage.components.upsert_component(
            code=seed["code"],
            kind=seed["kind"],
            display_name=seed["display_name"],
            is_active=True,
        )
        report.components_seeded += 1

    components = {c.code: c for c in storage.components.list_components()}

    for seed in JOB_SEEDS:
        component = components.get(seed["component_code"])
        if component is None:
            warning = (
                f"Skipping job {seed['job_key']}: missing component "
                f"{seed['component_code']}"
            )
            report.warnings.append(warning)
            logger.warning("[monitoring][bootstrap] %s", warning)
            continue
        storage.jobs.upsert_job_definition(
            job_key=seed["job_key"],
            component_id=component.id,
            display_name=seed["display_name"],
            is_active=True,
        )
        report.jobs_seeded += 1

    for seed in METRIC_SEEDS:
        storage.metrics.upsert_metric_definition(
            metric_key=seed["metric_key"],
            value_type=seed["value_type"],
            description=seed["description"],
            is_active=True,
        )
        report.metrics_seeded += 1

    for seed in ALERT_RULE_SEEDS:
        storage.alerts.upsert_alert_rule(
            code=seed["code"],
            display_name=seed["display_name"],
            severity=seed["severity"],
            rule_type=seed["rule_type"],
            enabled=bool(seed.get("enabled", True)),
            config_json=seed["config_json"],
            description=seed["description"],
        )
        report.alert_rules_seeded += 1

    _enable_catalog_if_all_disabled(storage)

    return report


def reload_registry(storage: MonitoringStorage) -> RegistrySnapshot:
    """Rebuild and cache RegistrySnapshot from Storage."""
    snapshot = build_registry_from_storage(storage)
    return set_registry(snapshot)


def bootstrap_monitoring(
    session: Optional[Session] = None,
) -> BootstrapReport:
    """
    Ensure dimensions and load registry.

    If ``session`` is omitted, opens a committing ``monitoring_session``.
    Does not create schema (call ``ensure_monitoring_schema`` separately).
    """
    try:
        if session is not None:
            storage = MonitoringStorage(session)
            report = ensure_dimensions(storage)
            report.registry = reload_registry(storage)
            return report

        with monitoring_session(commit=True) as sess:
            storage = MonitoringStorage(sess)
            report = ensure_dimensions(storage)
            report.registry = reload_registry(storage)
            return report
    except Exception as exc:
        logger.error("[monitoring][bootstrap] failed: %s", exc)
        return BootstrapReport(ok=False, error=str(exc))
