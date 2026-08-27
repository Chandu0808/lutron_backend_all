"""Repository for monitoring.mon_processor_connectivity_current."""

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
from app.monitoring.storage.types import ConnectivityCurrentRow


def _row(m) -> ConnectivityCurrentRow:
    return ConnectivityCurrentRow(
        processor_id=int(m["processor_id"]),
        observer_component_id=as_optional_uuid(m["observer_component_id"]),
        status=m["status"],
        last_ok_at=m["last_ok_at"],
        last_error_at=m["last_error_at"],
        detail_json=as_json_dict(m["detail_json"]),
        updated_at=m["updated_at"],
    )


class ConnectivityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_processor_connectivity(
        self,
        *,
        processor_id: int,
        status: str,
        observer_component_id: Optional[UUID] = None,
        last_ok_at: Optional[datetime] = None,
        last_error_at: Optional[datetime] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> ConnectivityCurrentRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_processor_connectivity_current
                        (processor_id, observer_component_id, status,
                         last_ok_at, last_error_at, detail_json, updated_at)
                    VALUES
                        (:processor_id, :observer_component_id, :status,
                         :last_ok_at, :last_error_at, CAST(:detail_json AS jsonb), NOW())
                    ON CONFLICT (processor_id) DO UPDATE SET
                        observer_component_id = COALESCE(
                            EXCLUDED.observer_component_id,
                            monitoring.mon_processor_connectivity_current.observer_component_id
                        ),
                        status = EXCLUDED.status,
                        last_ok_at = COALESCE(
                            EXCLUDED.last_ok_at,
                            monitoring.mon_processor_connectivity_current.last_ok_at
                        ),
                        last_error_at = COALESCE(
                            EXCLUDED.last_error_at,
                            monitoring.mon_processor_connectivity_current.last_error_at
                        ),
                        detail_json = EXCLUDED.detail_json,
                        updated_at = NOW()
                    RETURNING processor_id, observer_component_id, status,
                              last_ok_at, last_error_at, detail_json, updated_at
                    """
                ),
                {
                    "processor_id": processor_id,
                    "observer_component_id": (
                        str(observer_component_id) if observer_component_id else None
                    ),
                    "status": status,
                    "last_ok_at": last_ok_at,
                    "last_error_at": last_error_at,
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def get_connectivity_for_processor(
        self, processor_id: int
    ) -> Optional[ConnectivityCurrentRow]:
        result = self._session.execute(
            text(
                """
                SELECT processor_id, observer_component_id, status,
                       last_ok_at, last_error_at, detail_json, updated_at
                FROM monitoring.mon_processor_connectivity_current
                WHERE processor_id = :processor_id
                """
            ),
            {"processor_id": processor_id},
        )
        m = mapping_row(result)
        return _row(m) if m else None

    def get_connectivity_current(self) -> List[ConnectivityCurrentRow]:
        result = self._session.execute(
            text(
                """
                SELECT processor_id, observer_component_id, status,
                       last_ok_at, last_error_at, detail_json, updated_at
                FROM monitoring.mon_processor_connectivity_current
                ORDER BY processor_id
                """
            )
        )
        return [_row(m) for m in mapping_rows(result)]
