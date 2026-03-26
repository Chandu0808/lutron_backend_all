"""
Standalone script to apply schema changes for alert UI display settings.

What it does:
1) Adds `display` boolean column (default TRUE) to:
   - processor
   - sensors_and_modules
   - drivers
2) Creates `alert_type_display_settings` table (global alert-type visibility)
   and ensures canonical alert types exist (default display TRUE).

Run this script once against your Postgres DB:
  python migrations/apply_alert_display_schema.py
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


ALTERS = [
    ("processor", "ADD COLUMN IF NOT EXISTS display BOOLEAN NOT NULL DEFAULT TRUE"),
    (
        "sensors_and_modules",
        "ADD COLUMN IF NOT EXISTS display BOOLEAN NOT NULL DEFAULT TRUE",
    ),
    ("drivers", "ADD COLUMN IF NOT EXISTS display BOOLEAN NOT NULL DEFAULT TRUE"),
]


SETUP_SQL = [
    """
    CREATE TABLE IF NOT EXISTS alert_type_display_settings (
        id SERIAL PRIMARY KEY,
        alert_type VARCHAR(64) NOT NULL UNIQUE,
        display BOOLEAN NOT NULL DEFAULT TRUE
    )
    """,
    # Ensure canonical alert types exist
    """
    INSERT INTO alert_type_display_settings (alert_type, display)
    VALUES
      ('Processor Not Responding', TRUE),
      ('Device Not Responding', TRUE),
      ('Ballast Failure', TRUE),
      ('Lamp Failure', TRUE),
      ('Other Warnings', TRUE)
    ON CONFLICT (alert_type) DO NOTHING
    """,
]


def apply_schema() -> None:
    with engine.connect() as conn:
        print("Applying alert UI display schema changes...")
        print("-" * 60)

        for table, alter_clause in ALTERS:
            sql = f"ALTER TABLE {table} {alter_clause}"
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"  {table}.display  OK")
            except Exception as e:
                print(f"  {table}.display  FAIL: {e}")

        for stmt in SETUP_SQL:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                print(f"  setup FAIL: {e}")

        print("-" * 60)
        print("Done.")


if __name__ == "__main__":
    try:
        apply_schema()
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)

