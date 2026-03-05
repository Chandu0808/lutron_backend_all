"""
Script to add performance indexes to the occupancy_logs table.
Run this script once to add the indexes to your existing database.

Usage:
  1. Ensure your app's database engine is configured (environment/env as usual).
  2. From the backend root or migrations folder, run:
       python migrations/add_occupancy_indexes.py
"""

from app.database.session import engine
from sqlalchemy import text
import sys


def add_indexes():
    """Add performance indexes to occupancy_logs table"""

    indexes = [
        {
            "name": "ix_occupancy_logs_event_time",
            "sql": "CREATE INDEX IF NOT EXISTS ix_occupancy_logs_event_time "
                   "ON occupancy_logs(event_time)",
        },
        {
            "name": "ix_occupancy_logs_area_processor_time",
            "sql": "CREATE INDEX IF NOT EXISTS ix_occupancy_logs_area_processor_time "
                   "ON occupancy_logs(area_code, processor_id, event_time)",
        },
        {
            "name": "ix_occupancy_logs_status_time",
            "sql": "CREATE INDEX IF NOT EXISTS ix_occupancy_logs_status_time "
                   "ON occupancy_logs(occupation_status, event_time)",
        },
    ]

    try:
        with engine.connect() as connection:
            print("Adding performance indexes to occupancy_logs table...")
            print("-" * 60)

            for idx in indexes:
                try:
                    print(f"Creating index: {idx['name']}...", end=" ")
                    connection.execute(text(idx["sql"]))
                    connection.commit()
                    print("✓ Created successfully")
                except Exception as e:
                    print(f"✗ Error: {str(e)}")
                    # Continue with other indexes even if one fails
                    continue

            print("-" * 60)
            print("Index creation completed!")
            print("\nNote: Index creation may take some time on large tables.")
            print("You can verify the indexes were created by checking your database.")

    except Exception as e:
        print(f"Error connecting to database: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    add_indexes()

