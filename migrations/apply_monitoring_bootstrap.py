"""
Apply Monitoring schema (if needed) and bootstrap dimension seeds (Phase 2).

Does not start runtime monitoring. Safe to re-run (idempotent).

Usage:
  python migrations/apply_monitoring_bootstrap.py

Requires: DATABASE_HOST_URL; LMS public.processor / public.users for schema FKs.
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

from sqlalchemy import create_engine

DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def main() -> None:
    from app.database.migrate_monitoring import (
        ensure_monitoring_schema,
        monitoring_schema_present,
    )
    from app.monitoring.bootstrap import bootstrap_monitoring

    print("Ensuring monitoring schema...")
    ensure_monitoring_schema(engine)
    if not monitoring_schema_present(engine):
        print("ERROR: monitoring schema missing after ensure.")
        sys.exit(1)

    print("Bootstrapping monitoring dimensions...")
    report = bootstrap_monitoring()
    if not report.ok:
        print(f"ERROR: bootstrap failed: {report.error}")
        sys.exit(1)

    print("Bootstrap OK:")
    for key, value in report.as_dict().items():
        if key == "registry":
            continue
        print(f"  {key}: {value}")
    print("Note: MONITORING_* runtime flags remain off unless set in the environment.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Monitoring bootstrap apply failed: {e}")
        sys.exit(1)
