"""
Standalone script to add polygon_index column to coordinates
for multi-polygon area support.

Usage:
  1. Copy this file and environment.env (or .env) to the target PC if needed.
  2. Set DATABASE_HOST_URL in environment.env or .env to the target database URL.
  3. pip install python-dotenv sqlalchemy psycopg2-binary  (if not already)
  4. python apply_polygon_index_schema.py

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
    # Fallback to environment variables only
    pass

from sqlalchemy import create_engine, text


DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def apply_schema():
    sql_statements = [
        "ALTER TABLE coordinates "
        "ADD COLUMN IF NOT EXISTS polygon_index INTEGER NOT NULL DEFAULT 0",
        "UPDATE coordinates SET polygon_index = 0 WHERE polygon_index IS NULL",
    ]

    try:
        with engine.connect() as conn:
            print("Applying polygon_index schema changes to coordinates...")
            print("-" * 60)
            for stmt in sql_statements:
                try:
                    conn.execute(text(stmt))
                    conn.commit()
                    print(f"  OK: {stmt}")
                except Exception as e:
                    print(f"  FAIL: {stmt}\n       {e}")
            print("-" * 60)
            print("Done.")
    except Exception as e:
        print(f"Database connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    apply_schema()

