"""
One-time backfill: dashboard_chart_order -> dashboard_layout (idempotent).

Run against Postgres after central config tables exist:
  python migrations/migrate_dashboard_chart_order_to_dashboard_layout.py
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

from sqlalchemy import create_engine

DATABASE_URL = os.getenv("DATABASE_HOST_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_HOST_URL not set. Set it in environment.env or .env")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def main() -> None:
    from app.database.migrate_central_config import ensure_central_config_tables
    from app.database.migrate_dashboard_chart_order_to_layout import (
        run_dashboard_chart_order_to_layout_migration,
    )

    print("Ensuring dashboard_layout table exists...")
    ensure_central_config_tables(engine)

    print("Migrating dashboard_chart_order -> dashboard_layout...")
    counts = run_dashboard_chart_order_to_layout_migration(engine)
    print(
        "Done. "
        f"migrated={counts['migrated']}, "
        f"skipped_existing={counts['skipped_existing']}, "
        f"skipped_null={counts['skipped_null']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
