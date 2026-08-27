"""Repository for monitoring.mon_http_request_agg."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import as_uuid, mapping_row, mapping_rows, run_write
from app.monitoring.storage.types import HttpAggRow


def _row(m) -> HttpAggRow:
    return HttpAggRow(
        id=int(m["id"]),
        component_id=as_uuid(m["component_id"]),
        bucket_start=m["bucket_start"],
        route_template=m["route_template"],
        method=m["method"],
        status_class=m["status_class"],
        request_count=int(m["request_count"]),
        error_count=int(m["error_count"]),
        sum_duration_ms=int(m["sum_duration_ms"]),
        max_duration_ms=int(m["max_duration_ms"]),
    )


class HttpAggRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_http_agg(
        self,
        *,
        component_id: UUID,
        bucket_start: datetime,
        route_template: str,
        method: str,
        status_class: str,
        request_count: int,
        error_count: int = 0,
        sum_duration_ms: int = 0,
        max_duration_ms: int = 0,
    ) -> HttpAggRow:
        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_http_request_agg
                        (component_id, bucket_start, route_template, method, status_class,
                         request_count, error_count, sum_duration_ms, max_duration_ms)
                    VALUES
                        (:component_id, :bucket_start, :route_template, :method, :status_class,
                         :request_count, :error_count, :sum_duration_ms, :max_duration_ms)
                    ON CONFLICT (component_id, bucket_start, method, route_template, status_class)
                    DO UPDATE SET
                        request_count = monitoring.mon_http_request_agg.request_count
                            + EXCLUDED.request_count,
                        error_count = monitoring.mon_http_request_agg.error_count
                            + EXCLUDED.error_count,
                        sum_duration_ms = monitoring.mon_http_request_agg.sum_duration_ms
                            + EXCLUDED.sum_duration_ms,
                        max_duration_ms = GREATEST(
                            monitoring.mon_http_request_agg.max_duration_ms,
                            EXCLUDED.max_duration_ms
                        )
                    RETURNING id, component_id, bucket_start, route_template, method, status_class,
                              request_count, error_count, sum_duration_ms, max_duration_ms
                    """
                ),
                {
                    "component_id": str(component_id),
                    "bucket_start": bucket_start,
                    "route_template": route_template,
                    "method": method,
                    "status_class": status_class,
                    "request_count": request_count,
                    "error_count": error_count,
                    "sum_duration_ms": sum_duration_ms,
                    "max_duration_ms": max_duration_ms,
                },
            )
            return _row(mapping_row(result))

        return run_write(self._session, _do)

    def query_http_agg(
        self,
        *,
        component_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5000,
    ) -> List[HttpAggRow]:
        clauses = []
        params = {"limit": limit}
        if component_id is not None:
            clauses.append("component_id = :component_id")
            params["component_id"] = str(component_id)
        if since is not None:
            clauses.append("bucket_start >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("bucket_start < :until")
            params["until"] = until
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT id, component_id, bucket_start, route_template, method, status_class,
                       request_count, error_count, sum_duration_ms, max_duration_ms
                FROM monitoring.mon_http_request_agg
                {where}
                ORDER BY bucket_start DESC
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
                        SELECT id FROM monitoring.mon_http_request_agg
                        WHERE bucket_start < :before
                        ORDER BY bucket_start ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_http_request_agg h
                    USING doomed
                    WHERE h.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
