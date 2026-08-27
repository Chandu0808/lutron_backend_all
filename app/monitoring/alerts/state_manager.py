"""
Alert instance lifecycle (Phase 10).

OPEN → ACKNOWLEDGED → RESOLVED
Never reopen resolved alerts; new firings create a new OPEN instance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.monitoring.alerts.conditions import AlertFinding
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.types import AlertInstanceRow, AlertRuleRow

logger = logging.getLogger("lutron_monitoring.alerts.state")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TransitionReport:
    opened: List[AlertInstanceRow] = field(default_factory=list)
    resolved: List[AlertInstanceRow] = field(default_factory=list)
    suppressed: int = 0


class AlertStateManager:
    """Apply desired findings to mon_alert_instance via Storage only."""

    def __init__(self, storage: MonitoringStorage) -> None:
        self._storage = storage

    def apply_findings(
        self,
        rule: AlertRuleRow,
        findings: List[AlertFinding],
        *,
        now: Optional[datetime] = None,
    ) -> TransitionReport:
        now = now or _utcnow()
        report = TransitionReport()
        desired: Dict[str, AlertFinding] = {f.fingerprint: f for f in findings}
        active = self._storage.alerts.list_active_alerts_for_rule(rule.id)
        active_by_fp = {a.fingerprint: a for a in active}

        for fp, finding in desired.items():
            if fp in active_by_fp:
                report.suppressed += 1
                continue
            opened = self._storage.alerts.open_alert(
                rule_id=rule.id,
                fingerprint=fp,
                title=finding.title[:256],
                severity=rule.severity,
                opened_at=now,
                message=(finding.message[:2048] if finding.message else None),
                component_id=finding.component_id,
                processor_id=finding.processor_id,
                job_definition_id=finding.job_definition_id,
                detail_json=finding.detail,
            )
            report.opened.append(opened)

        for fp, inst in active_by_fp.items():
            if fp in desired:
                continue
            resolved = self._storage.alerts.resolve_alert(inst.id, resolved_at=now)
            report.resolved.append(resolved)

        return report

    def acknowledge(
        self,
        alert_id: int,
        *,
        user_id: int,
        acknowledged_at: Optional[datetime] = None,
    ) -> AlertInstanceRow:
        return self._storage.alerts.ack_alert(
            alert_id,
            user_id=user_id,
            acknowledged_at=acknowledged_at or _utcnow(),
        )

    def resolve(
        self,
        alert_id: int,
        *,
        resolved_at: Optional[datetime] = None,
    ) -> AlertInstanceRow:
        return self._storage.alerts.resolve_alert(
            alert_id, resolved_at=resolved_at or _utcnow()
        )
