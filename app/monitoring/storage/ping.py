"""Repository for monitoring.mon_leap_ping_sample."""

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
from app.monitoring.storage.types import LeapPingRow


def _row(m) -> LeapPingRow:
    return LeapPingRow(
        id=int(m["id"]),
        processor_id=int(m["processor_id"]),
        observer_component_id=as_optional_uuid(m["observer_component_id"]),
        sampled_at=m["sampled_at"],
        rtt_ms=m["rtt_ms"],
        success=bool(m["success"]),
        detail_json=as_json_dict(m["detail_json"]),
    )


class PingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_leap_ping(
        self,
        *,
        processor_id: int,
        sampled_at: datetime,
        success: bool,
        rtt_ms: Optional[int] = None,
        observer_component_id: Optional[UUID] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> LeapPingRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_leap_ping_sample
                        (processor_id, observer_component_id, sampled_at,
                         rtt_ms, success, detail_json)
                    VALUES
                        (:processor_id, :observer_component_id, :sampled_at,
                         :rtt_ms, :success, CAST(:detail_json AS jsonb))
                    RETURNING id, processor_id, observer_component_id, sampled_at,
                              rtt_ms, success, detail_json
                    """
                ),
                {
                    "processor_id": processor_id,
                    "observer_component_id": (
                        str(observer_component_id) if observer_component_id else None
                    ),
                    "sampled_at": sampled_at,
                    "rtt_ms": rtt_ms,
                    "success": success,
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def list_leap_pings(
        self,
        *,
        processor_id: Optional[int] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[LeapPingRow]:
        clauses = []
        params: Dict[str, Any] = {"limit": limit}
        if processor_id is not None:
            clauses.append("processor_id = :processor_id")
            params["processor_id"] = processor_id
        if since is not None:
            clauses.append("sampled_at >= :since")
            params["since"] = since
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT id, processor_id, observer_component_id, sampled_at,
                       rtt_ms, success, detail_json
                FROM monitoring.mon_leap_ping_sample
                {where}
                ORDER BY sampled_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_row(m) for m in mapping_rows(result)]

    def delete_before(self, *, before: datetime, limit: int = 2000) -> int:
        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_leap_ping_sample
                        WHERE sampled_at < :before
                        ORDER BY sampled_at ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_leap_ping_sample p
                    USING doomed
                    WHERE p.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
