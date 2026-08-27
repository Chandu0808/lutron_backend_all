"""
Standalone: add area_path / area_map_failures for alert tables.

  python migrations/apply_alert_area_path.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file()))))

try:
    from dotenv import load_dotenv

    for env_file in ("environment.env", ".env", "../environment.env", "../.env"):
        if os.path.isfile(env_file):
            load_dotenv(env_file)
            break
except ImportError:
    pass

from sqlalchemy import create_engine

DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def apply_schema() -> None:
    from app.database.migrate_alert_area_path import ensure_alert_area_path_columns

    print("Applying alert area_path schema...")
    ensure_alert_area_path_columns(engine)
    print("Done.")


if __name__ == "__main__":
    try:
        apply_schema()
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)
