#!/usr/bin/env python3
"""
Time Column Generator Utility
Generates time-based analysis columns from created_at timestamp

This utility handles auto-generation of:
- created_date: Date part of created_at
- timespan_15min: HHMM format (e.g., "2000" for 20:00)
- timespan_6hr: 6-hour bucket (0, 6, 12, or 18)
"""

from datetime import datetime, date
from typing import Tuple, Optional


def generate_time_columns(created_at: Optional[datetime]) -> Tuple[Optional[date], Optional[str], Optional[int]]:
    """
    Generate time-based analysis columns from created_at timestamp.
    
    Args:
        created_at: Datetime object (timezone-aware or naive)
    
    Returns:
        Tuple of (created_date, timespan_15min, timespan_6hr)
    
    Logic:
        - created_date: Date part of created_at
        - timespan_15min: HHMM format rounded to nearest 15 minutes (e.g., "2000" for 20:00)
        - timespan_6hr: 6-hour bucket based on hour ranges:
            - Hours 0-5: bucket 0
            - Hours 6-11: bucket 6
            - Hours 12-17: bucket 12
            - Hours 18-23: bucket 18
    """
    if created_at is None:
        print("[WARNING] generate_time_columns called with None created_at")
        return None, None, None
    
    try:
        # Extract date part
        created_date = created_at.date()
        
        # Extract hour and minute from local time
        # For timezone-aware datetime, hour is already in local timezone
        hour = created_at.hour
        minute = created_at.minute
        
        # Calculate timespan_15min (HHMM format, rounded down to nearest 15 minutes)
        minute_bucket = (minute // 15) * 15  # Round down: 0, 15, 30, or 45
        timespan_15min = f"{hour:02d}{minute_bucket:02d}"
        
        # Calculate timespan_6hr based on hour ranges
        if 0 <= hour < 6:
            timespan_6hr = 0
        elif 6 <= hour < 12:
            timespan_6hr = 6
        elif 12 <= hour < 18:
            timespan_6hr = 12
        elif 18 <= hour < 24:
            timespan_6hr = 18
        else:
            print(f"[WARNING] Invalid hour value: {hour} for created_at: {created_at}")
            timespan_6hr = None  # Should not happen, but safety check
        
        return created_date, timespan_15min, timespan_6hr
        
    except Exception as e:
        print(f"[CRITICAL ERROR] generate_time_columns failed: {e}")
        print(f"  Input created_at: {created_at}")
        print(f"  created_at type: {type(created_at)}")
        raise  # Re-raise to prevent silent failures


def apply_time_columns_to_record(record):
    """
    Apply generated time columns to a SQLAlchemy record object.
    
    Args:
        record: SQLAlchemy model instance with created_at attribute
    
    This function modifies the record in-place by setting:
        - record.created_date
        - record.timespan_15min
        - record.timespan_6hr
    """
    if hasattr(record, 'created_at') and record.created_at:
        created_date, timespan_15min, timespan_6hr = generate_time_columns(record.created_at)
        
        if hasattr(record, 'created_date'):
            record.created_date = created_date
        if hasattr(record, 'timespan_15min'):
            record.timespan_15min = timespan_15min
        if hasattr(record, 'timespan_6hr'):
            record.timespan_6hr = timespan_6hr

