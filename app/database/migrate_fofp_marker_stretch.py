"""Idempotent FOFP stretch columns on zone_floorplan_positions."""

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


def ensure_fofp_marker_stretch_columns(engine) -> None:
    """
    Add shape_size_x / shape_size_y if the FOFP model expects them but DB is behind.
    Safe to run on every startup (PostgreSQL ADD COLUMN IF NOT EXISTS).
    """
    if not _table_exists(engine, "zone_floorplan_positions"):
        return
    if not _has_column(engine, "zone_floorplan_positions", "shape_size"):
        return

    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""
    if dialect == "postgresql":
        stmts = [
            """
            ALTER TABLE zone_floorplan_positions
            ADD COLUMN IF NOT EXISTS shape_size_x INTEGER NULL
            """,
            """
            ALTER TABLE zone_floorplan_positions
            ADD COLUMN IF NOT EXISTS shape_size_y INTEGER NULL
            """,
            """
            UPDATE zone_floorplan_positions
            SET shape_size_x = shape_size,
                shape_size_y = shape_size
            WHERE shape_size_x IS NULL OR shape_size_y IS NULL
            """,
        ]
        with engine.begin() as conn:
            for stmt in stmts:
                conn.execute(text(stmt))
        return

    if _has_column(engine, "zone_floorplan_positions", "shape_size_x"):
        return

    with engine.begin() as conn:
        if not _has_column(engine, "zone_floorplan_positions", "shape_size_x"):
            conn.execute(
                text(
                    "ALTER TABLE zone_floorplan_positions "
                    "ADD COLUMN shape_size_x INTEGER"
                )
            )
        if not _has_column(engine, "zone_floorplan_positions", "shape_size_y"):
            conn.execute(
                text(
                    "ALTER TABLE zone_floorplan_positions "
                    "ADD COLUMN shape_size_y INTEGER"
                )
            )
        conn.execute(
            text(
                """
                UPDATE zone_floorplan_positions
                SET shape_size_x = shape_size,
                    shape_size_y = shape_size
                WHERE shape_size_x IS NULL OR shape_size_y IS NULL
                """
            )
        )
