"""
One-time backfill: widget_titles -> widget_configuration (idempotent).

Run after central config tables exist:
  python migrations/migrate_widget_titles_to_widget_configuration.py
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
    from app.database.migrate_widget_titles_to_configuration import (
        ensure_widget_title_configuration,
    )

    print("Ensuring widget_configuration table exists...")
    ensure_central_config_tables(engine)

    print("Migrating widget_titles -> widget_configuration...")
    counts = ensure_widget_title_configuration(engine)
    print(
        "Done. "
        f"migrated={counts.get('migrated', 0)}, "
        f"skipped_existing={counts.get('skipped_existing', 0)}, "
        f"seeded={counts.get('seeded', 0)}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
