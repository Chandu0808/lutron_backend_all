"""Repository for monitoring.mon_event."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import (
    as_json_dict,
    as_optional_uuid,
    mapping_row,
    mapping_rows,
    run_write,
)
from app.monitoring.storage.types import EventRow


def _row(m) -> EventRow:
    return EventRow(
        id=int(m["id"]),
        event_at=m["event_at"],
        event_type=m["event_type"],
        severity=m["severity"],
        component_id=as_optional_uuid(m["component_id"]),
        processor_id=m["processor_id"],
        fingerprint=m["fingerprint"],
        payload_json=as_json_dict(m["payload_json"]),
    )


class EventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_event(
        self,
        *,
        event_at: datetime,
        event_type: str,
        severity: str = "info",
        component_id: Optional[UUID] = None,
        processor_id: Optional[int] = None,
        fingerprint: Optional[str] = None,
        payload_json: Optional[Dict[str, Any]] = None,
    ) -> EventRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_event
                        (event_at, event_type, severity, component_id,
                         processor_id, fingerprint, payload_json)
                    VALUES
                        (:event_at, :event_type, :severity, :component_id,
                         :processor_id, :fingerprint, CAST(:payload_json AS jsonb))
                    RETURNING id, event_at, event_type, severity, component_id,
                              processor_id, fingerprint, payload_json
                    """
                ),
                {
                    "event_at": event_at,
                    "event_type": event_type,
                    "severity": severity,
                    "component_id": str(component_id) if component_id else None,
                    "processor_id": processor_id,
                    "fingerprint": fingerprint,
                    "payload_json": json.dumps(payload_json or {}),
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        event_type_prefix: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        processor_id: Optional[int] = None,
        component_id: Optional[UUID] = None,
        component_ids: Optional[List[UUID]] = None,
        child_names: Optional[List[str]] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        order: str = "desc",
        limit: int = 500,
    ) -> List[EventRow]:
        clauses = []
        params: Dict[str, Any] = {"limit": max(1, min(int(limit), 5000))}
        if event_type:
            clauses.append("event_type = :event_type")
            params["event_type"] = event_type
        if event_type_prefix:
            clauses.append("event_type LIKE :event_type_prefix")
            params["event_type_prefix"] = f"{event_type_prefix}%"
        if event_types:
            placeholders = []
            for i, et in enumerate(event_types):
                key = f"et_{i}"
                placeholders.append(f":{key}")
                params[key] = et
            clauses.append(f"event_type IN ({', '.join(placeholders)})")
        if processor_id is not None:
            clauses.append("processor_id = :processor_id")
            params["processor_id"] = processor_id
        if component_id is not None:
            clauses.append("component_id = :component_id")
            params["component_id"] = str(component_id)
        if component_ids:
            placeholders = []
            for i, cid in enumerate(component_ids):
                key = f"cid_{i}"
                placeholders.append(f":{key}")
                params[key] = str(cid)
            clauses.append(f"component_id IN ({', '.join(placeholders)})")
        if child_names:
            placeholders = []
            for i, name in enumerate(child_names):
                key = f"child_{i}"
                placeholders.append(f":{key}")
                params[key] = name
            clauses.append(
                f"(payload_json ->> 'child_name') IN ({', '.join(placeholders)})"
            )
        if since is not None:
            clauses.append("event_at >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("event_at <= :until")
            params["until"] = until
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order_sql = "ASC" if str(order).lower() == "asc" else "DESC"
        result = self._session.execute(
            text(
                f"""
                SELECT id, event_at, event_type, severity, component_id,
                       processor_id, fingerprint, payload_json
                FROM monitoring.mon_event
                {where}
                ORDER BY event_at {order_sql}
                LIMIT :limit
                """
            ),
            params,
        )
        return [_row(m) for m in mapping_rows(result)]

    def delete_before(self, *, before: datetime, limit: int = 2000) -> int:
        """Delete oldest events with event_at < before (batched)."""
        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_event
                        WHERE event_at < :before
                        ORDER BY event_at ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_event e
                    USING doomed
                    WHERE e.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
