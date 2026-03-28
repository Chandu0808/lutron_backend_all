"""
Adds `trim_savings` to `area_energy_saving_by_strategy` (nullable float).

Run once against Postgres:
  python migrations/apply_area_energy_trim_savings_column.py

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

SQL = (
    "ALTER TABLE area_energy_saving_by_strategy "
    "ADD COLUMN IF NOT EXISTS trim_savings DOUBLE PRECISION"
)


def apply_schema():
    try:
        with engine.connect() as conn:
            print("Adding area_energy_saving_by_strategy.trim_savings...")
            conn.execute(text(SQL))
            conn.commit()
            print("Done.")
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    apply_schema()
