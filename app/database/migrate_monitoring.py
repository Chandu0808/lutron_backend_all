"""
Idempotent Monitoring Platform schema (Phase 1).

Creates PostgreSQL schema ``monitoring`` and the freeze tables only.
Does not seed dimensions, start services, or wire into application startup.

Feature flags remain off by default. Phase 2 may call this from ``main.py``
only when ``MONITORING_ENABLED`` is truthy (schema ensure + bootstrap seeds).
No runtime monitoring Service is started in Phase 2.

Usage (ops / CLI):
  python migrations/apply_monitoring_schema.py
  python migrations/apply_monitoring_bootstrap.py
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

MONITORING_SCHEMA = "monitoring"

# Freeze schema revision (Phase 1 DDL through Phase 13 hardening).
MONITORING_SCHEMA_VERSION = "1.0.0"

# Freeze table names (without schema prefix), creation order respects FKs.
MONITORING_TABLES: Tuple[str, ...] = (
    "mon_component",
    "mon_component_health_current",
    "mon_job_definition",
    "mon_metric_definition",
    "mon_alert_rule",
    "mon_processor_connectivity_current",
    "mon_leap_ping_sample",
    "mon_event",
    "mon_job_run",
    "mon_http_request_agg",
    "mon_metric_sample",
    "mon_metric_rollup",
    "mon_alert_instance",
)

# (label, DDL) — each statement is idempotent on PostgreSQL.
_SCHEMA_STATEMENTS: Tuple[Tuple[str, str], ...] = (
    (
        "schema monitoring",
        f"CREATE SCHEMA IF NOT EXISTS {MONITORING_SCHEMA}",
    ),
    (
        "table mon_component",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_component (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(64) NOT NULL,
            kind VARCHAR(64) NOT NULL,
            display_name VARCHAR(128) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_mon_component_code UNIQUE (code)
        )
        """,
    ),
    (
        "table mon_component_health_current",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_component_health_current (
            component_id UUID PRIMARY KEY
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE CASCADE,
            status VARCHAR(32) NOT NULL,
            last_heartbeat_at TIMESTAMPTZ,
            detail_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "table mon_job_definition",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_job_definition (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_key VARCHAR(128) NOT NULL,
            component_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE RESTRICT,
            display_name VARCHAR(256) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_mon_job_definition_job_key UNIQUE (job_key)
        )
        """,
    ),
    (
        "table mon_metric_definition",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_metric_definition (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            metric_key VARCHAR(128) NOT NULL,
            value_type VARCHAR(32) NOT NULL,
            description VARCHAR(512),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_mon_metric_definition_metric_key UNIQUE (metric_key)
        )
        """,
    ),
    (
        "table mon_alert_rule",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_alert_rule (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(128) NOT NULL,
            display_name VARCHAR(256) NOT NULL,
            severity VARCHAR(32) NOT NULL,
            rule_type VARCHAR(64) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            config_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            description VARCHAR(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_mon_alert_rule_code UNIQUE (code)
        )
        """,
    ),
    (
        "table mon_processor_connectivity_current",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_processor_connectivity_current (
            processor_id INTEGER PRIMARY KEY
                REFERENCES public.processor (id) ON DELETE CASCADE,
            observer_component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            status VARCHAR(32) NOT NULL,
            last_ok_at TIMESTAMPTZ,
            last_error_at TIMESTAMPTZ,
            detail_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ),
    (
        "table mon_leap_ping_sample",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_leap_ping_sample (
            id BIGSERIAL PRIMARY KEY,
            processor_id INTEGER NOT NULL
                REFERENCES public.processor (id) ON DELETE CASCADE,
            observer_component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            sampled_at TIMESTAMPTZ NOT NULL,
            rtt_ms INTEGER,
            success BOOLEAN NOT NULL,
            detail_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """,
    ),
    (
        "table mon_event",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_event (
            id BIGSERIAL PRIMARY KEY,
            event_at TIMESTAMPTZ NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            severity VARCHAR(32) NOT NULL DEFAULT 'info',
            component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            processor_id INTEGER
                REFERENCES public.processor (id) ON DELETE SET NULL,
            fingerprint VARCHAR(128),
            payload_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """,
    ),
    (
        "table mon_job_run",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_job_run (
            id BIGSERIAL PRIMARY KEY,
            job_definition_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_job_definition (id) ON DELETE CASCADE,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            outcome VARCHAR(32) NOT NULL,
            duration_ms INTEGER,
            error_class VARCHAR(128),
            error_message VARCHAR(2048),
            host_pid INTEGER,
            trigger_source VARCHAR(32),
            detail_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """,
    ),
    (
        "table mon_http_request_agg",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_http_request_agg (
            id BIGSERIAL PRIMARY KEY,
            component_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE CASCADE,
            bucket_start TIMESTAMPTZ NOT NULL,
            route_template VARCHAR(256) NOT NULL,
            method VARCHAR(16) NOT NULL,
            status_class VARCHAR(8) NOT NULL,
            request_count BIGINT NOT NULL DEFAULT 0,
            error_count BIGINT NOT NULL DEFAULT 0,
            sum_duration_ms BIGINT NOT NULL DEFAULT 0,
            max_duration_ms INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT uq_mon_http_request_agg_bucket UNIQUE (
                component_id, bucket_start, method, route_template, status_class
            )
        )
        """,
    ),
    (
        "table mon_metric_sample",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_metric_sample (
            id BIGSERIAL PRIMARY KEY,
            metric_definition_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_metric_definition (id) ON DELETE CASCADE,
            sampled_at TIMESTAMPTZ NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            processor_id INTEGER
                REFERENCES public.processor (id) ON DELETE SET NULL
        )
        """,
    ),
    (
        "table mon_metric_rollup",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_metric_rollup (
            id BIGSERIAL PRIMARY KEY,
            metric_definition_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_metric_definition (id) ON DELETE CASCADE,
            bucket_start TIMESTAMPTZ NOT NULL,
            bucket_size VARCHAR(8) NOT NULL,
            sample_count BIGINT NOT NULL DEFAULT 0,
            sum_value DOUBLE PRECISION NOT NULL DEFAULT 0,
            avg_value DOUBLE PRECISION,
            min_value DOUBLE PRECISION,
            max_value DOUBLE PRECISION,
            component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            processor_id INTEGER
                REFERENCES public.processor (id) ON DELETE SET NULL
        )
        """,
    ),
    (
        "table mon_alert_instance",
        f"""
        CREATE TABLE IF NOT EXISTS {MONITORING_SCHEMA}.mon_alert_instance (
            id BIGSERIAL PRIMARY KEY,
            rule_id UUID NOT NULL
                REFERENCES {MONITORING_SCHEMA}.mon_alert_rule (id) ON DELETE CASCADE,
            status VARCHAR(32) NOT NULL,
            severity VARCHAR(32) NOT NULL,
            fingerprint VARCHAR(128) NOT NULL,
            title VARCHAR(256) NOT NULL,
            message VARCHAR(2048),
            opened_at TIMESTAMPTZ NOT NULL,
            acknowledged_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            acknowledged_by_user_id INTEGER
                REFERENCES public.users (id) ON DELETE SET NULL,
            component_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_component (id) ON DELETE SET NULL,
            processor_id INTEGER
                REFERENCES public.processor (id) ON DELETE SET NULL,
            job_definition_id UUID
                REFERENCES {MONITORING_SCHEMA}.mon_job_definition (id) ON DELETE SET NULL,
            detail_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """,
    ),
)

_INDEX_STATEMENTS: Tuple[Tuple[str, str], ...] = (
    (
        "index mon_component_health_current status",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_component_health_current_status
        ON {MONITORING_SCHEMA}.mon_component_health_current (status)
        """,
    ),
    (
        "index mon_processor_connectivity_current status",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_processor_connectivity_current_status
        ON {MONITORING_SCHEMA}.mon_processor_connectivity_current (status)
        """,
    ),
    (
        "index mon_processor_connectivity_current observer",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_processor_connectivity_current_observer
        ON {MONITORING_SCHEMA}.mon_processor_connectivity_current (observer_component_id)
        """,
    ),
    (
        "index mon_leap_ping_sample processor time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_leap_ping_sample_processor_sampled
        ON {MONITORING_SCHEMA}.mon_leap_ping_sample (processor_id, sampled_at DESC)
        """,
    ),
    (
        "index mon_leap_ping_sample failures",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_leap_ping_sample_fail_sampled
        ON {MONITORING_SCHEMA}.mon_leap_ping_sample (sampled_at DESC)
        WHERE success = FALSE
        """,
    ),
    (
        "index mon_event processor time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_event_processor_event_at
        ON {MONITORING_SCHEMA}.mon_event (processor_id, event_at DESC)
        """,
    ),
    (
        "index mon_event type time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_event_type_event_at
        ON {MONITORING_SCHEMA}.mon_event (event_type, event_at DESC)
        """,
    ),
    (
        "index mon_event component time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_event_component_event_at
        ON {MONITORING_SCHEMA}.mon_event (component_id, event_at DESC)
        """,
    ),
    (
        "index mon_job_definition component",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_job_definition_component
        ON {MONITORING_SCHEMA}.mon_job_definition (component_id)
        """,
    ),
    (
        "index mon_job_run definition time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_job_run_definition_started
        ON {MONITORING_SCHEMA}.mon_job_run (job_definition_id, started_at DESC)
        """,
    ),
    (
        "index mon_job_run non-success",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_job_run_outcome_started
        ON {MONITORING_SCHEMA}.mon_job_run (outcome, started_at DESC)
        WHERE outcome <> 'success'
        """,
    ),
    (
        "index mon_http_request_agg bucket",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_http_request_agg_bucket_start
        ON {MONITORING_SCHEMA}.mon_http_request_agg (bucket_start DESC)
        """,
    ),
    (
        "index mon_metric_sample definition time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_metric_sample_definition_sampled
        ON {MONITORING_SCHEMA}.mon_metric_sample (metric_definition_id, sampled_at DESC)
        """,
    ),
    (
        "index mon_metric_sample processor time",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_metric_sample_processor_sampled
        ON {MONITORING_SCHEMA}.mon_metric_sample (processor_id, sampled_at DESC)
        WHERE processor_id IS NOT NULL
        """,
    ),
    (
        "unique index mon_metric_rollup bucket scope",
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mon_metric_rollup_bucket_scope
        ON {MONITORING_SCHEMA}.mon_metric_rollup (
            metric_definition_id,
            bucket_size,
            bucket_start,
            COALESCE(component_id, '00000000-0000-0000-0000-000000000000'::uuid),
            COALESCE(processor_id, -1)
        )
        """,
    ),
    (
        "index mon_alert_instance open list",
        f"""
        CREATE INDEX IF NOT EXISTS ix_mon_alert_instance_status_severity_opened
        ON {MONITORING_SCHEMA}.mon_alert_instance (status, severity, opened_at DESC)
        """,
    ),
    (
        "unique index mon_alert_instance open fingerprint",
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mon_alert_instance_open_fingerprint
        ON {MONITORING_SCHEMA}.mon_alert_instance (rule_id, fingerprint)
        WHERE status = 'open'
        """,
    ),
)


def iter_schema_statements() -> Iterable[Tuple[str, str]]:
    """Yield (label, ddl) for tables then indexes — used by tests and apply."""
    yield from _SCHEMA_STATEMENTS
    yield from _INDEX_STATEMENTS


def ensure_monitoring_schema(engine: Engine) -> None:
    """
    Create monitoring schema, freeze tables, and indexes if missing.

    PostgreSQL only. Idempotent (``IF NOT EXISTS``). Does not seed data.
    Requires ``public.processor`` and ``public.users`` for FK targets.
    """
    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""
    if dialect != "postgresql":
        raise RuntimeError(
            f"Monitoring schema requires PostgreSQL; got dialect={dialect!r}"
        )

    with engine.begin() as conn:
        for _label, stmt in iter_schema_statements():
            conn.execute(text(stmt))


def monitoring_schema_present(engine: Engine) -> bool:
    """Return True if schema exists and all freeze tables are present."""
    insp = inspect(engine)
    try:
        schemas = insp.get_schema_names()
    except Exception:
        return False
    if MONITORING_SCHEMA not in schemas:
        return False
    try:
        tables = set(insp.get_table_names(schema=MONITORING_SCHEMA))
    except Exception:
        return False
    return all(name in tables for name in MONITORING_TABLES)


def missing_monitoring_tables(engine: Engine) -> Tuple[str, ...]:
    """Return freeze table names missing from the monitoring schema."""
    insp = inspect(engine)
    try:
        if MONITORING_SCHEMA not in insp.get_schema_names():
            return MONITORING_TABLES
        tables = set(insp.get_table_names(schema=MONITORING_SCHEMA))
    except Exception:
        return MONITORING_TABLES
    return tuple(name for name in MONITORING_TABLES if name not in tables)
