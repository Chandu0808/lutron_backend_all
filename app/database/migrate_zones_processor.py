from __future__ import annotations

from sqlalchemy import inspect, text


def _has_column(engine, table_name: str, column_name: str) -> bool:
    insp = inspect(engine)
    try:
        cols = insp.get_columns(table_name)
    except Exception:
        return False
    return any(c.get("name") == column_name for c in cols)


def _sqlite_index_exists(engine, index_name: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'index' AND name = :name"),
            {"name": index_name},
        ).fetchall()
    return bool(rows)


def ensure_zones_processor_scope(engine) -> None:
    """
    Idempotent migration:
    - Add zones.processor_id (FK to processor.id, cascade)
    - Backfill zones.processor_id from areas.processor_id via zones.area_id
    - Replace legacy UNIQUE(code) with UNIQUE(processor_id, code)

    Notes:
    - PostgreSQL: uses ALTER TABLE and conditional index drops/creates.
    - SQLite: rebuilds zones into zones__new and renames.
    """
    dialect = (getattr(engine, "dialect", None) and engine.dialect.name) or ""

    # Fresh installs (or already migrated) should no-op quickly.
    if _has_column(engine, "zones", "processor_id"):
        if dialect == "sqlite":
            if not _sqlite_index_exists(engine, "uq_zones_processor_code"):
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_zones_processor_code ON zones(processor_id, code)"
                        )
                    )
        elif dialect == "postgresql":
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_zones_processor_code ON zones(processor_id, code)"
                    )
                )
        return

    if dialect == "sqlite":
        _migrate_sqlite(engine)
        return

    if dialect == "postgresql":
        _migrate_postgresql(engine)
        return

    # Unknown dialect: best-effort no-op
    return


def _migrate_postgresql(engine) -> None:
    with engine.begin() as conn:
        # 1) Add column if missing
        conn.execute(text("ALTER TABLE zones ADD COLUMN IF NOT EXISTS processor_id INTEGER"))

        # 2) Backfill from areas
        conn.execute(
            text(
                """
                UPDATE zones z
                SET processor_id = a.processor_id
                FROM areas a
                WHERE z.area_id = a.id
                  AND (z.processor_id IS NULL)
                """
            )
        )

        # 3) Enforce NOT NULL once backfilled (may fail if orphaned rows exist)
        conn.execute(text("ALTER TABLE zones ALTER COLUMN processor_id SET NOT NULL"))

        # 4) Add FK if missing
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM information_schema.table_constraints
                        WHERE constraint_type = 'FOREIGN KEY'
                          AND table_name = 'zones'
                          AND constraint_name = 'fk_zones_processor_id'
                    ) THEN
                        ALTER TABLE zones
                        ADD CONSTRAINT fk_zones_processor_id
                        FOREIGN KEY (processor_id) REFERENCES processor(id)
                        ON DELETE CASCADE;
                    END IF;
                END $$;
                """
            )
        )

        # 5) Drop legacy unique on code (unknown name; drop any unique index on exactly (code))
        conn.execute(
            text(
                """
                DO $$
                DECLARE
                  idx record;
                BEGIN
                  FOR idx IN
                    SELECT i.relname AS index_name
                    FROM pg_index x
                    JOIN pg_class t ON t.oid = x.indrelid
                    JOIN pg_class i ON i.oid = x.indexrelid
                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(x.indkey)
                    WHERE t.relname = 'zones'
                      AND x.indisunique = true
                    GROUP BY i.relname
                    HAVING array_agg(a.attname ORDER BY a.attname) = ARRAY['code']
                  LOOP
                    EXECUTE format('DROP INDEX IF EXISTS %I', idx.index_name);
                  END LOOP;
                END $$;
                """
            )
        )

        # 6) Create composite unique index
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_zones_processor_code ON zones(processor_id, code)"))


def _migrate_sqlite(engine) -> None:
    # SQLite can't add FK constraints to an existing column reliably; rebuild table.
    with engine.begin() as conn:
        # Backfill processor_id during copy using join to areas
        conn.execute(text("PRAGMA foreign_keys=OFF"))

        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS zones__new (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR(50) NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    type VARCHAR(50),
                    area_id INTEGER NOT NULL,
                    processor_id INTEGER NOT NULL,
                    max_power FLOAT,
                    high_end_trim FLOAT,
                    energy_trim FLOAT,
                    low_end_trim FLOAT,
                    loadcontroller_code INTEGER,
                    FOREIGN KEY(area_id) REFERENCES areas(id) ON DELETE CASCADE,
                    FOREIGN KEY(processor_id) REFERENCES processor(id) ON DELETE CASCADE
                )
                """
            )
        )

        # Copy rows, setting processor_id from areas
        conn.execute(
            text(
                """
                INSERT INTO zones__new (
                    id, code, name, type, area_id, processor_id,
                    max_power, high_end_trim, energy_trim, low_end_trim, loadcontroller_code
                )
                SELECT
                    z.id,
                    z.code,
                    z.name,
                    z.type,
                    z.area_id,
                    a.processor_id,
                    z.max_power,
                    z.high_end_trim,
                    z.energy_trim,
                    z.low_end_trim,
                    z.loadcontroller_code
                FROM zones z
                JOIN areas a ON a.id = z.area_id
                """
            )
        )

        conn.execute(text("DROP TABLE zones"))
        conn.execute(text("ALTER TABLE zones__new RENAME TO zones"))

        # Recreate indexes/constraints
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_zones_processor_code ON zones(processor_id, code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_zones_processor_id ON zones(processor_id)"))

        conn.execute(text("PRAGMA foreign_keys=ON"))

