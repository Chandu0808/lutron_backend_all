"""
Standalone script to create the Monitoring Platform schema (Phase 1).

Idempotent on PostgreSQL. Does not seed data, start services, or enable flags.

Usage (from lutron_backend working directory):
  python migrations/apply_monitoring_schema.py

Requires: DATABASE_HOST_URL in environment.env or .env
Requires: public.processor and public.users (LMS tables) for foreign keys.
"""

from __future__ import annotations

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


def _require_lms_parents() -> None:
    insp = inspect(engine)
    public_tables = set(insp.get_table_names(schema="public"))
    missing = [t for t in ("processor", "users") if t not in public_tables]
    if missing:
        print(
            "ERROR: LMS parent tables missing in public schema: "
            + ", ".join(missing)
        )
        print("Apply core LMS schema before monitoring schema.")
        sys.exit(1)


def apply_schema() -> None:
    from app.database.migrate_monitoring import (
        MONITORING_SCHEMA,
        MONITORING_TABLES,
        ensure_monitoring_schema,
        missing_monitoring_tables,
        monitoring_schema_present,
    )

    print("Applying Monitoring Platform schema (Phase 1)...")
    print("-" * 60)
    print("Note: MONITORING_* feature flags remain disabled by default.")
    print("This script does not wire monitoring into application startup.")
    print("-" * 60)

    _require_lms_parents()
    ensure_monitoring_schema(engine)

    missing = missing_monitoring_tables(engine)
    if missing:
        print("ERROR: Missing tables after apply: " + ", ".join(missing))
        sys.exit(1)
    if not monitoring_schema_present(engine):
        print("ERROR: monitoring schema verification failed.")
        sys.exit(1)

    insp = inspect(engine)
    for name in MONITORING_TABLES:
        cols = [c["name"] for c in insp.get_columns(name, schema=MONITORING_SCHEMA)]
        print(f"  {MONITORING_SCHEMA}.{name}: {', '.join(cols)}")

    print("-" * 60)
    print("Done. Schema is ready; runtime monitoring is not enabled.")
    print("Rollback (non-prod only, approved): DROP SCHEMA monitoring CASCADE;")


if __name__ == "__main__":
    try:
        apply_schema()
    except Exception as e:
        print(f"Monitoring schema apply failed: {e}")
        sys.exit(1)
