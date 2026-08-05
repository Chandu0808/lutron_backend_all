"""
Optional one-shot migration for alert reconciliation indexes / legacy status normalize.

Safe to re-run. Requires DATABASE_HOST_URL (or app settings) for DB connectivity.

Usage:
  python migrations/apply_alert_reconciliation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as script from repo root or migrations/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.database.session import SessionLocal, engine


NORMALIZE_SQL = [
    # Drivers
    "UPDATE drivers SET alert_status = 'ok' WHERE alert_status IN ('okay', 'Active', 'Resolved')",
    "UPDATE drivers SET alert_status = 'not_ok' WHERE alert_status IN ('not_okay')",
    # Devices
    "UPDATE sensors_and_modules SET alert_status = 'ok' WHERE alert_status IN ('okay', 'Active', 'Resolved')",
    "UPDATE sensors_and_modules SET alert_status = 'not_ok' WHERE alert_status IN ('not_okay')",
    # Processors
    "UPDATE processor SET ping_status = 'ok' WHERE ping_status IN ('okay', 'Active', 'Resolved')",
    "UPDATE processor SET ping_status = 'not_ok' WHERE ping_status IN ('not_okay')",
]

INDEX_SQL = [
    """
    CREATE INDEX IF NOT EXISTS ix_drivers_active_alert
    ON drivers (processor_id, loadcontroller_code)
    WHERE alert_status IN ('not_ok', 'not_okay') AND solved_time IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_sensors_active_alert
    ON sensors_and_modules (processor_id, device_code)
    WHERE alert_status = 'not_ok' AND solved_time IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_processor_active_alert
    ON processor (id)
    WHERE ping_status = 'not_ok' AND solved_time IS NULL
    """,
]


def main() -> None:
    db = SessionLocal()
    try:
        for sql in NORMALIZE_SQL:
            db.execute(text(sql))
        db.commit()
        print("Normalized legacy alert/ping status strings.")

        with engine.begin() as conn:
            for sql in INDEX_SQL:
                conn.execute(text(sql))
        print("Ensured partial indexes for active alerts.")
    except Exception as exc:
        db.rollback()
        print(f"Migration failed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
