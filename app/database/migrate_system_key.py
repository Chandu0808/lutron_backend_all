"""Idempotent system_key columns, orphan backfill, and partial unique indexes."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

UQ_SENSORS = "uq_sensors_modules_system_device"
UQ_DRIVERS = "uq_drivers_system_loadcontroller"


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


def _index_exists(conn, index_name: str, dialect: str) -> bool:
    if dialect == "postgresql":
        row = conn.execute(
            text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"
            ),
            {"n": index_name},
        ).first()
        return row is not None
    # SQLite
    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :n"),
        {"n": index_name},
    ).first()
    return row is not None


def _sqlite_has_column(conn, table_name: str, column_name: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(r[1] == column_name for r in rows)


def _add_text_column(conn, dialect: str, table: str, column: str) -> None:
    if dialect == "postgresql":
        if not _has_column(conn, table, column):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} TEXT NULL"))
    else:
        if not _sqlite_has_column(conn, table, column):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} TEXT"))


def _drop_unique_indexes(conn, dialect: str) -> None:
    """
    Drop partial unique indexes so orphan backfill can assign system_key even when
    duplicate (system_key, device_code/LC) rows still exist. Collapse runs next;
    indexes are recreated afterward.
    """
    for name in (UQ_SENSORS, UQ_DRIVERS):
        try:
            if dialect == "postgresql":
                conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
            else:
                conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
        except Exception as exc:
            print(f"[migrate_system_key] drop index {name} skipped: {exc}")


def _backfill_system_keys_sql(conn, dialect: str) -> None:
    """Copy processor.system_key onto unkeyed device/driver rows (pre-collapse)."""
    if dialect == "postgresql":
        if _table_exists(conn, "sensors_and_modules") and _table_exists(conn, "processor"):
            conn.execute(
                text(
                    """
                    UPDATE sensors_and_modules AS s
                    SET system_key = p.system_key
                    FROM processor AS p
                    WHERE s.processor_id = p.id
                      AND s.system_key IS NULL
                      AND p.system_key IS NOT NULL
                    """
                )
            )
        if _table_exists(conn, "drivers") and _table_exists(conn, "processor"):
            conn.execute(
                text(
                    """
                    UPDATE drivers AS d
                    SET system_key = p.system_key
                    FROM processor AS p
                    WHERE d.processor_id = p.id
                      AND d.system_key IS NULL
                      AND p.system_key IS NOT NULL
                    """
                )
            )
        return

    try:
        conn.execute(
            text(
                """
                UPDATE sensors_and_modules
                SET system_key = (
                  SELECT processor.system_key FROM processor
                  WHERE processor.id = sensors_and_modules.processor_id
                )
                WHERE system_key IS NULL
                  AND processor_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM processor
                    WHERE processor.id = sensors_and_modules.processor_id
                      AND processor.system_key IS NOT NULL
                  )
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE drivers
                SET system_key = (
                  SELECT processor.system_key FROM processor
                  WHERE processor.id = drivers.processor_id
                )
                WHERE system_key IS NULL
                  AND processor_id IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM processor
                    WHERE processor.id = drivers.processor_id
                      AND processor.system_key IS NOT NULL
                  )
                """
            )
        )
    except Exception as exc:
        print(f"[migrate_system_key] sqlite backfill skipped: {exc}")


def _create_unique_indexes(conn, dialect: str) -> None:
    if dialect == "postgresql":
        if _table_exists(conn, "sensors_and_modules") and not _index_exists(
            conn, UQ_SENSORS, dialect
        ):
            conn.execute(
                text(
                    f"""
                    CREATE UNIQUE INDEX {UQ_SENSORS}
                    ON sensors_and_modules (system_key, device_code)
                    WHERE system_key IS NOT NULL
                    """
                )
            )
        if _table_exists(conn, "drivers") and not _index_exists(conn, UQ_DRIVERS, dialect):
            conn.execute(
                text(
                    f"""
                    CREATE UNIQUE INDEX {UQ_DRIVERS}
                    ON drivers (system_key, loadcontroller_code)
                    WHERE system_key IS NOT NULL
                    """
                )
            )
        return

    try:
        conn.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {UQ_SENSORS}
                ON sensors_and_modules (system_key, device_code)
                WHERE system_key IS NOT NULL
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {UQ_DRIVERS}
                ON drivers (system_key, loadcontroller_code)
                WHERE system_key IS NOT NULL
                """
            )
        )
    except Exception as exc:
        print(f"[migrate_system_key] create indexes skipped: {exc}")


def ensure_system_key_schema(engine) -> None:
    """
    1) Add system_key / project_name columns
    2) Drop unique indexes if present (safe re-entry after a partial prior run)
    3) Backfill keys from processor, collapse orphans
    4) Recreate partial unique indexes

    Must run with a single backend instance — ALTER TABLE uses an 8s lock_timeout.
    """
    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""

    with engine.connect() as conn:
        if dialect == "postgresql":
            conn.execute(text("SET lock_timeout = '8s'"))

        if dialect == "postgresql":
            if _table_exists(conn, "processor"):
                _add_text_column(conn, dialect, "processor", "system_key")
                _add_text_column(conn, dialect, "processor", "project_name")
            if _table_exists(conn, "sensors_and_modules"):
                _add_text_column(conn, dialect, "sensors_and_modules", "system_key")
            if _table_exists(conn, "drivers"):
                _add_text_column(conn, dialect, "drivers", "system_key")
        else:
            for table, cols in (
                ("processor", ("system_key", "project_name")),
                ("sensors_and_modules", ("system_key",)),
                ("drivers", ("system_key",)),
            ):
                try:
                    for col in cols:
                        _add_text_column(conn, dialect, table, col)
                except Exception:
                    pass

        # Indexes may already exist from a previous startup that crashed mid-backfill.
        # Drop them so orphan rows can be keyed and collapsed safely.
        _drop_unique_indexes(conn, dialect)
        _backfill_system_keys_sql(conn, dialect)
        conn.commit()

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        from app.utils.system_identity import (
            backfill_row_system_keys_from_processors,
            collapse_all_systems,
            heal_duplicate_active_device_alerts,
        )

        backfill_row_system_keys_from_processors(db)
        collapse_all_systems(db)
        heal_duplicate_active_device_alerts(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[migrate_system_key] collapse/backfill error: {exc}")
        # Continue to recreate indexes; CREATE UNIQUE will fail loudly if dupes remain.
    finally:
        db.close()

    with engine.connect() as conn:
        if dialect == "postgresql":
            conn.execute(text("SET lock_timeout = '8s'"))
        _create_unique_indexes(conn, dialect)
        conn.commit()
