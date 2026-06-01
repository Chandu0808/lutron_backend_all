"""
Standalone script to add drivers.zone_id and backfill from processor_id + zone_code.

Run once against Postgres:
  python migrations/apply_drivers_zone_id.py
"""

import os
import sys

# Allow `from app...` when run as `python migrations/apply_drivers_zone_id.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def apply_schema() -> None:
    from app.database.migrate_drivers_zone_id import ensure_drivers_zone_id

    print("Applying drivers.zone_id schema...")
    ensure_drivers_zone_id(engine)
    print("Done.")


if __name__ == "__main__":
    try:
        apply_schema()
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)
