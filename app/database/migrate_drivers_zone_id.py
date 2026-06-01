"""Idempotent drivers.zone_id column: add FK and backfill from processor_id + zone_code."""

from __future__ import annotations

from sqlalchemy import inspect, text


def _has_column(engine, table_name: str, column_name: str) -> bool:
    insp = inspect(engine)
    try:
        cols = insp.get_columns(table_name)
    except Exception:
        return False
    return any(c.get("name") == column_name for c in cols)


def _table_exists(engine, table_name: str) -> bool:
    insp = inspect(engine)
    try:
        return table_name in insp.get_table_names()
    except Exception:
        return False


def _backfill_drivers_zone_id(conn) -> None:
    conn.execute(
        text(
            """
            UPDATE drivers d
            SET zone_id = z.id
            FROM zones z
            WHERE d.zone_id IS NULL
              AND d.processor_id IS NOT NULL
              AND d.zone_code IS NOT NULL
              AND z.processor_id = d.processor_id
              AND z.code = d.zone_code::text
            """
        )
    )


def ensure_drivers_zone_id(engine) -> None:
    """
    Add drivers.zone_id (FK to zones.id) and backfill via processor_id + zone_code.
    Safe to run on every startup.
    """
    if not _table_exists(engine, "drivers"):
        return

    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""

    if dialect == "postgresql":
        with engine.begin() as conn:
            if not _has_column(engine, "drivers", "zone_id"):
                conn.execute(
                    text(
                        """
                        ALTER TABLE drivers
                        ADD COLUMN zone_id INTEGER NULL
                        REFERENCES zones(id) ON DELETE SET NULL
                        """
                    )
                )
            _backfill_drivers_zone_id(conn)
        return

    if not _has_column(engine, "drivers", "zone_id"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE drivers ADD COLUMN zone_id INTEGER"))
            conn.execute(
                text(
                    """
                    UPDATE drivers
                    SET zone_id = (
                        SELECT z.id FROM zones z
                        WHERE z.processor_id = drivers.processor_id
                          AND z.code = CAST(drivers.zone_code AS TEXT)
                        LIMIT 1
                    )
                    WHERE zone_id IS NULL
                      AND processor_id IS NOT NULL
                      AND zone_code IS NOT NULL
                    """
                )
            )
        return

    if dialect == "sqlite":
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE drivers
                    SET zone_id = (
                        SELECT z.id FROM zones z
                        WHERE z.processor_id = drivers.processor_id
                          AND z.code = CAST(drivers.zone_code AS TEXT)
                        LIMIT 1
                    )
                    WHERE zone_id IS NULL
                      AND processor_id IS NOT NULL
                      AND zone_code IS NOT NULL
                    """
                )
            )
