"""
Standalone script to create Phase 3 central configuration tables.

Run once against Postgres (idempotent):
  python migrations/apply_central_config.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv

    for env_file in ("environment.env", ".env", "../environment.env", "../.env"):
        if os.path.isfile(env_file):
            load_dotenv(env_file)
            break
except ImportError:
    pass

from sqlalchemy import create_engine, inspect

DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def apply_schema() -> None:
    from app.database.migrate_central_config import (
        central_config_tables_present,
        ensure_central_config_tables,
    )

    print("Applying central configuration tables...")
    ensure_central_config_tables(engine)
    if not central_config_tables_present(engine):
        print("ERROR: One or more central config tables are still missing.")
        sys.exit(1)

    insp = inspect(engine)
    for name in ("installation_settings", "widget_configuration", "dashboard_layout"):
        cols = [c["name"] for c in insp.get_columns(name)]
        print(f"  {name}: {', '.join(cols)}")
    print("Done.")


if __name__ == "__main__":
    try:
        apply_schema()
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)
