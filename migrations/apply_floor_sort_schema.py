"""
Standalone script to add sort_order column to floors for manual floor sorting.

Usage:
  python migrations/apply_floor_sort_schema.py
"""

import os
import sys

# Allow `from app...` when run as: python migrations/apply_floor_sort_schema.py
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


def apply_schema():
    from app.database.migrate_floor_sort_order import ensure_floor_sort_order_column

    try:
        print("Applying floor sort_order schema...")
        ensure_floor_sort_order_column(engine)
        print("Done.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    apply_schema()
