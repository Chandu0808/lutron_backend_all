"""
Evaluate a single alert rule → desired findings (Phase 10).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from app.monitoring.alerts.conditions import (
    EVALUATORS,
    SUPPORTED_RULE_TYPES,
    AlertFinding,
)
from app.monitoring.registry import RegistrySnapshot
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.types import AlertRuleRow

logger = logging.getLogger("lutron_monitoring.alerts.evaluator")


def evaluate_rule(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    """
    Evaluate one rule. Returns findings that are currently true.

    Disabled / unsupported rules return an empty list (no opens, no resolves
    driven from this call — engine skips disabled rules entirely).
    """
    if not rule.enabled:
        return []
    rule_type = (rule.rule_type or "").strip().lower()
    if rule_type not in SUPPORTED_RULE_TYPES:
        logger.info(
            "[monitoring][alerts] skip unsupported rule_type=%s code=%s",
            rule_type,
            rule.code,
        )
        return []
    fn = EVALUATORS.get(rule_type)
    if fn is None:
        return []
    try:
        return fn(storage, registry, rule, now=now)
    except Exception as exc:
        logger.warning(
            "[monitoring][alerts] evaluate failed rule=%s: %s", rule.code, exc
        )
        return []
