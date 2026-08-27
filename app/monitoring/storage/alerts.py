"""Repository for alert rules and alert instances."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import (
    as_json_dict,
    as_optional_uuid,
    as_uuid,
    mapping_row,
    mapping_rows,
    run_write,
)
from app.monitoring.storage.exceptions import StorageNotFoundError
from app.monitoring.storage.types import AlertInstanceRow, AlertRuleRow


def _rule_row(m) -> AlertRuleRow:
    return AlertRuleRow(
        id=as_uuid(m["id"]),
        code=m["code"],
        display_name=m["display_name"],
        severity=m["severity"],
        rule_type=m["rule_type"],
        enabled=bool(m["enabled"]),
        config_json=as_json_dict(m["config_json"]),
        description=m["description"],
    )


def _instance_row(m) -> AlertInstanceRow:
    return AlertInstanceRow(
        id=int(m["id"]),
        rule_id=as_uuid(m["rule_id"]),
        status=m["status"],
        severity=m["severity"],
        fingerprint=m["fingerprint"],
        title=m["title"],
        message=m["message"],
        opened_at=m["opened_at"],
        acknowledged_at=m["acknowledged_at"],
        resolved_at=m["resolved_at"],
        acknowledged_by_user_id=m["acknowledged_by_user_id"],
        component_id=as_optional_uuid(m["component_id"]),
        processor_id=m["processor_id"],
        job_definition_id=as_optional_uuid(m["job_definition_id"]),
        detail_json=as_json_dict(m["detail_json"]),
    )


_INSTANCE_COLS = """
    id, rule_id, status, severity, fingerprint, title, message,
    opened_at, acknowledged_at, resolved_at, acknowledged_by_user_id,
    component_id, processor_id, job_definition_id, detail_json
