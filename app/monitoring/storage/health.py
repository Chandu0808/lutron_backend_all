"""Repository for monitoring.mon_component_health_current."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import (
    as_json_dict,
    as_uuid,
    mapping_row,
    mapping_rows,
    run_write,
)
from app.monitoring.storage.types import HealthCurrentRow


def _row(m) -> HealthCurrentRow:
    return HealthCurrentRow(
        component_id=as_uuid(m["component_id"]),
        status=m["status"],
        last_heartbeat_at=m["last_heartbeat_at"],
        detail_json=as_json_dict(m["detail_json"]),
        updated_at=m["updated_at"],
    )


class HealthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_component_health(
        self,
        *,
        component_id: UUID,
        status: str,
        last_heartbeat_at: Optional[datetime] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> HealthCurrentRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_component_health_current
                        (component_id, status, last_heartbeat_at, detail_json, updated_at)
                    VALUES
                        (:component_id, :status, :last_heartbeat_at,
                         CAST(:detail_json AS jsonb), NOW())
                    ON CONFLICT (component_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        last_heartbeat_at = COALESCE(
                            EXCLUDED.last_heartbeat_at,
                            monitoring.mon_component_health_current.last_heartbeat_at
                        ),
                        detail_json = EXCLUDED.detail_json,
                        updated_at = NOW()
                    RETURNING component_id, status, last_heartbeat_at, detail_json, updated_at
                    """
                ),
                {
                    "component_id": str(component_id),
                    "status": status,
                    "last_heartbeat_at": last_heartbeat_at,
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def get_health_current(self, component_id: UUID) -> Optional[HealthCurrentRow]:
        result = self._session.execute(
            text(
                """
                SELECT component_id, status, last_heartbeat_at, detail_json, updated_at
                FROM monitoring.mon_component_health_current
                WHERE component_id = :component_id
                """
            ),
            {"component_id": str(component_id)},
        )
        m = mapping_row(result)
        return _row(m) if m else None

    def get_all_health_current(self) -> List[HealthCurrentRow]:
        result = self._session.execute(
            text(
                """
                SELECT component_id, status, last_heartbeat_at, detail_json, updated_at
                FROM monitoring.mon_component_health_current
                ORDER BY component_id
                """
            )
        )
        return [_row(m) for m in mapping_rows(result)]
