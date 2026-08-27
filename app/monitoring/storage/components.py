"""Repository for monitoring.mon_component."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import as_uuid, mapping_row, mapping_rows, run_write
from app.monitoring.storage.types import ComponentRow


def _row(m) -> ComponentRow:
    return ComponentRow(
        id=as_uuid(m["id"]),
        code=m["code"],
        kind=m["kind"],
        display_name=m["display_name"],
        is_active=bool(m["is_active"]),
    )


class ComponentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_component(
        self,
        *,
        code: str,
        kind: str,
        display_name: str,
        is_active: bool = True,
    ) -> ComponentRow:
        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_component
                        (code, kind, display_name, is_active)
                    VALUES
                        (:code, :kind, :display_name, :is_active)
                    ON CONFLICT (code) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        display_name = EXCLUDED.display_name,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                    RETURNING id, code, kind, display_name, is_active
                    """
                ),
                {
                    "code": code,
                    "kind": kind,
                    "display_name": display_name,
                    "is_active": is_active,
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def get_component_by_code(self, code: str) -> Optional[ComponentRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, code, kind, display_name, is_active
                FROM monitoring.mon_component
                WHERE code = :code
                """
            ),
            {"code": code},
        )
        m = mapping_row(result)
        return _row(m) if m else None

    def get_component_by_id(self, component_id: UUID) -> Optional[ComponentRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, code, kind, display_name, is_active
                FROM monitoring.mon_component
                WHERE id = :id
                """
            ),
            {"id": str(component_id)},
        )
        m = mapping_row(result)
        return _row(m) if m else None

    def list_components(self, *, active_only: bool = False) -> List[ComponentRow]:
        sql = """
            SELECT id, code, kind, display_name, is_active
            FROM monitoring.mon_component
        """
        if active_only:
            sql += " WHERE is_active = TRUE"
        sql += " ORDER BY code"
        result = self._session.execute(text(sql))
        return [_row(m) for m in mapping_rows(result)]
