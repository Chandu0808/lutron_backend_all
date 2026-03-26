"""
Standalone script to add Phase 1 schema changes for Manual Energy Logger.
Run this script on any PC to apply the same DB changes (zones, processor_zone_events, current_zone_status).

Usage:
  1. Copy this file and environment.env (or .env) to the target PC.
  2. Set DATABASE_HOST_URL in environment.env to the target database URL.
  3. pip install python-dotenv sqlalchemy psycopg2-binary  (if not already)
  4. python apply_manual_energy_logger_schema.py

Requires: PostgreSQL (uses ADD COLUMN IF NOT EXISTS).
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
    pass  # use os.environ only

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)

ALTERS = [
    ("zones", "max_power", "ADD COLUMN IF NOT EXISTS max_power DOUBLE PRECISION"),
    ("zones", "high_end_trim", "ADD COLUMN IF NOT EXISTS high_end_trim DOUBLE PRECISION"),
    ("zones", "energy_trim", "ADD COLUMN IF NOT EXISTS energy_trim DOUBLE PRECISION"),
    ("zones", "low_end_trim", "ADD COLUMN IF NOT EXISTS low_end_trim DOUBLE PRECISION"),
    ("zones", "loadcontroller_code", "ADD COLUMN IF NOT EXISTS loadcontroller_code INTEGER"),
    (
        "processor_zone_events",
        "zone_instantaneous_power",
        "ADD COLUMN IF NOT EXISTS zone_instantaneous_power DOUBLE PRECISION",
    ),
    (
        "processor_zone_events",
        "zone_instantaneous_max_power",
        "ADD COLUMN IF NOT EXISTS zone_instantaneous_max_power DOUBLE PRECISION",
    ),
    (
        "current_zone_status",
        "zone_instantaneous_power",
        "ADD COLUMN IF NOT EXISTS zone_instantaneous_power DOUBLE PRECISION",
    ),
    (
        "current_zone_status",
        "zone_instantaneous_max_power",
        "ADD COLUMN IF NOT EXISTS zone_instantaneous_max_power DOUBLE PRECISION",
    ),
]


def apply_schema():
    try:
        with engine.connect() as conn:
            print("Applying Manual Energy Logger Phase 1 schema...")
            print("-" * 60)
            for table, col, alter_clause in ALTERS:
                sql = f"ALTER TABLE {table} {alter_clause}"
                try:
                    conn.execute(text(sql))
                    conn.commit()
                    print(f"  {table}.{col}  OK")
                except Exception as e:
                    print(f"  {table}.{col}  FAIL: {e}")
            print("-" * 60)
            print("Done.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    apply_schema()