"""


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_alert_rule(
        self,
        *,
        code: str,
        display_name: str,
        severity: str,
        rule_type: str,
        enabled: bool = False,
        config_json: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
    ) -> AlertRuleRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_alert_rule
                        (code, display_name, severity, rule_type, enabled,
                         config_json, description)
                    VALUES
                        (:code, :display_name, :severity, :rule_type, :enabled,
                         CAST(:config_json AS jsonb), :description)
                    ON CONFLICT (code) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        severity = EXCLUDED.severity,
                        rule_type = EXCLUDED.rule_type,
                        -- Do not overwrite enabled on re-seed (ops may have toggled it).
                        config_json = EXCLUDED.config_json,
                        description = EXCLUDED.description,
                        updated_at = NOW()
                    RETURNING id, code, display_name, severity, rule_type,
                              enabled, config_json, description
                    """
                ),
                {
                    "code": code,
                    "display_name": display_name,
                    "severity": severity,
                    "rule_type": rule_type,
                    "enabled": enabled,
                    "config_json": json.dumps(config_json or {}),
                    "description": description,
                },
            )
            return _rule_row(mapping_row(result))

        return run_write(self._session, _do)

    def get_alert_rule_by_code(self, code: str) -> Optional[AlertRuleRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, code, display_name, severity, rule_type,
                       enabled, config_json, description
                FROM monitoring.mon_alert_rule
                WHERE code = :code
                """
            ),
            {"code": code},
        )
        m = mapping_row(result)
        return _rule_row(m) if m else None

    def list_alert_rules(self, *, enabled_only: bool = False) -> List[AlertRuleRow]:
        sql = """
            SELECT id, code, display_name, severity, rule_type,
                   enabled, config_json, description
            FROM monitoring.mon_alert_rule
        """
        if enabled_only:
            sql += " WHERE enabled = TRUE"
        sql += " ORDER BY code"
        return [_rule_row(m) for m in mapping_rows(self._session.execute(text(sql)))]

    def set_rules_enabled(self, codes: List[str], *, enabled: bool = True) -> int:
        if not codes:
            return 0

        def _do():
            params: Dict[str, Any] = {"enabled": enabled}
            placeholders = []
            for i, code in enumerate(codes):
                key = f"c{i}"
                placeholders.append(f":{key}")
                params[key] = code
            result = self._session.execute(
                text(
                    f"""
                    UPDATE monitoring.mon_alert_rule
                    SET enabled = :enabled, updated_at = NOW()
                    WHERE code IN ({", ".join(placeholders)})
                    """
                ),
                params,
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)

    def get_active_alert(
        self,
        *,
        rule_id: UUID,
        fingerprint: str,
    ) -> Optional[AlertInstanceRow]:
        """Return open or acknowledged instance for (rule, fingerprint), if any."""
        result = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                WHERE rule_id = :rule_id AND fingerprint = :fingerprint
                  AND status IN ('open', 'acknowledged')
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {"rule_id": str(rule_id), "fingerprint": fingerprint},
        )
        m = mapping_row(result)
        return _instance_row(m) if m else None

    def list_active_alerts_for_rule(
        self, rule_id: UUID, *, limit: int = 500
    ) -> List[AlertInstanceRow]:
        result = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                WHERE rule_id = :rule_id AND status IN ('open', 'acknowledged')
                ORDER BY opened_at DESC
                LIMIT :limit
                """
            ),
            {"rule_id": str(rule_id), "limit": limit},
        )
        return [_instance_row(m) for m in mapping_rows(result)]

    def open_alert(
        self,
        *,
        rule_id: UUID,
        fingerprint: str,
        title: str,
        severity: str,
        opened_at: datetime,
        message: Optional[str] = None,
        component_id: Optional[UUID] = None,
        processor_id: Optional[int] = None,
        job_definition_id: Optional[UUID] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> AlertInstanceRow:
        """Open alert or return existing open instance for (rule_id, fingerprint)."""
        import json

        existing = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                WHERE rule_id = :rule_id AND fingerprint = :fingerprint
                  AND status IN ('open', 'acknowledged')
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {"rule_id": str(rule_id), "fingerprint": fingerprint},
        )
        m = mapping_row(existing)
        if m:
            return _instance_row(m)

        def _do():
            result = self._session.execute(
                text(
                    f"""
                    INSERT INTO monitoring.mon_alert_instance
                        (rule_id, status, severity, fingerprint, title, message,
                         opened_at, component_id, processor_id, job_definition_id, detail_json)
                    VALUES
                        (:rule_id, 'open', :severity, :fingerprint, :title, :message,
                         :opened_at, :component_id, :processor_id, :job_definition_id,
                         CAST(:detail_json AS jsonb))
                    RETURNING {_INSTANCE_COLS}
                    """
                ),
                {
                    "rule_id": str(rule_id),
                    "severity": severity,
                    "fingerprint": fingerprint,
                    "title": title,
                    "message": message,
                    "opened_at": opened_at,
                    "component_id": str(component_id) if component_id else None,
                    "processor_id": processor_id,
                    "job_definition_id": (
                        str(job_definition_id) if job_definition_id else None
                    ),
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            return _instance_row(mapping_row(result))

        return run_write(self._session, _do)

    def resolve_alert(
        self,
        alert_id: int,
        *,
        resolved_at: datetime,
    ) -> AlertInstanceRow:
        def _do():
            result = self._session.execute(
                text(
                    f"""
                    UPDATE monitoring.mon_alert_instance
                    SET status = 'resolved',
                        resolved_at = :resolved_at
                    WHERE id = :id AND status IN ('open', 'acknowledged')
                    RETURNING {_INSTANCE_COLS}
                    """
                ),
                {"id": alert_id, "resolved_at": resolved_at},
            )
            m = mapping_row(result)
            if not m:
                raise StorageNotFoundError(f"alert instance {alert_id} not open")
            return _instance_row(m)

        return run_write(self._session, _do)

    def ack_alert(
        self,
        alert_id: int,
        *,
        user_id: int,
        acknowledged_at: datetime,
    ) -> AlertInstanceRow:
        def _do():
            result = self._session.execute(
                text(
                    f"""
                    UPDATE monitoring.mon_alert_instance
                    SET status = 'acknowledged',
                        acknowledged_at = :acknowledged_at,
                        acknowledged_by_user_id = :user_id
                    WHERE id = :id AND status = 'open'
                    RETURNING {_INSTANCE_COLS}
                    """
                ),
                {
                    "id": alert_id,
                    "user_id": user_id,
                    "acknowledged_at": acknowledged_at,
                },
            )
            m = mapping_row(result)
            if not m:
                raise StorageNotFoundError(f"alert instance {alert_id} not open")
            return _instance_row(m)

        return run_write(self._session, _do)

    def list_alert_instances(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        component_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AlertInstanceRow]:
        """Read-only listing with optional filters and pagination."""
        clauses = []
        params: Dict[str, Any] = {
            "limit": max(1, min(int(limit), 1000)),
            "offset": max(0, int(offset)),
        }
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if severity:
            clauses.append("severity = :severity")
            params["severity"] = severity
        if component_id is not None:
            clauses.append("component_id = :component_id")
            params["component_id"] = str(component_id)
        if since is not None:
            clauses.append("opened_at >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("opened_at < :until")
            params["until"] = until
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                {where}
                ORDER BY opened_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [_instance_row(m) for m in mapping_rows(result)]

    def count_alert_instances(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        component_id: Optional[UUID] = None,
    ) -> int:
        clauses = []
        params: Dict[str, Any] = {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if severity:
            clauses.append("severity = :severity")
            params["severity"] = severity
        if component_id is not None:
            clauses.append("component_id = :component_id")
            params["component_id"] = str(component_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT COUNT(*) AS cnt
                FROM monitoring.mon_alert_instance
                {where}
                """
            ),
            params,
        )
        m = mapping_row(result)
        return int(m["cnt"]) if m else 0

    def get_alert_instance(self, alert_id: int) -> Optional[AlertInstanceRow]:
        result = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                WHERE id = :id
                """
            ),
            {"id": alert_id},
        )
        m = mapping_row(result)
        return _instance_row(m) if m else None

    def list_open_alerts(self, *, limit: int = 500) -> List[AlertInstanceRow]:
        result = self._session.execute(
            text(
                f"""
                SELECT {_INSTANCE_COLS}
                FROM monitoring.mon_alert_instance
                WHERE status IN ('open', 'acknowledged')
                ORDER BY opened_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [_instance_row(m) for m in mapping_rows(result)]

    def delete_resolved_before(self, *, before: datetime, limit: int = 2000) -> int:
        """Delete resolved alert instances older than cutoff (never open/acked)."""

        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_alert_instance
                        WHERE status = 'resolved'
                          AND COALESCE(resolved_at, opened_at) < :before
                        ORDER BY COALESCE(resolved_at, opened_at) ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_alert_instance a
                    USING doomed
                    WHERE a.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
