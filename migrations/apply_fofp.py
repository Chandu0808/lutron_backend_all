"""
Standalone FOFP database migration (all steps in one script).

Applies, in order:
  1. Core tables: fofp_shapes, zone_floorplan_positions (+ indexes, shape seed)
  2. Global settings: fofp_settings (+ default row)
  3. Settings extensions: marker_size, last_generated_at, generation_status
  4. Settings marker_color column
  5. Per-zone marker_shape column
  6. Per-zone shape_size_x / shape_size_y columns (+ backfill)
  7. zone_id nullable + ON DELETE SET NULL FK (preserve rows on zone delete)

Idempotent: safe to run multiple times on PostgreSQL.

Usage:
  python migrations/apply_fofp.py

Requires: DATABASE_HOST_URL in environment.env or .env
"""

import os
import sys

try:
    from dotenv import load_dotenv

    for env_file in ("environment.env", ".env", "../environment.env", "../.env"):
        if os.path.isfile(env_file):
            load_dotenv(env_file)
            break
except ImportError:
    pass

from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

DEFAULT_SHAPES = [
    "circle",
    "glowing_dot",
    "square",
    "triangle",
    "hexagon",
    "bulb",
]


def _run(label: str, stmt: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(stmt))
        print(f"  {label}  OK")
    except Exception as e:
        print(f"  {label}  FAIL: {e}")


def step_core_tables() -> None:
    print("[1/7] Core FOFP tables and indexes")
    print("-" * 60)
    create_tables = [
        (
            "table fofp_shapes",
            """
            CREATE TABLE IF NOT EXISTS fofp_shapes (
                id SERIAL PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                default_color VARCHAR(32),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_fofp_shapes_name UNIQUE (name)
            )
            """,
        ),
        (
            "table zone_floorplan_positions",
            """
            CREATE TABLE IF NOT EXISTS zone_floorplan_positions (
                id SERIAL PRIMARY KEY,
                floor_id INTEGER NOT NULL REFERENCES floors(id) ON DELETE CASCADE,
                area_id INTEGER NOT NULL REFERENCES areas(id) ON DELETE CASCADE,
                zone_id INTEGER NOT NULL REFERENCES zones(id) ON DELETE CASCADE,
                x DOUBLE PRECISION NOT NULL,
                y DOUBLE PRECISION NOT NULL,
                shape_size INTEGER NOT NULL DEFAULT 5,
                placement_source VARCHAR(16) NOT NULL DEFAULT 'auto',
                zone_available BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                modified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_zone_floorplan_positions_zone_id UNIQUE (zone_id)
            )
            """,
        ),
    ]
    for label, stmt in create_tables:
        _run(label, stmt)

    indexes = [
        (
            "index ix_zone_floorplan_positions_floor_id",
            "CREATE INDEX IF NOT EXISTS ix_zone_floorplan_positions_floor_id "
            "ON zone_floorplan_positions(floor_id)",
        ),
        (
            "index ix_zone_floorplan_positions_area_id",
            "CREATE INDEX IF NOT EXISTS ix_zone_floorplan_positions_area_id "
            "ON zone_floorplan_positions(area_id)",
        ),
        (
            "index ix_zone_floorplan_positions_floor_area",
            "CREATE INDEX IF NOT EXISTS ix_zone_floorplan_positions_floor_area "
            "ON zone_floorplan_positions(floor_id, area_id)",
        ),
    ]
    for label, stmt in indexes:
        _run(label, stmt)

    _run(
        f"seed fofp_shapes ({', '.join(DEFAULT_SHAPES)})",
        """
        INSERT INTO fofp_shapes (name)
        VALUES
          ('circle'),
          ('glowing_dot'),
          ('square'),
          ('triangle'),
          ('hexagon'),
          ('bulb')
        ON CONFLICT (name) DO NOTHING
        """,
    )


