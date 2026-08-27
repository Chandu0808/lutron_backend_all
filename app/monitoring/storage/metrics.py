"""Repository for metric definitions, samples, and rollups."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.monitoring.storage._util import (
    as_optional_uuid,
    as_uuid,
    mapping_row,
    mapping_rows,
    run_write,
)
from app.monitoring.storage.types import (
    MetricDefinitionRow,
    MetricRollupRow,
    MetricSampleRow,
)


def _def_row(m) -> MetricDefinitionRow:
    return MetricDefinitionRow(
        id=as_uuid(m["id"]),
        metric_key=m["metric_key"],
        value_type=m["value_type"],
        description=m["description"],
        is_active=bool(m["is_active"]),
    )


def _sample_row(m) -> MetricSampleRow:
    return MetricSampleRow(
        id=int(m["id"]),
        metric_definition_id=as_uuid(m["metric_definition_id"]),
        sampled_at=m["sampled_at"],
        value=float(m["value"]),
        component_id=as_optional_uuid(m["component_id"]),
        processor_id=m["processor_id"],
    )


def _rollup_row(m) -> MetricRollupRow:
    return MetricRollupRow(
        id=int(m["id"]),
        metric_definition_id=as_uuid(m["metric_definition_id"]),
        bucket_start=m["bucket_start"],
        bucket_size=m["bucket_size"],
        sample_count=int(m["sample_count"]),
        sum_value=float(m["sum_value"]),
        avg_value=m["avg_value"],
        min_value=m["min_value"],
        max_value=m["max_value"],
        component_id=as_optional_uuid(m["component_id"]),
        processor_id=m["processor_id"],
    )


class MetricsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_metric_definition(
        self,
        *,
        metric_key: str,
        value_type: str,
        description: Optional[str] = None,
        is_active: bool = True,
    ) -> MetricDefinitionRow:
        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_metric_definition
                        (metric_key, value_type, description, is_active)
                    VALUES
                        (:metric_key, :value_type, :description, :is_active)
                    ON CONFLICT (metric_key) DO UPDATE SET
                        value_type = EXCLUDED.value_type,
                        description = EXCLUDED.description,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                    RETURNING id, metric_key, value_type, description, is_active
                    """
                ),
                {
                    "metric_key": metric_key,
                    "value_type": value_type,
                    "description": description,
                    "is_active": is_active,
                },
            )
            return _def_row(mapping_row(result))

        return run_write(self._session, _do)

    def get_metric_definition_by_key(
        self, metric_key: str
    ) -> Optional[MetricDefinitionRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, metric_key, value_type, description, is_active
                FROM monitoring.mon_metric_definition
                WHERE metric_key = :metric_key
                """
            ),
            {"metric_key": metric_key},
        )
        m = mapping_row(result)
        return _def_row(m) if m else None

    def list_metric_definitions(
        self, *, active_only: bool = False
    ) -> List[MetricDefinitionRow]:
        sql = """
            SELECT id, metric_key, value_type, description, is_active
            FROM monitoring.mon_metric_definition
        """
        if active_only:
            sql += " WHERE is_active = TRUE"
        sql += " ORDER BY metric_key"
        return [_def_row(m) for m in mapping_rows(self._session.execute(text(sql)))]

    def insert_metric_sample(
        self,
        *,
        metric_definition_id: UUID,
        sampled_at: datetime,
        value: float,
        component_id: Optional[UUID] = None,
        processor_id: Optional[int] = None,
    ) -> MetricSampleRow:
        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_metric_sample
                        (metric_definition_id, sampled_at, value, component_id, processor_id)
                    VALUES
                        (:metric_definition_id, :sampled_at, :value, :component_id, :processor_id)
                    RETURNING id, metric_definition_id, sampled_at, value, component_id, processor_id
                    """
                ),
                {
                    "metric_definition_id": str(metric_definition_id),
                    "sampled_at": sampled_at,
                    "value": value,
                    "component_id": str(component_id) if component_id else None,
                    "processor_id": processor_id,
                },
            )
            return _sample_row(mapping_row(result))

        return run_write(self._session, _do)

    def query_metric_samples(
        self,
        *,
        metric_definition_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5000,
    ) -> List[MetricSampleRow]:
        clauses = []
        params = {"limit": limit}
        if metric_definition_id is not None:
            clauses.append("metric_definition_id = :metric_definition_id")
            params["metric_definition_id"] = str(metric_definition_id)
        if since is not None:
            clauses.append("sampled_at >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("sampled_at < :until")
            params["until"] = until
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT id, metric_definition_id, sampled_at, value, component_id, processor_id
                FROM monitoring.mon_metric_sample
                {where}
                ORDER BY sampled_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_sample_row(m) for m in mapping_rows(result)]

    def upsert_metric_rollup(
        self,
        *,
        metric_definition_id: UUID,
        bucket_start: datetime,
        bucket_size: str,
        sample_count: int,
        sum_value: float,
        avg_value: Optional[float] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        component_id: Optional[UUID] = None,
        processor_id: Optional[int] = None,
    ) -> MetricRollupRow:
        def _do():
            # Delete+insert via unique expression index is awkward in ON CONFLICT;
            # use upsert on natural key with COALESCE sentinels matching the unique index.
            result = self._session.execute(
                text(
                    """
                    WITH upsert AS (
                        UPDATE monitoring.mon_metric_rollup AS r
                        SET sample_count = :sample_count,
                            sum_value = :sum_value,
                            avg_value = :avg_value,
                            min_value = :min_value,
                            max_value = :max_value
                        WHERE r.metric_definition_id = :metric_definition_id
                          AND r.bucket_size = :bucket_size
                          AND r.bucket_start = :bucket_start
                          AND COALESCE(r.component_id, '00000000-0000-0000-0000-000000000000'::uuid)
                              = COALESCE(CAST(:component_id AS uuid),
                                         '00000000-0000-0000-0000-000000000000'::uuid)
                          AND COALESCE(r.processor_id, -1)
                              = COALESCE(:processor_id, -1)
                        RETURNING r.*
                    ),
                    inserted AS (
                        INSERT INTO monitoring.mon_metric_rollup
                            (metric_definition_id, bucket_start, bucket_size, sample_count,
                             sum_value, avg_value, min_value, max_value, component_id, processor_id)
                        SELECT :metric_definition_id, :bucket_start, :bucket_size, :sample_count,
                               :sum_value, :avg_value, :min_value, :max_value,
                               CAST(:component_id AS uuid), :processor_id
                        WHERE NOT EXISTS (SELECT 1 FROM upsert)
                        RETURNING *
                    )
                    SELECT id, metric_definition_id, bucket_start, bucket_size, sample_count,
                           sum_value, avg_value, min_value, max_value, component_id, processor_id
                    FROM upsert
                    UNION ALL
                    SELECT id, metric_definition_id, bucket_start, bucket_size, sample_count,
                           sum_value, avg_value, min_value, max_value, component_id, processor_id
                    FROM inserted
                    """
                ),
                {
                    "metric_definition_id": str(metric_definition_id),
                    "bucket_start": bucket_start,
                    "bucket_size": bucket_size,
                    "sample_count": sample_count,
                    "sum_value": sum_value,
                    "avg_value": avg_value,
                    "min_value": min_value,
                    "max_value": max_value,
                    "component_id": str(component_id) if component_id else None,
                    "processor_id": processor_id,
                },
            )
            return _rollup_row(mapping_row(result))

        return run_write(self._session, _do)

    def query_metric_rollups(
        self,
        *,
        metric_definition_id: Optional[UUID] = None,
        bucket_size: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 5000,
    ) -> List[MetricRollupRow]:
        clauses = []
        params = {"limit": limit}
        if metric_definition_id is not None:
            clauses.append("metric_definition_id = :metric_definition_id")
            params["metric_definition_id"] = str(metric_definition_id)
        if bucket_size:
            clauses.append("bucket_size = :bucket_size")
            params["bucket_size"] = bucket_size
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
                SELECT id, metric_definition_id, bucket_start, bucket_size, sample_count,
                       sum_value, avg_value, min_value, max_value, component_id, processor_id
                FROM monitoring.mon_metric_rollup
                {where}
                ORDER BY bucket_start DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_rollup_row(m) for m in mapping_rows(result)]

    def delete_samples_before(self, *, before: datetime, limit: int = 2000) -> int:
        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_metric_sample
                        WHERE sampled_at < :before
                        ORDER BY sampled_at ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_metric_sample s
                    USING doomed
                    WHERE s.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)

    def delete_rollups_before(self, *, before: datetime, limit: int = 2000) -> int:
        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_metric_rollup
                        WHERE bucket_start < :before
                        ORDER BY bucket_start ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_metric_rollup r
                    USING doomed
                    WHERE r.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
