"""In-memory RegistrySnapshot for monitoring dimension lookups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
from uuid import UUID

from app.monitoring.storage.types import (
    AlertRuleRow,
    ComponentRow,
    JobDefinitionRow,
    MetricDefinitionRow,
)


@dataclass
class RegistrySnapshot:
    """Immutable-after-load maps from natural keys to dimension rows/ids."""

    components_by_code: Dict[str, ComponentRow] = field(default_factory=dict)
    jobs_by_key: Dict[str, JobDefinitionRow] = field(default_factory=dict)
    metrics_by_key: Dict[str, MetricDefinitionRow] = field(default_factory=dict)
    alert_rules_by_code: Dict[str, AlertRuleRow] = field(default_factory=dict)

    def component_id(self, code: str) -> Optional[UUID]:
        row = self.components_by_code.get(code)
        return row.id if row else None

    def job_id(self, job_key: str) -> Optional[UUID]:
        row = self.jobs_by_key.get(job_key)
        return row.id if row else None

    def metric_id(self, metric_key: str) -> Optional[UUID]:
        row = self.metrics_by_key.get(metric_key)
        return row.id if row else None

    def alert_rule_id(self, code: str) -> Optional[UUID]:
        row = self.alert_rules_by_code.get(code)
        return row.id if row else None


_registry_cache: Optional[RegistrySnapshot] = None


def get_registry() -> Optional[RegistrySnapshot]:
    """Return process-local cached registry, or None if not loaded."""
    return _registry_cache


def set_registry(snapshot: RegistrySnapshot) -> RegistrySnapshot:
    """Replace process-local registry cache atomically."""
    global _registry_cache
    _registry_cache = snapshot
    return snapshot


def clear_registry() -> None:
    """Clear cache (tests / shutdown)."""
    global _registry_cache
    _registry_cache = None


def build_registry_from_storage(storage) -> RegistrySnapshot:
    """Load dimension tables into a RegistrySnapshot (read-only)."""
    snapshot = RegistrySnapshot(
        components_by_code={c.code: c for c in storage.components.list_components()},
        jobs_by_key={j.job_key: j for j in storage.jobs.list_job_definitions()},
        metrics_by_key={
            m.metric_key: m for m in storage.metrics.list_metric_definitions()
        },
        alert_rules_by_code={r.code: r for r in storage.alerts.list_alert_rules()},
    )
    return snapshot
