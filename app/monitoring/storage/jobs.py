"""Repository for monitoring.mon_job_definition and mon_job_run."""

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
from app.monitoring.storage.types import JobDefinitionRow, JobRunRow


def _def_row(m) -> JobDefinitionRow:
    return JobDefinitionRow(
        id=as_uuid(m["id"]),
        job_key=m["job_key"],
        component_id=as_uuid(m["component_id"]),
        display_name=m["display_name"],
        is_active=bool(m["is_active"]),
    )


def _run_row(m) -> JobRunRow:
    return JobRunRow(
        id=int(m["id"]),
        job_definition_id=as_uuid(m["job_definition_id"]),
        started_at=m["started_at"],
        finished_at=m["finished_at"],
        outcome=m["outcome"],
        duration_ms=m["duration_ms"],
        error_class=m["error_class"],
        error_message=m["error_message"],
        host_pid=m["host_pid"],
        trigger_source=m["trigger_source"],
        detail_json=as_json_dict(m["detail_json"]),
    )


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_job_definition(
        self,
        *,
        job_key: str,
        component_id: UUID,
        display_name: str,
        is_active: bool = True,
    ) -> JobDefinitionRow:
        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_job_definition
                        (job_key, component_id, display_name, is_active)
                    VALUES
                        (:job_key, :component_id, :display_name, :is_active)
                    ON CONFLICT (job_key) DO UPDATE SET
                        component_id = EXCLUDED.component_id,
                        display_name = EXCLUDED.display_name,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                    RETURNING id, job_key, component_id, display_name, is_active
                    """
                ),
                {
                    "job_key": job_key,
                    "component_id": str(component_id),
                    "display_name": display_name,
                    "is_active": is_active,
                },
            )
            return _def_row(mapping_row(result))

        return run_write(self._session, _do)

    def get_job_definition_by_key(self, job_key: str) -> Optional[JobDefinitionRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, job_key, component_id, display_name, is_active
                FROM monitoring.mon_job_definition
                WHERE job_key = :job_key
                """
            ),
            {"job_key": job_key},
        )
        m = mapping_row(result)
        return _def_row(m) if m else None

    def list_job_definitions(self, *, active_only: bool = False) -> List[JobDefinitionRow]:
        sql = """
            SELECT id, job_key, component_id, display_name, is_active
            FROM monitoring.mon_job_definition
        """
        if active_only:
            sql += " WHERE is_active = TRUE"
        sql += " ORDER BY job_key"
        return [_def_row(m) for m in mapping_rows(self._session.execute(text(sql)))]

    def insert_job_run(
        self,
        *,
        job_definition_id: UUID,
        started_at: datetime,
        outcome: str,
        finished_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        error_class: Optional[str] = None,
        error_message: Optional[str] = None,
        host_pid: Optional[int] = None,
        trigger_source: Optional[str] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> JobRunRow:
        import json

        def _do():
            result = self._session.execute(
                text(
                    """
                    INSERT INTO monitoring.mon_job_run
                        (job_definition_id, started_at, finished_at, outcome,
                         duration_ms, error_class, error_message, host_pid,
                         trigger_source, detail_json)
                    VALUES
                        (:job_definition_id, :started_at, :finished_at, :outcome,
                         :duration_ms, :error_class, :error_message, :host_pid,
                         :trigger_source, CAST(:detail_json AS jsonb))
                    RETURNING id, job_definition_id, started_at, finished_at, outcome,
                              duration_ms, error_class, error_message, host_pid,
                              trigger_source, detail_json
                    """
                ),
                {
                    "job_definition_id": str(job_definition_id),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                    "error_class": error_class,
                    "error_message": (error_message[:2048] if error_message else None),
                    "host_pid": host_pid,
                    "trigger_source": trigger_source,
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            return _run_row(mapping_row(result))

        return run_write(self._session, _do)

    def complete_job_run(
        self,
        *,
        job_definition_id: UUID,
        started_at: datetime,
        outcome: str,
        finished_at: Optional[datetime] = None,
        duration_ms: Optional[int] = None,
        error_class: Optional[str] = None,
        error_message: Optional[str] = None,
        host_pid: Optional[int] = None,
        trigger_source: Optional[str] = None,
        detail_json: Optional[Dict[str, Any]] = None,
    ) -> JobRunRow:
        """
        Finish a running job row in place.

        Matches ``job_definition_id`` + ``started_at`` + outcome='running'.
        If no running row exists (e.g. skipped emit without start), inserts one
        terminal row.
        """
        import json

        outcome_n = (outcome or "").strip().lower()
        if outcome_n == "running":
            return self.insert_job_run(
                job_definition_id=job_definition_id,
                started_at=started_at,
                outcome="running",
                finished_at=None,
                duration_ms=None,
                error_class=error_class,
                error_message=error_message,
                host_pid=host_pid,
                trigger_source=trigger_source,
                detail_json=detail_json,
            )

        def _do():
            result = self._session.execute(
                text(
                    """
                    UPDATE monitoring.mon_job_run
                    SET finished_at = :finished_at,
                        outcome = :outcome,
                        duration_ms = :duration_ms,
                        error_class = :error_class,
                        error_message = :error_message,
                        host_pid = COALESCE(:host_pid, host_pid),
                        trigger_source = COALESCE(:trigger_source, trigger_source),
                        detail_json = CAST(:detail_json AS jsonb)
                    WHERE id = (
                        SELECT id FROM monitoring.mon_job_run
                        WHERE job_definition_id = :job_definition_id
                          AND started_at = :started_at
                          AND outcome = 'running'
                        ORDER BY id ASC
                        LIMIT 1
                    )
                    RETURNING id, job_definition_id, started_at, finished_at, outcome,
                              duration_ms, error_class, error_message, host_pid,
                              trigger_source, detail_json
                    """
                ),
                {
                    "job_definition_id": str(job_definition_id),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "outcome": outcome_n,
                    "duration_ms": duration_ms,
                    "error_class": error_class,
                    "error_message": (error_message[:2048] if error_message else None),
                    "host_pid": host_pid,
                    "trigger_source": trigger_source,
                    "detail_json": json.dumps(detail_json or {}),
                },
            )
            m = mapping_row(result)
            if m:
                return _run_row(m)
            return None

        updated = run_write(self._session, _do)
        if updated is not None:
            return updated
        return self.insert_job_run(
            job_definition_id=job_definition_id,
            started_at=started_at,
            outcome=outcome_n,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_class=error_class,
            error_message=error_message,
            host_pid=host_pid,
            trigger_source=trigger_source,
            detail_json=detail_json,
        )

    def list_job_runs(
        self,
        *,
        job_definition_id: Optional[UUID] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 200,
    ) -> List[JobRunRow]:
        clauses = []
        params: Dict[str, Any] = {"limit": limit}
        if job_definition_id is not None:
            clauses.append("job_definition_id = :job_definition_id")
            params["job_definition_id"] = str(job_definition_id)
        if outcome:
            clauses.append("outcome = :outcome")
            params["outcome"] = outcome
        if since is not None:
            clauses.append("started_at >= :since")
            params["since"] = since
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        result = self._session.execute(
            text(
                f"""
                SELECT id, job_definition_id, started_at, finished_at, outcome,
                       duration_ms, error_class, error_message, host_pid,
                       trigger_source, detail_json
                FROM monitoring.mon_job_run
                {where}
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [_run_row(m) for m in mapping_rows(result)]

    def get_latest_job_run(
        self, job_definition_id: UUID
    ) -> Optional[JobRunRow]:
        result = self._session.execute(
            text(
                """
                SELECT id, job_definition_id, started_at, finished_at, outcome,
                       duration_ms, error_class, error_message, host_pid,
                       trigger_source, detail_json
                FROM monitoring.mon_job_run
                WHERE job_definition_id = :job_definition_id
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
            {"job_definition_id": str(job_definition_id)},
        )
        m = mapping_row(result)
        return _run_row(m) if m else None

    def delete_runs_before(self, *, before: datetime, limit: int = 2000) -> int:
        def _do():
            result = self._session.execute(
                text(
                    """
                    WITH doomed AS (
                        SELECT id FROM monitoring.mon_job_run
                        WHERE started_at < :before
                        ORDER BY started_at ASC
                        LIMIT :limit
                    )
                    DELETE FROM monitoring.mon_job_run r
                    USING doomed
                    WHERE r.id = doomed.id
                    """
                ),
                {"before": before, "limit": limit},
            )
            return int(result.rowcount or 0)

        return run_write(self._session, _do)
