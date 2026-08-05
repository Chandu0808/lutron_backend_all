"""Idempotent floors.sort_order column for manual floor ordering."""

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


def ensure_floor_sort_order_column(engine) -> None:
    """Add sort_order to floors if missing. Safe on every startup."""
    if not _table_exists(engine, "floors"):
        return
    if _has_column(engine, "floors", "sort_order"):
        return

    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""
    if dialect == "postgresql":
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE floors "
                    "ADD COLUMN IF NOT EXISTS sort_order INTEGER NULL"
                )
            )
        return

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE floors ADD COLUMN sort_order INTEGER"))
