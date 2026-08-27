"""Idempotent area_path columns on drivers and sensors_and_modules."""

from __future__ import annotations

from sqlalchemy import text


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table_name},
    ).first()
    return row is not None


def _has_column(conn, table_name: str, column_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table_name, "c": column_name},
    ).first()
    return row is not None


def ensure_alert_area_path_columns(engine) -> None:
    """
    Add area_path + area_map_failures on drivers and sensors_and_modules.
    Uses one connection, short lock_timeout — safe to run on every startup.
    """
    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""
    targets = ("drivers", "sensors_and_modules")

    with engine.connect() as conn:
        if dialect == "postgresql":
            conn.execute(text("SET lock_timeout = '8s'"))
        for table in targets:
            if not _table_exists(conn, table):
                continue
            if not _has_column(conn, table, "area_path"):
                if dialect == "postgresql":
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS area_path TEXT NULL")
                    )
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN area_path TEXT"))
            if not _has_column(conn, table, "area_map_failures"):
                if dialect == "postgresql":
                    conn.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                            f"area_map_failures INTEGER NOT NULL DEFAULT 0"
                        )
                    )
                else:
                    conn.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN area_map_failures "
                            f"INTEGER NOT NULL DEFAULT 0"
                        )
                    )
        conn.commit()