def step_settings_table() -> None:
    print("[2/7] FOFP settings table")
    print("-" * 60)
    _run(
        "table fofp_settings",
        """
        CREATE TABLE IF NOT EXISTS fofp_settings (
            id SERIAL PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            default_shape VARCHAR(64) NOT NULL DEFAULT 'circle',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            modified_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
    )
    _run(
        "seed fofp_settings default row",
        """
        INSERT INTO fofp_settings (enabled, default_shape)
        SELECT FALSE, 'circle'
        WHERE NOT EXISTS (SELECT 1 FROM fofp_settings)
        """,
    )


def step_settings_extend() -> None:
    print("[3/7] FOFP settings extensions")
    print("-" * 60)
    for label, stmt in [
        (
            "marker_size column",
            """
            ALTER TABLE fofp_settings
            ADD COLUMN IF NOT EXISTS marker_size INTEGER NOT NULL DEFAULT 5
            """,
        ),
        (
            "last_generated_at column",
            """
            ALTER TABLE fofp_settings
            ADD COLUMN IF NOT EXISTS last_generated_at TIMESTAMP WITH TIME ZONE
            """,
        ),
        (
            "generation_status column",
            """
            ALTER TABLE fofp_settings
            ADD COLUMN IF NOT EXISTS generation_status VARCHAR(32) NOT NULL DEFAULT 'not_generated'
            """,
        ),
    ]:
        _run(label, stmt)


def step_settings_marker_color() -> None:
    print("[4/7] FOFP settings marker_color")
    print("-" * 60)
    _run(
        "marker_color column",
        """
        ALTER TABLE fofp_settings
        ADD COLUMN IF NOT EXISTS marker_color VARCHAR(7) NOT NULL DEFAULT '#FDD835'
        """,
    )


def step_marker_shape() -> None:
    print("[5/7] zone_floorplan_positions.marker_shape")
    print("-" * 60)
    _run(
        "marker_shape column",
        """
        ALTER TABLE zone_floorplan_positions
        ADD COLUMN IF NOT EXISTS marker_shape VARCHAR(64) NULL
        """,
    )


def step_marker_stretch() -> None:
    print("[6/7] zone_floorplan_positions shape_size_x / shape_size_y")
    print("-" * 60)
    for label, stmt in [
        (
            "shape_size_x column",
            """
            ALTER TABLE zone_floorplan_positions
            ADD COLUMN IF NOT EXISTS shape_size_x INTEGER NULL
            """,
        ),
        (
            "shape_size_y column",
            """
            ALTER TABLE zone_floorplan_positions
            ADD COLUMN IF NOT EXISTS shape_size_y INTEGER NULL
            """,
        ),
        (
            "backfill shape_size_x/y from shape_size",
            """
            UPDATE zone_floorplan_positions
            SET shape_size_x = shape_size,
                shape_size_y = shape_size
            WHERE shape_size_x IS NULL OR shape_size_y IS NULL
            """,
        ),
    ]:
        _run(label, stmt)


def step_preserve_on_zone_delete() -> None:
    print("[7/7] preserve positions on zone delete (SET NULL FK)")
    print("-" * 60)
    for label, stmt in [
        (
            "zone_id nullable",
            """
            ALTER TABLE zone_floorplan_positions
              ALTER COLUMN zone_id DROP NOT NULL
            """,
        ),
        (
            "drop zone FK",
            """
            ALTER TABLE zone_floorplan_positions
              DROP CONSTRAINT IF EXISTS zone_floorplan_positions_zone_id_fkey
            """,
        ),
        (
            "add zone FK ON DELETE SET NULL",
            """
            ALTER TABLE zone_floorplan_positions
              ADD CONSTRAINT zone_floorplan_positions_zone_id_fkey
              FOREIGN KEY (zone_id) REFERENCES zones(id) ON DELETE SET NULL
            """,
        ),
    ]:
        _run(label, stmt)


def apply_fofp() -> None:
    print("Applying FOFP schema (combined migration)")
    print("=" * 60)
    step_core_tables()
    print()
    step_settings_table()
    print()
    step_settings_extend()
    print()
    step_settings_marker_color()
    print()
    step_marker_shape()
    print()
    step_marker_stretch()
    print()
    step_preserve_on_zone_delete()
    print("=" * 60)
    print("Done.")


if __name__ == "__main__":
    try:
        apply_fofp()
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)
