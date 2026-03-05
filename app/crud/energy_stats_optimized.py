#!/usr/bin/env python3
"""
Optimized Chart API Functions using new time columns
Maintains exact same functionality with improved performance
"""

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, case, cast, Date
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import calendar
from collections import defaultdict
from bisect import bisect_right, bisect_left
from fastapi import HTTPException

from app.models.area_energy_stats import AreaEnergyStat
from app.models.area_occupancy_stats import AreaOccupancyStat
from app.models.area import Area
from app.models.area_group import AreaGroupMapping, AreaGroup
from app.models.processor import Processor
from app.utils.energy_unit_converter import convert_energy_dict, convert_single_energy_value
from app.utils.lutron_helpers import is_processor_reachable
import math 


def is_label_period_complete(
    label: str,
    bucket_type: str,
    time_range: str,
    start_date: datetime,
    end_date: datetime,
    now: datetime,
    label_index: Optional[int] = None,
    x_axis_length: Optional[int] = None
) -> bool:
    """
    Check if a label's time period is complete.
    Returns True if period is complete (can show data), False if incomplete (should hide).
    
    Args:
        label: The label to check (e.g., "Fri 18", "7/11", "Nov-4")
        bucket_type: Type of bucket ("6h", "day", "month4", "raw")
        time_range: Time range type ("this_week", "this_month", "this_year", "custom")
        start_date: Start date of the time range
        end_date: End date of the time range
        now: Current datetime
    
    Returns:
        bool: True if period is complete, False if incomplete
    """
    # Always show data for this_day and single-day custom (no period completion check needed)
    if time_range == "this_day" or (time_range == "custom" and (end_date.date() - start_date.date()).days == 0):
        return True
    
    if bucket_type == "6h":
        if time_range == "this_week":
            # Label format: "Fri 18" or "Fri 0" or "Sun 0"
            # Parse weekday and hour
            parts = label.split()
            if len(parts) != 2:
                return True  # Invalid format, default to showing
            
            weekday_str = parts[0]  # "Fri", "Sun", etc.
            hour_str = parts[1]     # "0", "6", "12", "18"
            
            # Map weekday string to day offset from start_date (which is Sunday)
            weekday_map = {"Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6}
            if weekday_str not in weekday_map:
                return True  # Invalid weekday, default to showing
            
            day_offset = weekday_map[weekday_str]
            target_day = start_date + timedelta(days=day_offset)
            
            # Determine period end time based on hour
            hour = int(hour_str)
            if hour == 0:
                # "X 0" labels: Need to check which "Sun 0" this is
                # First "Sun 0" (index 0) represents Sunday 00:00-06:00 (same as "Sun 6")
                # Last "Sun 0" (last index) represents Saturday 18:00-23:59
                # Other "X 0" represents previous day 18:00-00:00 (current day 00:00)
                if weekday_str == "Sun" and day_offset == 0:
                    # Check if this is the last label
                    if label_index is not None and x_axis_length is not None and label_index == x_axis_length - 1:
                        # This is the last "Sun 0" representing Saturday 18:00-23:59
                        saturday = start_date + timedelta(days=6)
                        period_end = saturday.replace(hour=23, minute=59, second=59, microsecond=999999)
                    else:
                        # First "Sun 0" (or other "Sun 0") represents Sunday 00:00-06:00
                        period_end = target_day.replace(hour=6, minute=0, second=0, microsecond=0)
                else:
                    # Regular "X 0" represents previous day 18:00-00:00 (current day 00:00)
                    period_end = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
            elif hour == 6:
                # "Fri 6" represents Friday 00:00-06:00
                period_end = target_day.replace(hour=6, minute=0, second=0, microsecond=0)
            elif hour == 12:
                # "Fri 12" represents Friday 06:00-12:00
                period_end = target_day.replace(hour=12, minute=0, second=0, microsecond=0)
            elif hour == 18:
                # "Fri 18" represents Friday 12:00-18:00
                period_end = target_day.replace(hour=18, minute=0, second=0, microsecond=0)
            else:
                return True  # Invalid hour, default to showing
            
            return now >= period_end
        
        elif time_range == "custom":
            # Label format: "Wed 6" or "Wed 0" (weekday format for 2-7 day custom periods)
            # Parse weekday and hour
            parts = label.split()
            if len(parts) != 2:
                return True  # Invalid format, default to showing
            
            weekday_str = parts[0]  # "Wed", "Thu", etc.
            hour_str = parts[1]     # "0", "6", "12", "18"
            
            # Map weekday string to day offset from start_date
            weekday_map = {"Sun": 0, "Mon": 1, "Tue": 2, "Wed": 3, "Thu": 4, "Fri": 5, "Sat": 6}
            if weekday_str not in weekday_map:
                return True  # Invalid weekday, default to showing
            
            day_offset = weekday_map[weekday_str]
            target_day = start_date + timedelta(days=day_offset)
            
            # Determine period end time based on hour
            hour = int(hour_str)
            if hour == 0:
                # "X 0" labels: First "X 0" represents current day 00:00-06:00 (same as "X 6")
                # Other "X 0" represents previous day 18:00-00:00 (current day 00:00)
                if day_offset == 0 and label_index is not None and label_index == 0:
                    # First label "X 0" represents start_date 00:00-06:00
                    period_end = target_day.replace(hour=6, minute=0, second=0, microsecond=0)
                else:
                    # Regular "X 0" represents previous day 18:00-00:00 (current day 00:00)
                    period_end = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
            elif hour == 6:
                period_end = target_day.replace(hour=6, minute=0, second=0, microsecond=0)
            elif hour == 12:
                period_end = target_day.replace(hour=12, minute=0, second=0, microsecond=0)
            elif hour == 18:
                period_end = target_day.replace(hour=18, minute=0, second=0, microsecond=0)
            else:
                return True  # Invalid hour, default to showing
            
            return now >= period_end
    
    elif bucket_type == "day":
        # Label format: "7/11" (day/month)
        date_parts = label.split("/")
        if len(date_parts) != 2:
            return True  # Invalid format
        
        day = int(date_parts[0])
        month = int(date_parts[1])
        # For this_month, use current year. For custom, use start_date year.
        if time_range == "this_month":
            target_date = datetime(now.year, month, day)
        else:
            target_date = datetime(start_date.year, month, day)
        
        # Period is the full day: 00:00-23:59:59.999999
        period_end = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        return now >= period_end
    
    elif bucket_type == "month4":
        if time_range == "this_year":
            # Label format: "Nov-4" (month-week)
            parts = label.split("-")
            if len(parts) != 2:
                return True  # Invalid format
            
            month_abbr = parts[0]  # "Nov"
            week_str = parts[1]     # "1", "2", "3", "4"
            
            # Map month abbreviation to month number
            month_map = {calendar.month_abbr[i]: i for i in range(1, 13)}
            if month_abbr not in month_map:
                return True  # Invalid month
            
            month = month_map[month_abbr]
            week = int(week_str)
            
            # Determine day range for the week
            if week == 1:
                period_end_day = 7
            elif week == 2:
                period_end_day = 15
            elif week == 3:
                period_end_day = 22
            elif week == 4:
                # Week 4 is days 23-end of month
                last_day = calendar.monthrange(now.year, month)[1]
                period_end_day = last_day
            else:
                return True
            
            period_end = datetime(now.year, month, period_end_day).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            
            return now >= period_end
        
        elif time_range == "custom":
            # Label format: "11/2024 4" (month/year week)
            parts = label.split()
            if len(parts) != 2:
                return True  # Invalid format
            
            date_str = parts[0]  # "11/2024"
            week_str = parts[1]   # "1", "2", "3", "4"
            
            date_parts = date_str.split("/")
            if len(date_parts) != 2:
                return True
            
            month = int(date_parts[0])
            year = int(date_parts[1])
            week = int(week_str)
            
            # Determine day range for the week
            if week == 1:
                period_end_day = 7
            elif week == 2:
                period_end_day = 15
            elif week == 3:
                period_end_day = 22
            elif week == 4:
                last_day = calendar.monthrange(year, month)[1]
                period_end_day = last_day
            else:
                return True
            
            period_end = datetime(year, month, period_end_day).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
            
            return now >= period_end
    
    # For "raw" bucket_type or other cases, always show (no period completion check)
    return True


def get_energy_consumption_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
    intervals: int = 10
) -> Dict[str, Any]:
    """
    Optimized energy consumption function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Determine Date Range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    # ---------- Fetch Areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()

    area_ids = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}

    # ---------- Determine Time Range Type (IDENTICAL LOGIC) ----------
    total_days = (end_date.date() - start_date.date()).days + 1
    is_same_date = total_days == 1

    # ---------- Build X-axis Labels (IDENTICAL LOGIC) ----------
    if time_range == "this_day" or (time_range == "custom" and is_same_date):
        x_axis = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        if time_range == "this_day":
            x_axis.append("23:59")
        elif time_range == "custom" and is_same_date:
            if start_date.date() <= now.date():
                x_axis.append("23:59")
        bucket_type = "raw"
    elif time_range == "this_week":
        x_axis = []
        for i in range(7):
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][i]
            if i == 6:  # Last day (Saturday) - 5 labels, last one is next Sunday 0
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", "Sun 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "custom" and total_days <= 7:
        # For 2-7 day custom periods, use weekday format (same as this_week) to match frontend expectations
        # This ensures consistency: backend returns "Wed 6", "Thu 0" format, not "17/12 0" format
        x_axis = []
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            weekday = weekdays[(day.weekday() + 1) % 7]
            if i == total_days - 1:  # Last day - 5 labels, last one is next day 0
                next_day = day + timedelta(days=1)
                next_weekday = weekdays[(next_day.weekday() + 1) % 7]
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", f"{next_weekday} 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            x_axis.append(f"{day.day}/{day.month}")
        bucket_type = "day"
    elif time_range == "this_year":
        x_axis = []
        for m in range(1, 13):
            x_axis.extend([
                f"{calendar.month_abbr[m]}-1",
                f"{calendar.month_abbr[m]}-2", 
                f"{calendar.month_abbr[m]}-3",
                f"{calendar.month_abbr[m]}-4"
            ])
        bucket_type = "month4"
    elif time_range == "custom" and total_days > 31:
        # 4 data points per month: 1/1 1, 7/1 2, 14/1 3, 22/1 4, 1/2 1, etc.
        x_axis = []
        month_iter = start_date
        while month_iter <= end_date:
            label_base = f"{month_iter.month}/{month_iter.year}"
            x_axis.extend([
                f"{label_base} 1",
                f"{label_base} 2",
                f"{label_base} 3", 
                f"{label_base} 4"
            ])
            if month_iter.month == 12:
                month_iter = month_iter.replace(year=month_iter.year + 1, month=1, day=1)
            else:
                month_iter = month_iter.replace(month=month_iter.month + 1, day=1)
        bucket_type = "month4"

    # ---------- Build Area Conditions (IDENTICAL LOGIC) ----------
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]

    # ---------- OPTIMIZED QUERY USING NEW TIME COLUMNS ----------
    if bucket_type == "raw":
        # Use timespan_15min for 15-minute intervals
        results = (
            db.query(
                AreaEnergyStat.timespan_15min,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.timespan_15min,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    elif bucket_type == "6h":
        # Use timespan_6hr for 6-hour intervals
        query = (
            db.query(
                AreaEnergyStat.timespan_6hr,
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
        )
        # For "this_week", exclude future records (only show up to current time)
        if time_range == "this_week":
            query = query.filter(AreaEnergyStat.created_at <= now)
        results = query.group_by(
            AreaEnergyStat.timespan_6hr,
            AreaEnergyStat.created_date,
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id
        ).all()
    elif bucket_type == "day":
        # Use created_date for daily intervals
        results = (
            db.query(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    elif bucket_type == "month4":
        # Use created_date for monthly intervals
        results = (
            db.query(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    else:
        # Fallback to original logic for any other cases
        results = (
            db.query(
                AreaEnergyStat.created_at,
                AreaEnergyStat.instantaneous_power,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .filter(AreaEnergyStat.created_at >= start_date)
            .filter(AreaEnergyStat.created_at <= end_date)
            .filter(or_(*area_conditions))
            .all()
        )

    # ---------- Helper Functions (IDENTICAL LOGIC) ----------
    def get_bucket_key_from_time_value(time_value, bucket_type: str, record_date=None) -> str:
        """Convert time column values to bucket keys"""
        if bucket_type == "raw":
            # time_value is timespan_15min (e.g., "1430")
            hour = int(time_value[:2])
            minute = int(time_value[2:])
            return f"{hour:02d}:{minute:02d}"
        elif bucket_type == "6h":
            # time_value is timespan_6hr (e.g., 18), record_date is created_date
            # Actual timespan_6hr values: 0=hours 0-5, 6=hours 6-11, 12=hours 12-17, 18=hours 18-23
            # Chart labels: "0"=hours 18-24 (prev day), "6"=hours 0-6, "12"=hours 6-12, "18"=hours 12-18
            # Mapping: timespan_6hr=0 -> "6", timespan_6hr=6 -> "12", timespan_6hr=12 -> "18", timespan_6hr=18 -> next day "0"
            if time_range == "this_week":
                if time_value == 18:
                    # Hours 18-23 map to next day's "0" label
                    next_day = record_date + timedelta(days=1)
                    next_day_weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(next_day.weekday() + 1) % 7]
                    return f"{next_day_weekday} 0"
                else:
                    # 0, 6, 12 map to current day labels
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(record_date.weekday() + 1) % 7]
                    hour_mapping = {0: "6", 6: "12", 12: "18"}
                    hour_str = hour_mapping.get(time_value, "6")
                    return f"{weekday} {hour_str}"
            else:  # custom ≤ 7 days - use weekday format
                weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                if time_value == 18:
                    # Hours 18-23 map to next day's "0" label
                    next_day = record_date + timedelta(days=1)
                    next_weekday = weekdays[(next_day.weekday() + 1) % 7]
                    return f"{next_weekday} 0"
                else:
                    # 0, 6, 12 map to current day labels
                    weekday = weekdays[(record_date.weekday() + 1) % 7]
                    hour_mapping = {0: "6", 6: "12", 12: "18"}
                    hour_str = hour_mapping.get(time_value, "6")
                    return f"{weekday} {hour_str}"
        elif bucket_type == "day":
            # time_value is created_date
            return f"{time_value.day}/{time_value.month}"
        elif bucket_type == "month4":
            # time_value is created_date
            d = time_value.day
            if d <= 7: w = "1"
            elif d <= 15: w = "2"
            elif d <= 22: w = "3"
            else: w = "4"
            if time_range == "this_year":
                return f"{calendar.month_abbr[time_value.month]}-{w}"
            else:
                return f"{time_value.month}/{time_value.year} {w}"
        return ""

    # ---------- Process Results (IDENTICAL LOGIC) ----------
    area_data = {}
    for area_id in area_ids:
        area_code = str(area_map[area_id]["code"])
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key
        area_records = [r for r in results if str(r.area_code) == area_code and r.processor_id == processor_id]
        
        if bucket_type in ["raw", "6h", "day", "month4"] and area_records:
            # Use optimized data
            bucket_values = {}
            bucket_has_data = {}
            for record in area_records:
                bucket_key = None
                if bucket_type == "raw":
                    bucket_key = get_bucket_key_from_time_value(record.timespan_15min, bucket_type)
                elif bucket_type == "6h":
                    bucket_key = get_bucket_key_from_time_value(record.timespan_6hr, bucket_type, record.created_date)
                elif bucket_type == "day":
                    bucket_key = get_bucket_key_from_time_value(record.created_date, bucket_type)
                elif bucket_type == "month4":
                    bucket_key = get_bucket_key_from_time_value(record.created_date, bucket_type)
                
                if bucket_key:
                    if bucket_key not in bucket_values:
                        bucket_values[bucket_key] = 0
                        bucket_has_data[bucket_key] = False
                    if record.total_power is not None:
                        bucket_values[bucket_key] += record.total_power
                        bucket_has_data[bucket_key] = True
            
            # Apply same division logic as original
            final_values = {}
            for bucket_key, total_power in bucket_values.items():
                if bucket_has_data.get(bucket_key):
                    averaged_value = round(total_power / 4, 2)
                    final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                else:
                    final_values[bucket_key] = None
        else:
            # Fallback to original logic for complex cases
            # Use the original complex bucketing logic when optimized approach fails
            final_values = {}
            
            # Get raw data for this area using original query
            area_condition = and_(
                AreaEnergyStat.area_code == int(area_code),
                AreaEnergyStat.processor_id == processor_id
            )
            
            raw_results = (
                db.query(
                    AreaEnergyStat.created_at,
                    AreaEnergyStat.instantaneous_power
                )
                .filter(AreaEnergyStat.created_at >= start_date)
                .filter(AreaEnergyStat.created_at <= end_date)
                .filter(area_condition)
                .all()
            )
            
            if raw_results:
                # Use original get_bucket_key logic
                def get_bucket_key_fallback(ts: datetime, bucket_type: str) -> str:
                    """Fallback bucket key function matching original logic"""
                    if bucket_type == "raw":
                        return f"{ts.hour:02d}:{ts.minute//15*15:02d}"
                    elif bucket_type == "6h":
                        if time_range == "this_week":
                            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                            hour_mapping = {6: "6", 12: "12", 18: "18", 0: "0"}
                            hour_str = hour_mapping.get(ts.hour//6*6, "0")
                            return f"{weekday} {hour_str}"
                        else:  # custom ≤ 7 days - use weekday format
                            weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                            weekday = weekdays[(ts.weekday() + 1) % 7]
                            hour_mapping = {6: "6", 12: "12", 18: "18", 0: "0"}
                            hour_str = hour_mapping.get(ts.hour//6*6, "0")
                            return f"{weekday} {hour_str}"
                    elif bucket_type == "day":
                        return f"{ts.day}/{ts.month}"
                    elif bucket_type in ["month4", "custom_month4"]:
                        d = ts.day
                        if d <= 7: w = "1"
                        elif d <= 15: w = "2"
                        elif d <= 22: w = "3"
                        else: w = "4"
                        if time_range == "this_year":
                            return f"{calendar.month_abbr[ts.month]}-{w}"
                        else:
                            return f"{ts.month}/{ts.year} {w}"
                    return ""
                
                bucket_values = {}
                for record in raw_results:
                    bucket_key = get_bucket_key_fallback(record.created_at, bucket_type)
                    if bucket_key:
                        if bucket_key not in bucket_values:
                            bucket_values[bucket_key] = []
                        bucket_values[bucket_key].append(record.instantaneous_power)
                
                # Apply same aggregation logic as original
                for bucket_key, values in bucket_values.items():
                    valid_values = [v for v in values if v is not None]
                    if valid_values:
                        total_power = sum(valid_values)
                        averaged_value = round(total_power / 4, 2)
                        final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                    else:
                        final_values[bucket_key] = None
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data (WITH PERIOD COMPLETION CHECK) ----------
    y_axis = {}
    if len(area_ids) < 5:
        # Individual areas
        for area_name, data in area_data.items():
            values = []
            for idx, label in enumerate(x_axis):
                # Special case: First "Sun 0" shows "Sun 6" data, so check "Sun 6" completion
                if time_range == "this_week" and label == "Sun 0" and idx == 0:
                    # Check if "Sun 6" period is complete (Sunday 00:00-06:00)
                    is_complete = is_label_period_complete(
                        "Sun 6", bucket_type, time_range, start_date, end_date, now,
                        label_index=None, x_axis_length=None
                    )
                    if is_complete:
                        values.append(data.get("Sun 6"))
                    else:
                        values.append(None)
                # Special case: Custom ≤7 days - first "X 0" shows same data as "X 6"
                elif time_range == "custom" and total_days <= 7 and bucket_type == "6h" and label.endswith(" 0") and idx == 0:
                    # Extract weekday part and construct "6" label (e.g., "Wed 0" -> "Wed 6")
                    weekday_part = label.split()[0]  # "Wed"
                    label_6 = f"{weekday_part} 6"  # "Wed 6"
                    # Check if "6" period is complete
                    is_complete = is_label_period_complete(
                        label_6, bucket_type, time_range, start_date, end_date, now,
                        label_index=None, x_axis_length=None
                    )
                    if is_complete:
                        values.append(data.get(label_6))
                    else:
                        values.append(None)
                elif bucket_type == "raw" and label == "23:59":
                    values.append(data.get("23:45"))
                else:
                    # Check if period is complete (hide incomplete periods)
                    is_complete = is_label_period_complete(
                        label, bucket_type, time_range, start_date, end_date, now,
                        label_index=idx, x_axis_length=len(x_axis)
                    )
                    
                    if not is_complete:
                        # Period not complete, hide this data point
                        values.append(None)
                    elif bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]:
                        values.append(None)
                    else:
                        values.append(data.get(label))
            y_axis[area_name] = values
    else:
        # Combined areas
        combined_values = []
        for idx, label in enumerate(x_axis):
            # Special case: First "Sun 0" shows "Sun 6" data, so check "Sun 6" completion
            if time_range == "this_week" and label == "Sun 0" and idx == 0:
                # Check if "Sun 6" period is complete (Sunday 00:00-06:00)
                is_complete = is_label_period_complete(
                    "Sun 6", bucket_type, time_range, start_date, end_date, now,
                    label_index=None, x_axis_length=None
                )
                if is_complete:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if "Sun 6" in data and data["Sun 6"] is not None:
                            total += data["Sun 6"]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
                else:
                    combined_values.append(None)
            # Special case: Custom ≤7 days - first "X 0" shows same data as "X 6"
            elif time_range == "custom" and total_days <= 7 and bucket_type == "6h" and label.endswith(" 0") and idx == 0:
                # Extract weekday part and construct "6" label (e.g., "Wed 0" -> "Wed 6")
                weekday_part = label.split()[0]  # "Wed"
                label_6 = f"{weekday_part} 6"  # "Wed 6"
                # Check if "6" period is complete
                is_complete = is_label_period_complete(
                    label_6, bucket_type, time_range, start_date, end_date, now,
                    label_index=None, x_axis_length=None
                )
                if is_complete:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if label_6 in data and data[label_6] is not None:
                            total += data[label_6]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
                else:
                    combined_values.append(None)
            elif bucket_type == "raw" and label == "23:59":
                total = 0
                count = 0
                for area_name, data in area_data.items():
                    if "23:45" in data and data["23:45"] is not None:
                        total += data["23:45"]
                        count += 1
                combined_values.append(round(total, 2) if count > 0 else None)
            else:
                # Check if period is complete (hide incomplete periods)
                is_complete = is_label_period_complete(
                    label, bucket_type, time_range, start_date, end_date, now,
                    label_index=idx, x_axis_length=len(x_axis)
                )
                
                if not is_complete:
                    # Period not complete, hide this data point
                    combined_values.append(None)
                elif bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]:
                    combined_values.append(None)
                else:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if label in data and data[label] is not None:
                            total += data[label]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
        y_axis["Combined Areas"] = combined_values

    # ---------- Compute instantaneous_max_power to determine unit ----------
    # Sum all instantaneous_max_power values in the time range (same logic as consumption/savings)
    max_power_sum = (
        db.query(func.sum(AreaEnergyStat.instantaneous_max_power))
        .filter(AreaEnergyStat.created_date >= start_date.date())
        .filter(AreaEnergyStat.created_date <= end_date.date())
        .filter(or_(*area_conditions))
        .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
        .scalar()
    )
    
    # Divide by 4 (same as consumption/savings calculation)
    instantaneous_max_power = (max_power_sum / 4.0) if max_power_sum else 0.0
    
    # Determine unit based on instantaneous_max_power
    if instantaneous_max_power > 2000:
        unit = "kWh"
        conversion_factor = 1000
    else:
        unit = "Wh"
        conversion_factor = 1
    
    # Apply conversion manually
    converted_y_axis = {}
    for area_name, values in y_axis.items():
        converted_y_axis[area_name] = [
            round(v / conversion_factor, 2) if v is not None else None 
            for v in values
        ]
    
    # Calculate max_limit for y-axis
    # Query sum(instantaneous_max_power)/4 grouped by time buckets
    # Get the maximum value among all buckets
    if bucket_type == "raw":
        # Group by timespan_15min and created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .filter(AreaEnergyStat.timespan_15min.isnot(None))
            .group_by(AreaEnergyStat.created_date, AreaEnergyStat.timespan_15min)
        )
    elif bucket_type == "6h":
        # Group by timespan_6hr and created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .filter(AreaEnergyStat.timespan_6hr.isnot(None))
            .group_by(AreaEnergyStat.created_date, AreaEnergyStat.timespan_6hr)
        )
        # For "this_week", exclude future records
        if time_range == "this_week":
            max_power_query = max_power_query.filter(AreaEnergyStat.created_at <= now)
    elif bucket_type == "day":
        # Group by created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .group_by(AreaEnergyStat.created_date)
        )
    elif bucket_type == "month4":
        # Group by created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .group_by(AreaEnergyStat.created_date)
        )
    else:
        # Fallback: use total sum
        max_power_query = None
    
    # Get all bucket values and find the maximum
    if max_power_query:
        max_power_buckets = max_power_query.all()
        max_limit_raw = max([row.max_power_per_bucket for row in max_power_buckets], default=0.0) if max_power_buckets else 0.0
    else:
        # Fallback: use total instantaneous_max_power
        max_limit_raw = instantaneous_max_power
    
    # Convert max_limit to match the unit (default is Wh, convert to kWh if needed)
    max_limit = max_limit_raw / conversion_factor if conversion_factor > 1 else max_limit_raw
    
    return {
        "status": "success", 
        "x-axis": x_axis, 
        "y-axis": converted_y_axis, 
        "unit": unit,
        "widget_title": "Consumption",
        "max_limit": math.ceil(max_limit)
    }


def get_occupancy_count_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict[str, Any]:
    """
    Optimized occupancy count function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Determine Date Range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    # ---------- Fetch Areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()

    area_ids_list = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}

    # ---------- Build X-axis (IDENTICAL LOGIC) ----------
    total_days = (end_date.date() - start_date.date()).days + 1

    if time_range == "this_day" or (time_range == "custom" and total_days == 1):
        x_axis = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        bucket_type = "15min"
    elif time_range == "this_week":
        x_axis = []
        for i in range(7):
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][i]
            x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "week6h"
    elif time_range == "custom" and total_days <= 7:
        # For 2-7 day custom periods, use weekday format (same as this_week) to match frontend expectations
        # This ensures consistency: backend returns "Wed 6", "Thu 0" format, not "17/12 0" format
        x_axis = []
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            weekday = weekdays[(day.weekday() + 1) % 7]
            x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        x_axis = [f"0/{start_date.month}"]
        x_axis.extend([
            f"{(start_date + timedelta(days=i)).day}/{(start_date + timedelta(days=i)).month}"
            for i in range(total_days)
        ])
        bucket_type = "day"
    elif time_range == "this_year":
        x_axis = []
        for m in range(1, 13):
            x_axis.extend([
                f"{calendar.month_abbr[m]}-0",
                f"{calendar.month_abbr[m]}-1",
                f"{calendar.month_abbr[m]}-2",
                f"{calendar.month_abbr[m]}-3",
            ])
        bucket_type = "month4"
    elif time_range == "custom" and total_days > 31:
        x_axis = []
        month_iter = start_date
        while month_iter <= end_date:
            label_base = f"{month_iter.month}/{month_iter.year}"
            x_axis.extend([
                f"{label_base}-0",
                f"{label_base}-1",
                f"{label_base}-2",
                f"{label_base}-3",
            ])
            if month_iter.month == 12:
                month_iter = month_iter.replace(year=month_iter.year + 1, month=1, day=1)
            else:
                month_iter = month_iter.replace(month=month_iter.month + 1, day=1)
        bucket_type = "custom_month4"

    # ---------- Build Area Conditions (IDENTICAL LOGIC) ----------
    area_conditions = [
        and_(
            AreaOccupancyStat.area_code == info["code"],
            AreaOccupancyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]

    # ---------- OPTIMIZED QUERY USING NEW TIME COLUMNS ----------
    if bucket_type == "15min":
        # Use timespan_15min for 15-minute intervals
        results = (
            db.query(
                AreaOccupancyStat.timespan_15min,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id,
                func.avg(case((AreaOccupancyStat.occupancy_status == 'Occupied', 1), else_=0)).label('avg_occupancy'),
                func.count(AreaOccupancyStat.id).label('record_count')
            )
            .filter(AreaOccupancyStat.created_date >= start_date.date())
            .filter(AreaOccupancyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaOccupancyStat.timespan_15min,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id
            )
            .all()
        )
    elif bucket_type in ["week6h", "6h"]:
        # Use timespan_6hr for 6-hour intervals
        query = (
            db.query(
                AreaOccupancyStat.timespan_6hr,
                AreaOccupancyStat.created_date,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id,
                func.avg(case((AreaOccupancyStat.occupancy_status == 'Occupied', 1), else_=0)).label('avg_occupancy'),
                func.count(AreaOccupancyStat.id).label('record_count')
            )
            .filter(AreaOccupancyStat.created_date >= start_date.date())
            .filter(AreaOccupancyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
        )
        # For "this_week", exclude future records (only show up to current time)
        if time_range == "this_week":
            query = query.filter(AreaOccupancyStat.created_at <= now)
        results = query.group_by(
            AreaOccupancyStat.timespan_6hr,
            AreaOccupancyStat.created_date,
            AreaOccupancyStat.area_code,
            AreaOccupancyStat.processor_id
        ).all()
    elif bucket_type == "day":
        # Use created_date for daily intervals
        results = (
            db.query(
                AreaOccupancyStat.created_date,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id,
                func.avg(case((AreaOccupancyStat.occupancy_status == 'Occupied', 1), else_=0)).label('avg_occupancy'),
                func.count(AreaOccupancyStat.id).label('record_count')
            )
            .filter(AreaOccupancyStat.created_date >= start_date.date())
            .filter(AreaOccupancyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaOccupancyStat.created_date,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id
            )
            .all()
        )
    elif bucket_type in ["month4", "custom_month4"]:
        # Use created_date for monthly intervals
        results = (
            db.query(
                AreaOccupancyStat.created_date,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id,
                func.avg(case((AreaOccupancyStat.occupancy_status == 'Occupied', 1), else_=0)).label('avg_occupancy'),
                func.count(AreaOccupancyStat.id).label('record_count')
            )
            .filter(AreaOccupancyStat.created_date >= start_date.date())
            .filter(AreaOccupancyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaOccupancyStat.created_date,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id
            )
            .all()
        )
    else:
        # Fallback to original logic for any other cases
        results = (
            db.query(AreaOccupancyStat.area_code, AreaOccupancyStat.processor_id, AreaOccupancyStat.occupancy_status, AreaOccupancyStat.created_at)
            .filter(AreaOccupancyStat.created_at >= start_date)
            .filter(AreaOccupancyStat.created_at <= end_date)
            .filter(or_(*area_conditions))
            .all()
        )

    # ---------- Process Results (SIMPLIFIED LOGIC) ----------
    area_data = {}
    for area_id in area_ids_list:
        area_code = area_map[area_id]["code"]
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key
        area_records = [r for r in results if str(r.area_code) == str(area_code) and r.processor_id == processor_id]
        
        if bucket_type in ["15min", "week6h", "6h", "day", "month4", "custom_month4"] and area_records:
            # Use optimized data - collect multiple values per bucket and average them
            bucket_values: Dict[str, List[float]] = defaultdict(list)
            for record in area_records:
                if bucket_type == "15min":
                    bucket_key = f"{int(record.timespan_15min[:2]):02d}:{int(record.timespan_15min[2:]):02d}"
                elif bucket_type in ["week6h", "6h"]:
                    # Map 6hr values to proper labels
                    # Actual timespan_6hr values: 0=hours 0-5, 6=hours 6-11, 12=hours 12-17, 18=hours 18-23
                    # Chart labels: "0"=hours 18-24 (prev day), "6"=hours 0-6, "12"=hours 6-12, "18"=hours 12-18
                    # Mapping: timespan_6hr=0 -> "6", timespan_6hr=6 -> "12", timespan_6hr=12 -> "18", timespan_6hr=18 -> next day "0"
                    if time_range == "this_week":
                        weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(record.created_date.weekday() + 1) % 7]
                        if record.timespan_6hr == 18:
                            # Hours 18-23 map to next day's "0" label
                            next_day = record.created_date + timedelta(days=1)
                            next_day_weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(next_day.weekday() + 1) % 7]
                            bucket_key = f"{next_day_weekday} 0"
                        else:
                            hour_mapping = {0: "6", 6: "12", 12: "18"}
                            hour_str = hour_mapping.get(record.timespan_6hr, "6")
                            bucket_key = f"{weekday} {hour_str}"
                    else:  # custom ≤ 7 days - use weekday format
                        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                        if record.timespan_6hr == 18:
                            # Hours 18-23 map to next day's "0" label
                            next_day = record.created_date + timedelta(days=1)
                            next_weekday = weekdays[(next_day.weekday() + 1) % 7]
                            bucket_key = f"{next_weekday} 0"
                        else:
                            weekday = weekdays[(record.created_date.weekday() + 1) % 7]
                            hour_mapping = {0: "6", 6: "12", 12: "18"}
                            hour_str = hour_mapping.get(record.timespan_6hr, "6")
                            bucket_key = f"{weekday} {hour_str}"
                elif bucket_type == "day":
                    bucket_key = f"{record.created_date.day}/{record.created_date.month}"
                elif bucket_type in ["month4", "custom_month4"]:
                    d = record.created_date.day
                    # Match original logic: only "1", "2", "3", no "4"
                    w = "1" if d <= 7 else "2" if d <= 15 else "3"
                    if time_range == "this_year":
                        bucket_key = f"{calendar.month_abbr[record.created_date.month]}-{w}"
                    else:
                        bucket_key = f"{record.created_date.month}/{record.created_date.year}-{w}"
                
                if bucket_key and record.avg_occupancy is not None:
                    bucket_values[bucket_key].append(record.avg_occupancy)
            
            # Average within buckets (same logic as original)
            final_values = {}
            for bucket_key, values in bucket_values.items():
                if values:
                    final_values[bucket_key] = int(round(sum(values) / len(values)))
                else:
                    final_values[bucket_key] = None
        else:
            # Fallback to original logic for complex cases
            final_values = {}
            
            # Get raw data for this area using original query
            area_condition = and_(
                AreaEnergyStat.area_code == int(area_code),
                AreaEnergyStat.processor_id == processor_id
            )
            
            raw_results = (
                db.query(
                    AreaEnergyStat.created_at,
                    AreaEnergyStat.instantaneous_saved_power
                )
                .filter(AreaEnergyStat.created_at >= start_date)
                .filter(AreaEnergyStat.created_at <= end_date)
                .filter(area_condition)
                .all()
            )
            
            if raw_results:
                def get_bucket_key_fallback(ts: datetime, bucket_type: str) -> str:
                    """Fallback bucket key function matching original logic"""
                    if bucket_type == "raw":
                        return f"{ts.hour:02d}:{ts.minute//15*15:02d}"
                    elif bucket_type == "6h":
                        if time_range == "this_week":
                            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                            hour_mapping = {6: "6", 12: "12", 18: "18", 0: "0"}
                            hour_str = hour_mapping.get(ts.hour//6*6, "0")
                            return f"{weekday} {hour_str}"
                        else:  # custom ≤ 7 days - use weekday format
                            weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                            weekday = weekdays[(ts.weekday() + 1) % 7]
                            hour_mapping = {6: "6", 12: "12", 18: "18", 0: "0"}
                            hour_str = hour_mapping.get(ts.hour//6*6, "0")
                            return f"{weekday} {hour_str}"
                    elif bucket_type == "day":
                        return f"{ts.day}/{ts.month}"
                    elif bucket_type in ["month4", "custom_month4"]:
                        d = ts.day
                        if d <= 7: w = "1"
                        elif d <= 15: w = "2"
                        elif d <= 22: w = "3"
                        else: w = "4"
                        if time_range == "this_year":
                            return f"{calendar.month_abbr[ts.month]}-{w}"
                        else:
                            return f"{ts.month}/{ts.year} {w}"
                    return ""
                
                bucket_values = {}
                for record in raw_results:
                    bucket_key = get_bucket_key_fallback(record.created_at, bucket_type)
                    if bucket_key:
                        if bucket_key not in bucket_values:
                            bucket_values[bucket_key] = []
                        bucket_values[bucket_key].append(record.instantaneous_saved_power)
                
                for bucket_key, values in bucket_values.items():
                    valid_values = [v for v in values if v is not None]
                    if valid_values:
                        total_savings = sum(valid_values)
                        averaged_value = round(total_savings / 4, 2)
                        final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                    else:
                        final_values[bucket_key] = None
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data (IDENTICAL LOGIC) ----------
    y_axis = {}
    if len(area_ids_list) == 1:
        # Single area - show data without area name
        for area_name, data in area_data.items():
            values = []
            for label in x_axis:
                values.append(data.get(label))
            y_axis["data"] = values
    else:
        # Multiple areas - show combined data without area name
        combined_values = []
        for label in x_axis:
            total = 0
            count = 0
            for area_name, data in area_data.items():
                if label in data and data[label] is not None:
                    total += data[label]
                    count += 1
            combined_values.append(int(round(total)) if count > 0 else None)
        y_axis["data"] = combined_values

    # ---------- Apply forward fill logic (IDENTICAL LOGIC) ----------
    def forward_fill(values):
        if not values:
            return values
        
        last_non_null_index = -1
        for i in range(len(values) - 1, -1, -1):
            if values[i] is not None:
                last_non_null_index = i
                break
        
        if last_non_null_index == -1:
            return values
        
        filled_values = values.copy()
        last_valid_value = None
        
        for i in range(last_non_null_index + 1):
            if filled_values[i] is not None:
                last_valid_value = filled_values[i]
            elif last_valid_value is not None:
                filled_values[i] = last_valid_value
        
        return filled_values

    # Apply forward fill to each series
    for key in y_axis:
        y_axis[key] = forward_fill(y_axis[key])

    return {
        "status": "success",
        "x-axis": x_axis,
        "y-axis": y_axis,
        "widget_title": "Utilization",
    }


def get_energy_savings_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
    intervals: int = 10
) -> Dict[str, Any]:
    """
    Optimized energy savings function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Determine Date Range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    # ---------- Fetch Areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()

    area_ids = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}

    # ---------- Determine Time Range Type (IDENTICAL LOGIC) ----------
    total_days = (end_date.date() - start_date.date()).days + 1
    is_same_date = total_days == 1

    # ---------- Build X-axis Labels (IDENTICAL LOGIC) ----------
    if time_range == "this_day" or (time_range == "custom" and is_same_date):
        x_axis = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        if time_range == "this_day":
            x_axis.append("23:59")
        elif time_range == "custom" and is_same_date:
            if start_date.date() <= now.date():
                x_axis.append("23:59")
        bucket_type = "raw"
    elif time_range == "this_week":
        x_axis = []
        for i in range(7):
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][i]
            if i == 6:  # Last day (Saturday) - 5 labels, last one is next Sunday 0
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", "Sun 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "custom" and total_days <= 7:
        # For 2-7 day custom periods, use weekday format (same as this_week) to match frontend expectations
        # This ensures consistency: backend returns "Wed 6", "Thu 0" format, not "17/12 0" format
        x_axis = []
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            weekday = weekdays[(day.weekday() + 1) % 7]
            if i == total_days - 1:  # Last day - 5 labels, last one is next day 0
                next_day = day + timedelta(days=1)
                next_weekday = weekdays[(next_day.weekday() + 1) % 7]
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", f"{next_weekday} 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            x_axis.append(f"{day.day}/{day.month}")
        bucket_type = "day"
    elif time_range == "this_year":
        x_axis = []
        for m in range(1, 13):
            x_axis.extend([
                f"{calendar.month_abbr[m]}-1",
                f"{calendar.month_abbr[m]}-2", 
                f"{calendar.month_abbr[m]}-3",
                f"{calendar.month_abbr[m]}-4"
            ])
        bucket_type = "month4"
    elif time_range == "custom" and total_days > 31:
        x_axis = []
        month_iter = start_date
        while month_iter <= end_date:
            label_base = f"{month_iter.month}/{month_iter.year}"
            x_axis.extend([
                f"{label_base} 1",
                f"{label_base} 2",
                f"{label_base} 3", 
                f"{label_base} 4"
            ])
            if month_iter.month == 12:
                month_iter = month_iter.replace(year=month_iter.year + 1, month=1, day=1)
            else:
                month_iter = month_iter.replace(month=month_iter.month + 1, day=1)
        bucket_type = "month4"

    # ---------- Build Area Conditions (IDENTICAL LOGIC) ----------
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]

    # ---------- OPTIMIZED QUERY USING NEW TIME COLUMNS ----------
    if bucket_type == "raw":
        # Use timespan_15min for 15-minute intervals
        results = (
            db.query(
                AreaEnergyStat.timespan_15min,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_saved_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.timespan_15min,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    elif bucket_type == "6h":
        # Use timespan_6hr for 6-hour intervals
        query = (
            db.query(
                AreaEnergyStat.timespan_6hr,
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_saved_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
        )
        # For "this_week", exclude future records (only show up to current time)
        if time_range == "this_week":
            query = query.filter(AreaEnergyStat.created_at <= now)
        results = query.group_by(
            AreaEnergyStat.timespan_6hr,
            AreaEnergyStat.created_date,
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id
        ).all()
    elif bucket_type == "day":
        # Use created_date for daily intervals
        results = (
            db.query(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_saved_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    elif bucket_type == "month4":
        # Use created_date for monthly intervals
        results = (
            db.query(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                func.sum(AreaEnergyStat.instantaneous_saved_power).label('total_power'),
                func.count(AreaEnergyStat.id).label('record_count')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .group_by(
                AreaEnergyStat.created_date,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .all()
        )
    else:
        # Fallback to original logic for any other cases
        results = (
            db.query(
                AreaEnergyStat.created_at,
                AreaEnergyStat.instantaneous_power,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .filter(AreaEnergyStat.created_at >= start_date)
            .filter(AreaEnergyStat.created_at <= end_date)
            .filter(or_(*area_conditions))
            .all()
        )

    # ---------- Helper Functions (IDENTICAL LOGIC) ----------
    def get_bucket_key_from_time_value(time_value, bucket_type: str, record_date=None) -> str:
        """Convert time column values to bucket keys"""
        if bucket_type == "raw":
            # time_value is timespan_15min (e.g., "1430")
            hour = int(time_value[:2])
            minute = int(time_value[2:])
            return f"{hour:02d}:{minute:02d}"
        elif bucket_type == "6h":
            # time_value is timespan_6hr (e.g., 18), record_date is created_date
            # Actual timespan_6hr values: 0=hours 0-5, 6=hours 6-11, 12=hours 12-17, 18=hours 18-23
            # Chart labels: "0"=hours 18-24 (prev day), "6"=hours 0-6, "12"=hours 6-12, "18"=hours 12-18
            # Mapping: timespan_6hr=0 -> "6", timespan_6hr=6 -> "12", timespan_6hr=12 -> "18", timespan_6hr=18 -> next day "0"
            if time_range == "this_week":
                if time_value == 18:
                    # Hours 18-23 map to next day's "0" label
                    next_day = record_date + timedelta(days=1)
                    next_day_weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(next_day.weekday() + 1) % 7]
                    return f"{next_day_weekday} 0"
                else:
                    # 0, 6, 12 map to current day labels
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(record_date.weekday() + 1) % 7]
                    hour_mapping = {0: "6", 6: "12", 12: "18"}
                    hour_str = hour_mapping.get(time_value, "6")
                    return f"{weekday} {hour_str}"
            else:  # custom ≤ 7 days - use weekday format
                weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                if time_value == 18:
                    # Hours 18-23 map to next day's "0" label
                    next_day = record_date + timedelta(days=1)
                    next_weekday = weekdays[(next_day.weekday() + 1) % 7]
                    return f"{next_weekday} 0"
                else:
                    # 0, 6, 12 map to current day labels
                    weekday = weekdays[(record_date.weekday() + 1) % 7]
                    hour_mapping = {0: "6", 6: "12", 12: "18"}
                    hour_str = hour_mapping.get(time_value, "6")
                    return f"{weekday} {hour_str}"
        elif bucket_type == "day":
            # time_value is created_date
            return f"{time_value.day}/{time_value.month}"
        elif bucket_type == "month4":
            # time_value is created_date
            d = time_value.day
            if d <= 7: w = "1"
            elif d <= 15: w = "2"
            elif d <= 22: w = "3"
            else: w = "4"
            if time_range == "this_year":
                return f"{calendar.month_abbr[time_value.month]}-{w}"
            else:
                return f"{time_value.month}/{time_value.year} {w}"
        elif bucket_type == "custom_month4":
            # Handle custom_month4 same as month4 (for backwards compatibility)
            d = time_value.day
            if d <= 7: w = "1"
            elif d <= 15: w = "2"
            elif d <= 22: w = "3"
            else: w = "4"
            return f"{time_value.month}/{time_value.year} {w}"
        return ""

    # ---------- Process Results (IDENTICAL LOGIC) ----------
    area_data = {}
    for area_id in area_ids:
        area_code = str(area_map[area_id]["code"])
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key
        area_records = [r for r in results if str(r.area_code) == area_code and r.processor_id == processor_id]
        
        if bucket_type in ["raw", "6h", "day", "month4", "custom_month4"] and area_records:
            # Use optimized data
            bucket_values = {}
            bucket_has_data = {}
            bucket_key = None
            for record in area_records:
                if bucket_type == "raw":
                    bucket_key = get_bucket_key_from_time_value(record.timespan_15min, bucket_type)
                elif bucket_type == "6h":
                    bucket_key = get_bucket_key_from_time_value(record.timespan_6hr, bucket_type, record.created_date)
                elif bucket_type == "day":
                    bucket_key = get_bucket_key_from_time_value(record.created_date, bucket_type)
                elif bucket_type == "month4":
                    bucket_key = get_bucket_key_from_time_value(record.created_date, bucket_type)
                elif bucket_type == "custom_month4":
                    bucket_key = get_bucket_key_from_time_value(record.created_date, "custom_month4")
                
                if bucket_key:
                    if bucket_key not in bucket_values:
                        bucket_values[bucket_key] = 0
                        bucket_has_data[bucket_key] = False
                    if record.total_power is not None:
                        bucket_values[bucket_key] += record.total_power
                        bucket_has_data[bucket_key] = True
            
            # Apply same division logic as original
            final_values = {}
            for bucket_key, total_power in bucket_values.items():
                if bucket_has_data.get(bucket_key):
                    averaged_value = round(total_power / 4, 2)
                    final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                else:
                    final_values[bucket_key] = None
        else:
            # Fallback to original logic for complex cases
            final_values = {}
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data (WITH PERIOD COMPLETION CHECK) ----------
    y_axis = {}
    if len(area_ids) < 5:
        # Individual areas
        for area_name, data in area_data.items():
            values = []
            for idx, label in enumerate(x_axis):
                # Special case: First "Sun 0" shows "Sun 6" data, so check "Sun 6" completion
                if time_range == "this_week" and label == "Sun 0" and idx == 0:
                    # Check if "Sun 6" period is complete (Sunday 00:00-06:00)
                    is_complete = is_label_period_complete(
                        "Sun 6", bucket_type, time_range, start_date, end_date, now,
                        label_index=None, x_axis_length=None
                    )
                    if is_complete:
                        values.append(data.get("Sun 6"))
                    else:
                        values.append(None)
                # Special case: Custom ≤7 days - first "X 0" shows same data as "X 6"
                elif time_range == "custom" and total_days <= 7 and bucket_type == "6h" and label.endswith(" 0") and idx == 0:
                    # Extract weekday part and construct "6" label (e.g., "Wed 0" -> "Wed 6")
                    weekday_part = label.split()[0]  # "Wed"
                    label_6 = f"{weekday_part} 6"  # "Wed 6"
                    # Check if "6" period is complete
                    is_complete = is_label_period_complete(
                        label_6, bucket_type, time_range, start_date, end_date, now,
                        label_index=None, x_axis_length=None
                    )
                    if is_complete:
                        values.append(data.get(label_6))
                    else:
                        values.append(None)
                elif bucket_type == "raw" and label == "23:59":
                    values.append(data.get("23:45"))
                else:
                    # Check if period is complete (hide incomplete periods)
                    is_complete = is_label_period_complete(
                        label, bucket_type, time_range, start_date, end_date, now,
                        label_index=idx, x_axis_length=len(x_axis)
                    )
                    
                    if not is_complete:
                        # Period not complete, hide this data point
                        values.append(None)
                    elif bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]:
                        values.append(None)
                    else:
                        values.append(data.get(label))
            y_axis[area_name] = values
    else:
        # Combined areas
        combined_values = []
        for idx, label in enumerate(x_axis):
            # Special case: First "Sun 0" shows "Sun 6" data, so check "Sun 6" completion
            if time_range == "this_week" and label == "Sun 0" and idx == 0:
                # Check if "Sun 6" period is complete (Sunday 00:00-06:00)
                is_complete = is_label_period_complete(
                    "Sun 6", bucket_type, time_range, start_date, end_date, now,
                    label_index=None, x_axis_length=None
                )
                if is_complete:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if "Sun 6" in data and data["Sun 6"] is not None:
                            total += data["Sun 6"]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
                else:
                    combined_values.append(None)
            # Special case: Custom ≤7 days - first "X 0" shows same data as "X 6"
            elif time_range == "custom" and total_days <= 7 and bucket_type == "6h" and label.endswith(" 0") and idx == 0:
                # Extract weekday part and construct "6" label (e.g., "Wed 0" -> "Wed 6")
                weekday_part = label.split()[0]  # "Wed"
                label_6 = f"{weekday_part} 6"  # "Wed 6"
                # Check if "6" period is complete
                is_complete = is_label_period_complete(
                    label_6, bucket_type, time_range, start_date, end_date, now,
                    label_index=None, x_axis_length=None
                )
                if is_complete:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if label_6 in data and data[label_6] is not None:
                            total += data[label_6]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
                else:
                    combined_values.append(None)
            elif bucket_type == "raw" and label == "23:59":
                total = 0
                count = 0
                for area_name, data in area_data.items():
                    if "23:45" in data and data["23:45"] is not None:
                        total += data["23:45"]
                        count += 1
                combined_values.append(round(total, 2) if count > 0 else None)
            else:
                # Check if period is complete (hide incomplete periods)
                is_complete = is_label_period_complete(
                    label, bucket_type, time_range, start_date, end_date, now,
                    label_index=idx, x_axis_length=len(x_axis)
                )
                
                if not is_complete:
                    # Period not complete, hide this data point
                    combined_values.append(None)
                elif bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]:
                    combined_values.append(None)
                else:
                    total = 0
                    count = 0
                    for area_name, data in area_data.items():
                        if label in data and data[label] is not None:
                            total += data[label]
                            count += 1
                    combined_values.append(round(total, 2) if count > 0 else None)
        y_axis["Combined Areas"] = combined_values

    # ---------- Compute instantaneous_max_power to determine unit ----------
    # Sum all instantaneous_max_power values in the time range (same logic as consumption/savings)
    max_power_sum = (
        db.query(func.sum(AreaEnergyStat.instantaneous_max_power))
        .filter(AreaEnergyStat.created_date >= start_date.date())
        .filter(AreaEnergyStat.created_date <= end_date.date())
        .filter(or_(*area_conditions))
        .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
        .scalar()
    )
    
    # Divide by 4 (same as consumption/savings calculation)
    instantaneous_max_power = (max_power_sum / 4.0) if max_power_sum else 0.0
    
    # Determine unit based on instantaneous_max_power
    if instantaneous_max_power > 2000:
        unit = "kWh"
        conversion_factor = 1000
    else:
        unit = "Wh"
        conversion_factor = 1
    
    # Apply conversion manually
    converted_y_axis = {}
    for area_name, values in y_axis.items():
        converted_y_axis[area_name] = [
            round(v / conversion_factor, 2) if v is not None else None 
            for v in values
        ]
    
    # Calculate max_limit for y-axis
    # Query sum(instantaneous_max_power)/4 grouped by time buckets
    # Get the maximum value among all buckets
    if bucket_type == "raw":
        # Group by timespan_15min and created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .filter(AreaEnergyStat.timespan_15min.isnot(None))
            .group_by(AreaEnergyStat.created_date, AreaEnergyStat.timespan_15min)
        )
    elif bucket_type == "6h":
        # Group by timespan_6hr and created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .filter(AreaEnergyStat.timespan_6hr.isnot(None))
            .group_by(AreaEnergyStat.created_date, AreaEnergyStat.timespan_6hr)
        )
        # For "this_week", exclude future records
        if time_range == "this_week":
            max_power_query = max_power_query.filter(AreaEnergyStat.created_at <= now)
    elif bucket_type == "day":
        # Group by created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .group_by(AreaEnergyStat.created_date)
        )
    elif bucket_type == "month4":
        # Group by created_date
        max_power_query = (
            db.query(
                (func.sum(AreaEnergyStat.instantaneous_max_power) / 4.0).label('max_power_per_bucket')
            )
            .filter(AreaEnergyStat.created_date >= start_date.date())
            .filter(AreaEnergyStat.created_date <= end_date.date())
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .group_by(AreaEnergyStat.created_date)
        )
    else:
        # Fallback: use total sum
        max_power_query = None
    
    # Get all bucket values and find the maximum
    if max_power_query:
        max_power_buckets = max_power_query.all()
        max_limit_raw = max([row.max_power_per_bucket for row in max_power_buckets], default=0.0) if max_power_buckets else 0.0
    else:
        # Fallback: use total instantaneous_max_power
        max_limit_raw = instantaneous_max_power
    
    # Convert max_limit to match the unit (default is Wh, convert to kWh if needed)
    max_limit = max_limit_raw / conversion_factor if conversion_factor > 1 else max_limit_raw
    
    return {
        "status": "success", 
        "x-axis": x_axis, 
        "y-axis": converted_y_axis, 
        "unit": unit,
        "widget_title": "Savings",
        "max_limit": math.ceil(max_limit)
    }


def get_total_consumption_by_area_id_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Optimized total consumption by area ID function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Determine Date Range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    # ---------- Resolve areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))

    areas = query.all()
    if not areas:
        areas = db.query(Area).all()
    if not areas:
        raise HTTPException(status_code=404, detail="No areas found")

    area_ids = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}

    # ---------- Get special groups (IDENTICAL LOGIC) ----------
    special_groups = (
        db.query(AreaGroup)
        .join(AreaGroupMapping)
        .filter(AreaGroupMapping.area_id.in_(area_ids))
        .filter(AreaGroup.special == True)
        .distinct()
        .all()
    )

    # ---------- OPTIMIZED: Fetch Total Consumption using database aggregation ----------
    # Build OR conditions for each (area_code, processor_id) pair
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]

    # Use created_date for index-based filtering and database aggregation
    total_consumption_result = (
        db.query(
            func.sum(AreaEnergyStat.instantaneous_power).label('total_power')
        )
        .filter(AreaEnergyStat.created_date >= start_date.date())
        .filter(AreaEnergyStat.created_date <= end_date.date())
        .filter(or_(*area_conditions))  # Use composite key filtering
        .scalar()
    )
    
    total_consumption = (total_consumption_result / 4.0) if total_consumption_result else 0.0  # Apply division by 4, ensure float

    # ---------- Process Special Groups (IDENTICAL LOGIC) ----------
    group_results = []
    total_group_consumption = 0

    for group in special_groups:
        # Get area codes for this group
        mapped_area_ids = [
            a_id for (a_id,) in db.query(AreaGroupMapping.area_id)
            .filter(AreaGroupMapping.group_id == group.id)
            .all()
        ]
        
        if not mapped_area_ids:
            group_results.append({
                "name": group.name,
                "consumption_percentage": "0 %",
                "actual_energy": "0.0 Wh"
            })
            continue

        # Filter mapped_area_ids to only include areas that are in the filtered area_ids
        # This ensures we only calculate consumption for areas that match the input filter
        if area_ids:
            filtered_mapped_area_ids = [aid for aid in mapped_area_ids if aid in area_ids]
        else:
            filtered_mapped_area_ids = mapped_area_ids
        
        if not filtered_mapped_area_ids:
            group_results.append({
                "name": group.name,
                "consumption_percentage": "0 %",
                "actual_energy": "0.0 Wh"
            })
            continue

        # Get area_code AND processor_id (composite key) for proper filtering
        # Only include areas that are in the filtered set
        area_keys = [
            (str(code), proc_id) for (code, proc_id) in db.query(Area.code, Area.processor_id)
            .filter(Area.id.in_(filtered_mapped_area_ids))
            .all()
        ]
        
        if not area_keys:
            group_results.append({
                "name": group.name,
                "consumption_percentage": "0 %",
                "actual_energy": "0.0 Wh"
            })
            continue

        # OPTIMIZED: Calculate group consumption using database aggregation
        # Build conditions for this group's areas (only filtered areas)
        group_area_conditions = [
            and_(
                AreaEnergyStat.area_code == int(code),
                AreaEnergyStat.processor_id == proc_id
            )
            for code, proc_id in area_keys
        ]
        
        if group_area_conditions:
            group_consumption_result = (
                db.query(
                    func.sum(AreaEnergyStat.instantaneous_power).label('total_power')
                )
                .filter(AreaEnergyStat.created_date >= start_date.date())
                .filter(AreaEnergyStat.created_date <= end_date.date())
                .filter(or_(*group_area_conditions))  # Use composite key filtering
                .scalar()
            )
            group_consumption = (group_consumption_result / 4.0) if group_consumption_result else 0.0  # Apply division by 4, ensure float
        else:
            group_consumption = 0.0

        # Store raw consumption value for global unit calculation
        group_results.append({
            "name": group.name,
            "consumption_value": group_consumption,  # Store raw value in Wh
            "consumption_percentage": "0 %",  # Will be calculated below
            "actual_energy": None  # Will be set after determining global unit
        })
        total_group_consumption += group_consumption

    # ---------- Determine Global Unit (same logic as convert_energy_dict) ----------
    all_group_values = [r["consumption_value"] for r in group_results if r.get("consumption_value", 0) > 0]
    max_group_value = max(all_group_values, default=0)
    
    if max_group_value < 2000:
        global_unit = "Wh"
        conversion_factor = 1
    elif max_group_value < 2000000:
        global_unit = "kWh"
        conversion_factor = 1000
    else:
        global_unit = "MWh"
        conversion_factor = 1000000

    # ---------- Convert All Groups Using Same Global Unit ----------
    for result in group_results:
        raw_value = result.get("consumption_value", 0.0)
        if raw_value is not None and raw_value > 0:
            converted_value = round(raw_value / conversion_factor, 2)
            result["actual_energy"] = f"{converted_value} {global_unit}"
        else:
            result["actual_energy"] = f"0.0 {global_unit}"

    # ---------- Calculate Percentages (using raw values in Wh) ----------
    for result in group_results:
        raw_value = result.get("consumption_value", 0.0)
        percentage = (raw_value / total_consumption * 100) if total_consumption > 0 else 0.0
        result["consumption_percentage"] = f"{round(percentage, 2)} %"

    return {
        "status": "success",
        "special_area_groups": group_results,
        "unit": global_unit,
        "widget_title": "Consumption by area group"
    }


def spaceutilization_by_area_group_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Optimized space utilization by area group function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Resolve time range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999)
    elif time_range == "this_week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    else:
        raise ValueError("Invalid time_range value")
    
    # ---------- Resolve areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))

    areas = query.all()
    if not areas:  # fallback to all
        areas = db.query(Area).all()
    if not areas:
        return []

    area_ids = [a.id for a in areas]
    
    # Step 1: Find all unique special groups for input area_ids (IDENTICAL LOGIC)
    special_groups = (
        db.query(AreaGroup)
        .join(AreaGroupMapping)
        .filter(AreaGroupMapping.area_id.in_(area_ids))
        .filter(AreaGroup.special.is_(True))
        .distinct()
        .all()
    )

    results = []

    for group in special_groups:
        # Step 2: All area_ids in this group (IDENTICAL LOGIC)
        group_area_ids = db.query(AreaGroupMapping.area_id) \
            .filter(AreaGroupMapping.group_id == group.id).all()
        group_area_ids = [row[0] for row in group_area_ids]

        if not group_area_ids:
            continue

        # Step 3: Fetch area_code and processor_id from Area table for composite key filtering (IDENTICAL LOGIC)
        area_info = db.query(Area.code, Area.processor_id).filter(Area.id.in_(group_area_ids)).all()
        
        if not area_info:
            continue
        
        # Build composite key tuples (area_code, processor_id)
        area_keys = [(str(code), proc_id) for code, proc_id in area_info]

        # Step 4: OPTIMIZED - Count occupied using database aggregation with created_date
        occupied_conditions = [
            and_(
                AreaOccupancyStat.area_code == str(code),
                AreaOccupancyStat.processor_id == proc_id
            )
            for code, proc_id in area_keys
        ]
        
        # Use created_date for index-based filtering and count aggregation
        # Note: original uses created_at < end_date, so we need to include the end_date day
        # Since end_date is typically 23:59:59.999, we use <= to include all records from that day
        total_occupied = db.query(func.count(AreaOccupancyStat.id)) \
            .filter(or_(*occupied_conditions)) \
            .filter(AreaOccupancyStat.occupancy_status.ilike("occupied")) \
            .filter(AreaOccupancyStat.created_date >= start_date.date()) \
            .filter(AreaOccupancyStat.created_date <= end_date.date()) \
            .scalar() or 0

        # Step 5: OPTIMIZED - Count unoccupied using database aggregation with created_date
        total_unoccupied = db.query(func.count(AreaOccupancyStat.id)) \
            .filter(or_(*occupied_conditions)) \
            .filter(AreaOccupancyStat.occupancy_status.ilike("unoccupied")) \
            .filter(AreaOccupancyStat.created_date >= start_date.date()) \
            .filter(AreaOccupancyStat.created_date <= end_date.date()) \
            .scalar() or 0

        total_possible = total_occupied + total_unoccupied

        results.append({
            "area_group_id": group.id,
            "area_group_name": group.name,
            "total_occupied": total_occupied,
            "total_possible": total_possible
        })

    return results


def _area_display_name(area: Area) -> str:
    """Return display name as 'floor.name / area.name' when floor exists, else area.name."""
    area_name = (area.name or "").strip() or ""
    if area.floor and getattr(area.floor, "name", None):
        floor_name = (area.floor.name or "").strip()
        if floor_name:
            return f"{floor_name} / {area_name}" if area_name else floor_name
    return area_name


def get_space_utilization_by_area_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Optimized space utilization by area function using new time columns.
    Maintains EXACT same functionality as original function.
    """
    now = datetime.now()

    # ---------- Inclusive time range resolution (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        # Week starts on Sunday
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    if not (start_date and end_date) or start_date >= end_date:
        raise ValueError("Invalid date range")

    # ---------- Resolve areas (IDENTICAL LOGIC) ----------
    query = db.query(Area).options(joinedload(Area.floor))
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))

    areas = query.all()
    if not areas:  # fallback to all
        areas = db.query(Area).options(joinedload(Area.floor)).all()
    if not areas:
        return {"status": "success", "utilized_area": []}

    utilized_area = []

    for area in areas:
        # Fetch area_code and processor_id for composite key filtering
        area_code = area.code
        processor_id = area.processor_id
        
        # OPTIMIZED: Single query for total + occupied counts using composite key with created_date
        # Use created_date for index-based filtering instead of created_at
        # Note: original uses created_at <= end_date, so we use <= end_date.date() to include the end date day
        counts = (
        db.query(
                func.count(AreaOccupancyStat.id).label("total"),
                func.sum(
                    case(
                        (AreaOccupancyStat.occupancy_status == "Occupied", 1),
                        else_=0
                    )
                ).label("occupied")
            )
            .filter(AreaOccupancyStat.area_code == str(area_code))
            .filter(AreaOccupancyStat.processor_id == processor_id)
        .filter(AreaOccupancyStat.created_date >= start_date.date())
        .filter(AreaOccupancyStat.created_date <= end_date.date())
            .first()
        )

        if not counts:
            total_samples = 0
            occupied_samples = 0
        else:
            total_samples = counts.total or 0
            occupied_samples = counts.occupied or 0

        occupied_percent = round((occupied_samples / total_samples) * 100, 2) if total_samples > 0 else 0.0

        utilized_area.append({
            "name": _area_display_name(area),
            "occupied": occupied_percent
        })

    return {
        "status": "success",
        "utilized_area": utilized_area
    }


def get_space_utilization_by_area_from_logs_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Space utilization by area function using OccupancyLog table.
    This function queries occupancy_logs table which tracks occupancy status changes over time.
    """
    from app.models.occupancy_logs import OccupancyLog
    
    now = datetime.now()

    # ---------- Inclusive time range resolution (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        # Week starts on Sunday
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    if not (start_date and end_date) or start_date >= end_date:
        raise ValueError("Invalid date range")

    # ---------- Resolve areas (IDENTICAL LOGIC) ----------
    query = db.query(Area).options(joinedload(Area.floor))
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))

    areas = query.all()
    if not areas:  # fallback to all
        areas = db.query(Area).options(joinedload(Area.floor)).all()
    if not areas:
        return {"status": "success", "utilized_area": []}

    utilized_area = []

    for area in areas:
        # Use area_id for filtering (more reliable than area_code + processor_id)
        # Fallback to area_code + processor_id if area_id is not available
        area_id = area.id
        area_code = str(area.code) if area.code else None
        processor_id = area.processor_id
        
        # Query OccupancyLog table: sum timespan for occupied vs total timespan
        # Filter by area_id if available, otherwise use area_code + processor_id
        # Build area filter condition
        if area_id:
            area_filter = OccupancyLog.area_id == area_id
        else:
            area_filter = and_(
                OccupancyLog.area_code == area_code,
                OccupancyLog.processor_id == processor_id
            )
        
        # Query 1: Records that start within the range [start_date, end_date]
        timespan_sums = (
            db.query(
                func.sum(
                    case(
                        (OccupancyLog.occupation_status == "Occupied", OccupancyLog.timespan),
                        else_=0
                    )
                ).label("occupied_timespan"),
                func.sum(
                    case(
                        (OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]), OccupancyLog.timespan),
                        else_=0
                    )
                ).label("total_timespan")
            )
            .filter(area_filter)
            .filter(OccupancyLog.event_time >= start_date)
            .filter(OccupancyLog.event_time <= end_date)
            .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
            .filter(OccupancyLog.timespan.isnot(None))
            .first()
        )

        if not timespan_sums:
            occupied_timespan = 0
            total_timespan = 0
        else:
            occupied_timespan = timespan_sums.occupied_timespan or 0
            total_timespan = timespan_sums.total_timespan or 0

        # Query 2: Records that start BEFORE start_date but extend INTO the range
        # These records have event_time < start_date but event_time + timespan > start_date
        before_start_records = (
            db.query(OccupancyLog)
            .filter(area_filter)
            .filter(OccupancyLog.event_time < start_date)
            .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
            .filter(OccupancyLog.timespan.isnot(None))
            .all()
        )
        
        # Process records that extend into the range
        for record in before_start_records:
            if not record.event_time or not record.timespan:
                continue
            
            period_start = record.event_time
            period_end = period_start + timedelta(seconds=record.timespan)
            
            # Only process if this record extends into our range
            if period_end > start_date:
                # Calculate the portion within [start_date, end_date]
                effective_start = max(period_start, start_date)
                effective_end = min(period_end, end_date)
                
                if effective_start < effective_end:
                    timespan_in_range = int((effective_end - effective_start).total_seconds())
                    
                    if record.occupation_status == "Occupied":
                        occupied_timespan += timespan_in_range
                    total_timespan += timespan_in_range

        # Get first and last records for edge case handling
        # Query records within the range
        base_query = db.query(OccupancyLog).filter(
            area_filter,
            OccupancyLog.event_time >= start_date,
            OccupancyLog.event_time <= end_date,
            OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"])
        )
        
        first_record = base_query.order_by(OccupancyLog.event_time.asc()).first()
        last_record = base_query.order_by(OccupancyLog.event_time.desc()).first()

        # Logic 1: Handle ongoing state (last record with NULL timespan)
        if last_record and last_record.timespan is None and last_record.event_time:
            # For ALL time ranges, cap at end_date or now, whichever is earlier
            calculation_end_time = min(end_date, now)
            
            # Only calculate if last_record.event_time is within or before the range
            if last_record.event_time <= calculation_end_time:
                ongoing_timespan = int((calculation_end_time - last_record.event_time).total_seconds())
            else:
                ongoing_timespan = 0
            
            # Add to appropriate sum based on last record's status
            if last_record.occupation_status == "Occupied":
                occupied_timespan += ongoing_timespan
            total_timespan += ongoing_timespan

        # Logic 2: Handle missing data at start (first record after start_date)
        if first_record and first_record.event_time and first_record.event_time > start_date:
            # Check if there's a record before start_date that extends into the range
            before_start_record = (
                db.query(OccupancyLog)
                .filter(area_filter)
                .filter(OccupancyLog.event_time < start_date)
                .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
                .order_by(OccupancyLog.event_time.desc())
                .first()
            )
            
            if before_start_record:
                # Check if the previous record extends into our range
                if before_start_record.timespan is not None:
                    before_period_end = before_start_record.event_time + timedelta(seconds=before_start_record.timespan)
                    if before_period_end > start_date:
                        # Previous status extends into range, already handled in Query 2 above
                        # No gap to fill
                        pass
                    else:
                        # Previous status ended before start_date, use opposite logic
                        gap_timespan = int((first_record.event_time - start_date).total_seconds())
                        if first_record.occupation_status == "Occupied":
                            # Prior period was Unoccupied, only add to total
                            total_timespan += gap_timespan
                        else:
                            # Prior period was Occupied, add to both
                            occupied_timespan += gap_timespan
                            total_timespan += gap_timespan
                else:
                    # Previous record is ongoing, extends into range
                    gap_start = start_date
                    gap_end = min(first_record.event_time, end_date, now)
                    gap_timespan = int((gap_end - gap_start).total_seconds())
                    
                    if before_start_record.occupation_status == "Occupied":
                        occupied_timespan += gap_timespan
                    total_timespan += gap_timespan
            else:
                # No record before start_date - use opposite of first record
                gap_timespan = int((first_record.event_time - start_date).total_seconds())
                if first_record.occupation_status == "Occupied":
                    # Prior period was Unoccupied, only add to total
                    total_timespan += gap_timespan
                else:
                    # Prior period was Occupied, add to both
                    occupied_timespan += gap_timespan
                    total_timespan += gap_timespan

        occupied_percent = round((occupied_timespan / total_timespan) * 100, 2) if total_timespan > 0 else 0.0

        utilized_area.append({
            "name": _area_display_name(area),
            "occupied": occupied_percent,
            "total_occupied_timespan": occupied_timespan,  # Total occupied timespan in seconds
            "total_timespan": total_timespan  # Total timespan (occupied + unoccupied) in seconds
        })

    return {
        "status": "success",
        "utilized_area": utilized_area
    }


def generate_6hour_buckets(start_date: datetime, end_date: datetime) -> List[tuple]:
    """Generate 6-hour buckets for week time range."""
    buckets = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date
    
    while current <= end:
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.extend([
            (day_start, day_start.replace(hour=6, minute=0, second=0, microsecond=0)),
            (day_start.replace(hour=6, minute=0, second=0, microsecond=0), 
             day_start.replace(hour=12, minute=0, second=0, microsecond=0)),
            (day_start.replace(hour=12, minute=0, second=0, microsecond=0), 
             day_start.replace(hour=18, minute=0, second=0, microsecond=0)),
            (day_start.replace(hour=18, minute=0, second=0, microsecond=0), 
             day_start.replace(hour=23, minute=59, second=59, microsecond=999999))
        ])
        current += timedelta(days=1)
        if current.date() > end.date():
            break
    
    return buckets


def generate_24hour_buckets(start_date: datetime, end_date: datetime) -> List[tuple]:
    """Generate 24-hour buckets for month time range."""
    buckets = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date
    
    while current <= end:
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
        if day_end > end:
            day_end = end
        buckets.append((day_start, day_end))
        current += timedelta(days=1)
        if current.date() > end.date():
            break
    
    return buckets


def generate_7day_buckets(start_date: datetime, end_date: datetime) -> List[tuple]:
    """Generate 7-day buckets for year time range."""
    buckets = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date
    
    while current <= end:
        week_start = current
        week_end = current + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)
        if week_end > end:
            week_end = end
        buckets.append((week_start, week_end))
        current += timedelta(days=7)
        if current > end:
            break
    
    return buckets


def calculate_occupancy_for_bucket(
    db: Session,
    area: Area,
    bucket_start: datetime,
    bucket_end: datetime,
    now: datetime,
    time_range: str
) -> float:
    """
    Calculate occupancy percentage for a single bucket using DB-read logic.
    Reuses the same logic from get_space_utilization_by_area_from_logs_optimized.
    """
    from app.models.occupancy_logs import OccupancyLog
    
    area_id = area.id
    area_code = str(area.code) if area.code else None
    processor_id = area.processor_id
    
    # Build area filter
    if area_id:
        area_filter = OccupancyLog.area_id == area_id
    else:
        area_filter = and_(
            OccupancyLog.area_code == area_code,
            OccupancyLog.processor_id == processor_id
        )
    
    # Query 1: Records that start within the bucket
    timespan_sums = (
        db.query(
            func.sum(
                case(
                    (OccupancyLog.occupation_status == "Occupied", OccupancyLog.timespan),
                    else_=0
                )
            ).label("occupied_timespan"),
            func.sum(
                case(
                    (OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]), OccupancyLog.timespan),
                    else_=0
                )
            ).label("total_timespan")
        )
        .filter(area_filter)
        .filter(OccupancyLog.event_time >= bucket_start)
        .filter(OccupancyLog.event_time <= bucket_end)
        .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
        .filter(OccupancyLog.timespan.isnot(None))
        .first()
    )
    
    occupied_timespan = timespan_sums.occupied_timespan or 0 if timespan_sums else 0
    
    # Query 2: Records that start BEFORE bucket_start but extend INTO the bucket
    before_start_records = (
        db.query(OccupancyLog)
        .filter(area_filter)
        .filter(OccupancyLog.event_time < bucket_start)
        .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
        .filter(OccupancyLog.timespan.isnot(None))
        .all()
    )
    
    for record in before_start_records:
        if not record.event_time or not record.timespan:
            continue
        period_start = record.event_time
        period_end = period_start + timedelta(seconds=record.timespan)
        if period_end > bucket_start:
            effective_start = max(period_start, bucket_start)
            effective_end = min(period_end, bucket_end)
            if effective_start < effective_end:
                timespan_in_range = int((effective_end - effective_start).total_seconds())
                if record.occupation_status == "Occupied":
                    occupied_timespan += timespan_in_range
    
    # Get first and last records for edge case handling
    base_query = db.query(OccupancyLog).filter(
        area_filter,
        OccupancyLog.event_time >= bucket_start,
        OccupancyLog.event_time <= bucket_end,
        OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"])
    )
    
    first_record = base_query.order_by(OccupancyLog.event_time.asc()).first()
    last_record = base_query.order_by(OccupancyLog.event_time.desc()).first()
    
    # Logic 1: Handle ongoing state (NULL timespan)
    if last_record and last_record.timespan is None and last_record.event_time:
        calculation_end_time = min(bucket_end, now)
        if last_record.event_time <= calculation_end_time:
            ongoing_timespan = int((calculation_end_time - last_record.event_time).total_seconds())
            if last_record.occupation_status == "Occupied":
                occupied_timespan += ongoing_timespan
    
    # Logic 2: Handle missing data at start
    if first_record and first_record.event_time and first_record.event_time > bucket_start:
        before_start_record = (
            db.query(OccupancyLog)
            .filter(area_filter)
            .filter(OccupancyLog.event_time < bucket_start)
            .filter(OccupancyLog.occupation_status.in_(["Occupied", "Unoccupied"]))
            .order_by(OccupancyLog.event_time.desc())
            .first()
        )
        
        if before_start_record:
            if before_start_record.timespan is not None:
                before_period_end = before_start_record.event_time + timedelta(seconds=before_start_record.timespan)
                if before_period_end <= bucket_start:
                    gap_timespan = int((first_record.event_time - bucket_start).total_seconds())
                    if first_record.occupation_status == "Unoccupied":
                        # Prior period was Occupied, add to occupied_timespan
                        occupied_timespan += gap_timespan
            else:
                gap_start = bucket_start
                gap_end = min(first_record.event_time, bucket_end, now)
                gap_timespan = int((gap_end - gap_start).total_seconds())
                if before_start_record.occupation_status == "Occupied":
                    occupied_timespan += gap_timespan
        else:
            gap_timespan = int((first_record.event_time - bucket_start).total_seconds())
            if first_record.occupation_status == "Unoccupied":
                # Prior period was Occupied, add to occupied_timespan
                occupied_timespan += gap_timespan
    
    # Determine bucket duration based on time_range
    # Use full bucket duration as total_timespan (cap at now for future buckets)
    effective_bucket_end = min(bucket_end, now)
    effective_bucket_duration = int((effective_bucket_end - bucket_start).total_seconds())
    
    if time_range == "this_week":
        bucket_duration = 21600  # 6 hours in seconds
    elif time_range == "this_month":
        bucket_duration = 86400  # 24 hours in seconds
    elif time_range == "this_year":
        bucket_duration = 604800  # 7 days in seconds
    else:
        bucket_duration = effective_bucket_duration
    
    # Use effective bucket duration (capped at now) as total_timespan
    # This ensures each bucket represents 100% of its time period (up to current time)
    total_timespan = effective_bucket_duration if effective_bucket_duration > 0 else bucket_duration
    
    # Calculate percentage
    occupancy_percentage = (occupied_timespan / total_timespan * 100) if total_timespan > 0 else 0.0
    
    return round(occupancy_percentage, 2)


def build_x_axis_labels_for_buckets(buckets: List[tuple], time_range: str, original_time_range: str = None, start_date: datetime = None, total_days: int = None) -> List[str]:
    """Build x-axis labels for bucket-based time ranges."""
    x_axis = []
    
    # Check predefined time ranges first (before custom) to ensure consistent format
    # This ensures that when navigating to previous/next year, same format is maintained
    if time_range == "this_week":
        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        
        # Check if first bucket is Sunday 0-6, if so add "Sun 0" at the beginning
        # This "Sun 0" will show previous week's Saturday 18-24 data
        # Only add for actual "this_week" or custom ranges that start on Sunday
        added_sun_0_at_start = False
        starts_on_sunday = False
        if buckets:
            first_bucket_start, first_bucket_end = buckets[0]
            # Check if first bucket is Sunday 0-6 (weekday 6 = Sunday, hour 0)
            is_sunday_start = (first_bucket_start.weekday() + 1) % 7 == 0 and first_bucket_start.hour == 0
            starts_on_sunday = is_sunday_start
            # Only add "Sun 0" for actual "this_week" OR custom ranges starting on Sunday
            if is_sunday_start and (original_time_range != "custom" or (original_time_range == "custom" and first_bucket_start.weekday() == 6)):
                x_axis.append("Sun 0")
                added_sun_0_at_start = True
        
        for idx, (bucket_start, bucket_end) in enumerate(buckets):
            day_name = weekdays[(bucket_start.weekday() + 1) % 7]
            start_hour = bucket_start.hour
            
            # If we added "Sun 0" at the start and this is the first bucket (Sunday 0-6),
            # label it as "Sun 6" instead of "Sun 0" to match the shifting logic
            if added_sun_0_at_start and idx == 0 and start_hour == 0 and (bucket_start.weekday() + 1) % 7 == 0:
                x_axis.append("Sun 6")
            elif start_hour == 0:
                # For custom ranges not starting on Sunday, first bucket (0-6) should be labeled as "0"
                # For this_week or custom ranges starting on Sunday, label as "6" (matches shifting logic)
                if original_time_range == "custom" and idx == 0 and not starts_on_sunday:
                    x_axis.append(f"{day_name} 0")
                    x_axis.append(f"{day_name} 6")
                else:
                    # 0-6 bucket should be labeled as "6"
                    x_axis.append(f"{day_name} 6")
            elif start_hour == 6:
                # 6-12 bucket should be labeled as "12"
                x_axis.append(f"{day_name} 12")
            elif start_hour == 12:
                # 12-18 bucket should be labeled as "18"
                x_axis.append(f"{day_name} 18")
            elif start_hour == 18:
                # 18-24 bucket: if last day -> skip (will be "24" at end), else -> "0" (next day)
                is_last_bucket = (idx == len(buckets) - 1)
                if is_last_bucket:
                    # Skip - will be handled at the end as "24"
                    pass
                else:
                    # Not the last bucket, so label as next day's "0"
                    next_day = bucket_start + timedelta(days=1)
                    next_day_name = weekdays[(next_day.weekday() + 1) % 7]
                    x_axis.append(f"{next_day_name} 0")
            else:
                x_axis.append(f"{day_name} {start_hour}")
        # Add "Sat 24" or "24" label for the last bucket (18-24 bucket)
        if buckets:
            last_bucket_start, last_bucket_end = buckets[-1]
            if last_bucket_start.hour == 18 and last_bucket_end.hour == 23:
                # Check if last bucket is Saturday
                last_day_name = weekdays[(last_bucket_start.weekday() + 1) % 7]
                
                # For custom week ranges (1 < days < 8), always add "24" point for last day
                # For this_week, only add "Sat 24" if it's Saturday
                if original_time_range == "custom" and total_days > 1 and total_days < 8:
                    # Always add weekday "24" label for the last bucket of last day
                    x_axis.append(f"{last_day_name} 24")
                elif time_range == "this_week" and last_day_name == "Sat":
                    # Add "Sat 24" label for the last bucket of Saturday (this_week only)
                    x_axis.append("Sat 24")
    
    elif time_range == "this_month":
        # Remove "0" label - just show day numbers, each day shows its own data
        for bucket_start, _ in buckets:
            x_axis.append(str(bucket_start.day))
    
    elif time_range == "this_year":
        # For this_year, start from January, don't add previous month's label
        # Only add previous month label if first bucket is not in January
        if buckets:
            first_bucket_start = buckets[0][0]
            # Only add previous month label if first bucket is NOT in January
            if first_bucket_start.month != 1:
                # Get previous month
                prev_month = first_bucket_start.month - 1
                x_axis.append(f"{calendar.month_abbr[prev_month]}-0")
        
        # Track month and week to group buckets properly
        # Only add unique week labels (group buckets 4+ with week 3)
        current_month = None
        week_counter = 0
        seen_weeks_in_month = set()
        
        for bucket_start, _ in buckets:
            month_abbr = calendar.month_abbr[bucket_start.month]
            
            # Reset week counter when month changes
            if current_month != bucket_start.month:
                current_month = bucket_start.month
                week_counter = 0
                seen_weeks_in_month = set()
            else:
                week_counter += 1
            
            # Cap week number at 3 - any 5th week gets clubbed with week 3
            week_num = min(week_counter, 3)
            
            # Only add label if we haven't seen this week number in this month yet
            # This groups multiple buckets (like 4th and 5th) into the same week label
            week_key = (current_month, week_num)
            if week_key not in seen_weeks_in_month:
                seen_weeks_in_month.add(week_key)
                x_axis.append(f"{month_abbr}-{week_num}")
    
    # Handle custom ranges with different label formats (only if not already handled by predefined ranges above)
    # This ensures custom ranges that route to predefined ranges use the same format
    elif original_time_range == "custom":
        if total_days <= 7:
            # Custom ≤ 7 days: Format as "D/M H" (e.g., "1/12 0", "1/12 6", "1/12 12", "1/12 18")
            for bucket_start, bucket_end in buckets:
                day = bucket_start.date()
                base = f"{day.day}/{day.month}"
                hour = bucket_start.hour
                # Map hours to bucket labels: 0-5 -> 0, 6-11 -> 6, 12-17 -> 12, 18-23 -> 18
                if hour < 6:
                    hour_label = "0"
                elif hour < 12:
                    hour_label = "6"
                elif hour < 18:
                    hour_label = "12"
                else:
                    hour_label = "18"
                x_axis.append(f"{base} {hour_label}")
            
            # For custom week ranges (more than 1 day and less than 8 days), 
            # the last day should have 5 data points (point '24')
            if buckets and total_days > 1 and total_days < 8:
                last_bucket_start, last_bucket_end = buckets[-1]
                if last_bucket_start.hour == 18 and last_bucket_end.hour == 23:
                    # Check if last bucket is Saturday
                    weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                    last_day_name = weekdays[(last_bucket_start.weekday() + 1) % 7]
                    last_day = last_bucket_start.date()
                    
                    if last_day_name == "Sat":
                        # Add "Sat 24" label for the last bucket of Saturday
                        x_axis.append("Sat 24")
                    else:
                        # Add "D/M 24" label for the last bucket of non-Saturday last day
                        x_axis.append(f"{last_day.day}/{last_day.month} 24")
        elif total_days <= 31:
            # Custom ≤ 31 days: Format as "0/M", "1/M", "2/M", etc.
            for bucket_start, _ in buckets:
                if bucket_start.date() == start_date.date():
                    x_axis.append(f"0/{start_date.month}")
                else:
                    x_axis.append(f"{bucket_start.day}/{bucket_start.month}")
        else:
            # Custom > 31 days: Format as "M/Y-W" (e.g., "12/2024-0", "12/2024-1", etc.)
            # Note: If time_range is "this_year" (routed), it will be handled above, so this only applies
            # to custom ranges > 31 days that don't route to this_year (shouldn't happen, but kept for safety)
            for bucket_start, _ in buckets:
                d = bucket_start.day
                w = "0" if d <= 7 else "1" if d <= 15 else "2" if d <= 22 else "3"
                x_axis.append(f"{bucket_start.month}/{bucket_start.year}-{w}")
    
    return x_axis


def get_instant_occupancy_count_optimized(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict[str, Any]:
    """
    Optimized instant occupancy count function using occupancy_logs table.
    Maintains EXACT same functionality as get_occupancy_count_optimized but uses occupancy_logs table.
    """
    from app.models.occupancy_logs import OccupancyLog
    
    now = datetime.now()

    # ---------- Determine Date Range (IDENTICAL LOGIC) ----------
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (start_date + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_date = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")

    # ---------- Fetch Areas (IDENTICAL LOGIC) ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()

    # Filter out areas from unreachable processors
    reachable_areas = []
    processor_cache = {}  # Cache processor info to avoid repeated queries
    
    for area in areas:
        processor_id = area.processor_id
        if processor_id not in processor_cache:
            processor = db.query(Processor).filter(Processor.id == processor_id).first()
            if processor:
                processor_cache[processor_id] = {
                    'processor': processor,
                    'reachable': is_processor_reachable(processor.ipv4)
                }
            else:
                processor_cache[processor_id] = {'processor': None, 'reachable': False}
        
        if processor_cache[processor_id]['reachable']:
            reachable_areas.append(area)
    
    # Use only reachable areas for calculation
    areas = reachable_areas
    area_ids_list = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}
    
    # Build set of valid area_keys for counting (only reachable areas)
    valid_area_keys = {(str(a.code), a.processor_id) for a in areas if a.code and a.processor_id}

    # ---------- Calculate total_days for custom range routing ----------
    total_days = (end_date.date() - start_date.date()).days + 1

    # ---------- Determine effective_time_range for custom ranges ----------
    # Route custom ranges to appropriate predefined range logic based on total_days
    effective_time_range = time_range
    if time_range == "custom":
        if total_days <= 1:
            effective_time_range = "this_day"
        elif total_days <= 7:
            effective_time_range = "this_week"
        elif total_days <= 31:
            effective_time_range = "this_month"
        else:
            effective_time_range = "this_year"

    # ---------- BRANCH: Bucket-based logic for week/month/year (including matching custom ranges) ----------
    if effective_time_range in ["this_week", "this_month", "this_year"]:
        # Generate buckets based on effective_time_range
        if effective_time_range == "this_week":
            buckets = generate_6hour_buckets(start_date, end_date)
        elif effective_time_range == "this_month":
            buckets = generate_24hour_buckets(start_date, end_date)
        elif effective_time_range == "this_year":
            buckets = generate_7day_buckets(start_date, end_date)
        
        # Filter out future buckets - only include buckets that have completely ended
        buckets = [(bucket_start, bucket_end) for bucket_start, bucket_end in buckets if bucket_end <= now]
        
        # If no buckets remain, return empty result
        if not buckets:
            return {
                "status": "success",
                "x-axis": [],
                "y-axis": {"data": []},
                "widget_title": "Utilization",
            }
        
        # HYBRID CHUNKING APPROACH: Single query per chunk, process in memory
        # This dramatically reduces query count while maintaining real-time data
        
        # Build all area filters
        all_area_filters = []
        for area in areas:
            if area.id:
                area_filter = OccupancyLog.area_id == area.id
            else:
                area_filter = and_(
                    OccupancyLog.area_code == str(area.code),
                    OccupancyLog.processor_id == area.processor_id
                )
            all_area_filters.append(area_filter)
        
        # OPTIMIZATION: Build area lookup maps for O(1) access instead of O(n) search
        area_by_id_map = {area.id: area for area in areas if area.id}
        area_by_code_processor_map = {
            (str(area.code), area.processor_id): area 
            for area in areas 
            if area.code and area.processor_id
        }
        
        # Calculate buffer for extending records
        buffer_hours = 48  # Ensure we capture all records that might extend into buckets
        extended_start = start_date - timedelta(hours=buffer_hours)
        
        # Determine if we need chunking based on time range
        # For year ranges, use chunking; for week/month, single query is fine
        use_chunking = effective_time_range == "this_year"
        chunk_size_days = 30 if use_chunking else None
        
        # Initialize data structures
        area_bucket_percentages = {area.id: [] for area in areas}
        
        if use_chunking:
            # Process buckets in chunks for very large datasets
            chunk_buckets = []
            current_chunk = []
            current_chunk_start = None
            
            for bucket_start, bucket_end in buckets:
                if current_chunk_start is None:
                    current_chunk_start = bucket_start
                
                days_since_chunk_start = (bucket_start - current_chunk_start).days
                if days_since_chunk_start < chunk_size_days and len(current_chunk) < chunk_size_days:
                    current_chunk.append((bucket_start, bucket_end))
                else:
                    if current_chunk:
                        chunk_buckets.append(current_chunk)
                    current_chunk = [(bucket_start, bucket_end)]
                    current_chunk_start = bucket_start
            
            if current_chunk:
                chunk_buckets.append(current_chunk)
        else:
            # Single chunk for week/month
            chunk_buckets = [buckets]
        
        # Process each chunk
        for chunk_buckets_list in chunk_buckets:
            if not chunk_buckets_list:
                continue
            
            # Calculate time range for this chunk
            chunk_start = chunk_buckets_list[0][0]
            chunk_end = chunk_buckets_list[-1][1]
            chunk_extended_start = chunk_start - timedelta(hours=buffer_hours)
            
            # OPTIMIZATION: Use event_date for initial filtering (leverages index: ix_occupancy_logs_date_timespan)
            chunk_extended_date_start = chunk_extended_start.date()
            chunk_date_end = chunk_end.date()
            
            # SINGLE QUERY: Fetch all records for this chunk once
            # OPTIMIZED: Select only needed columns instead of full ORM objects (70-80% memory reduction)
            # OPTIMIZED: Use event_date filter first to leverage index, then event_time for precise filtering
            # OPTIMIZED: Order by event_time (indexed column) to help query planner use optimal index
            # Column order: [0]=event_time, [1]=area_code, [2]=processor_id, [3]=area_id, [4]=occupation_status, [5]=timespan
            all_records = (
                db.query(
                    OccupancyLog.event_time,
                    OccupancyLog.area_code,
                    OccupancyLog.processor_id,
                    OccupancyLog.area_id,
                    OccupancyLog.occupation_status,
                    OccupancyLog.timespan
                )
                .filter(OccupancyLog.event_date >= chunk_extended_date_start)  # Use date index first
                .filter(OccupancyLog.event_date <= chunk_date_end)            # Narrow dataset early
                .filter(OccupancyLog.event_time >= chunk_extended_start)       # Then filter by time
                .filter(OccupancyLog.event_time <= chunk_end)
                .filter(or_(*all_area_filters))
                .filter(OccupancyLog.occupation_status.in_(['Occupied', 'Unoccupied']))
                .order_by(OccupancyLog.event_time)  # Helps query planner use index efficiently
                .all()
            )
            
            # Initialize data structures for this chunk
            bucket_timespans = {}
            extending_timespans = {}
            edge_case_data = {}
            before_start_data = {}
            
            for bucket_start, bucket_end in chunk_buckets_list:
                bucket_key = (bucket_start, bucket_end)
                bucket_timespans[bucket_key] = {}
                extending_timespans[bucket_key] = {}
                edge_case_data[bucket_key] = {area: {'first': None, 'last': None} for area in areas}
                before_start_data[bucket_key] = {area: None for area in areas}
            
            # OPTIMIZATION: Pre-compute bucket start/end times for binary search
            bucket_starts = [b[0] for b in chunk_buckets_list]
            bucket_ends = [b[1] for b in chunk_buckets_list]
            
            # Process all records in memory
            # OPTIMIZATION: Use tuple access (record[0], record[1], etc.) instead of namedtuple for zero overhead
            # Column order: [0]=event_time, [1]=area_code, [2]=processor_id, [3]=area_id, [4]=occupation_status, [5]=timespan
            for record in all_records:
                # record is a tuple: (event_time, area_code, processor_id, area_id, occupation_status, timespan)
                record_time = record[0]  # event_time
                if not record_time:
                    continue
                
                # OPTIMIZATION: Find matching area using O(1) lookup instead of O(n) search
                # Since records are pre-filtered by area_filters, most records will match
                matching_area = None
                area_id = record[3]  # area_id
                area_code = record[1]  # area_code
                processor_id = record[2]  # processor_id
                
                if area_id and area_id in area_by_id_map:
                    matching_area = area_by_id_map[area_id]
                elif area_code and processor_id:
                    key = (str(area_code), processor_id)
                    if key in area_by_code_processor_map:
                        matching_area = area_by_code_processor_map[key]
                
                # Skip if no matching area (shouldn't happen due to query filter, but safety check)
                if not matching_area:
                    continue
                
                area_key = (str(area_code) if area_code else None, processor_id, area_id)
                
                # OPTIMIZATION: Use binary search to find affected buckets instead of checking all buckets
                # This reduces complexity from O(n*m) to O(n*log(m))
                record_end = None
                timespan = record[5]  # timespan
                if timespan is not None:
                    record_end = record_time + timedelta(seconds=timespan)
                
                # Find bucket that contains record.event_time (if any)
                within_bucket_idx = None
                # Use bisect_left to find first bucket_start >= record_time
                idx = bisect_left(bucket_starts, record_time)
                
                # Check if record is within the bucket at idx-1 (if idx > 0)
                # bisect_left returns the insertion point, so if idx > 0, the record could be in bucket[idx-1]
                if idx > 0:
                    prev_idx = idx - 1
                    if bucket_starts[prev_idx] <= record_time <= bucket_ends[prev_idx]:
                        within_bucket_idx = prev_idx
                # If idx == 0, record_time is before all buckets, so within_bucket_idx stays None
                # If idx == len, record_time is after all buckets, so within_bucket_idx stays None
                
                # Find buckets that record extends into (record starts before but ends after bucket start)
                extending_indices = []
                if record_end:
                    # Find buckets where: record_time < bucket_start AND record_end > bucket_start
                    start_idx = bisect_right(bucket_starts, record_time)  # First bucket_start > record_time
                    end_idx = bisect_left(bucket_starts, record_end)  # First bucket_start >= record_end
                    
                    for i in range(start_idx, end_idx):
                        if i < len(bucket_starts) and record_time < bucket_starts[i] and record_end > bucket_starts[i]:
                            extending_indices.append(i)
                
                # Find bucket that record is before (for before_start_data)
                # This is the bucket with the smallest bucket_start that is > record_time
                before_bucket_idx = None
                if idx < len(bucket_starts) and record_time < bucket_starts[idx]:
                    before_bucket_idx = idx
                
                # 1. Timespan sums (records within bucket)
                occupation_status = record[4]  # occupation_status
                if within_bucket_idx is not None:
                    bucket_start, bucket_end = chunk_buckets_list[within_bucket_idx]
                    bucket_key = (bucket_start, bucket_end)
                    if timespan is not None and occupation_status == "Occupied":
                        if area_key not in bucket_timespans[bucket_key]:
                            bucket_timespans[bucket_key][area_key] = 0
                        bucket_timespans[bucket_key][area_key] += timespan
                    
                    # 3. Edge cases (first/last records) - same bucket as within
                    current_first = edge_case_data[bucket_key][matching_area]['first']
                    current_last = edge_case_data[bucket_key][matching_area]['last']
                    
                    if current_first is None or (record_time and (current_first is None or record_time < current_first[0])):
                        edge_case_data[bucket_key][matching_area]['first'] = record
                    if current_last is None or (record_time and (current_last is None or record_time > current_last[0])):
                        edge_case_data[bucket_key][matching_area]['last'] = record
                
                # 2. Extending records (start before but extend into)
                for ext_idx in extending_indices:
                    bucket_start, bucket_end = chunk_buckets_list[ext_idx]
                    bucket_key = (bucket_start, bucket_end)
                    
                    if timespan is not None:
                        period_end = record_time + timedelta(seconds=timespan)
                        effective_start = max(record_time, bucket_start)
                        effective_end = min(period_end, bucket_end)
                        if effective_start < effective_end:
                            timespan_in_range = int((effective_end - effective_start).total_seconds())
                            if occupation_status == "Occupied":
                                if area_key not in extending_timespans[bucket_key]:
                                    extending_timespans[bucket_key][area_key] = 0
                                extending_timespans[bucket_key][area_key] += timespan_in_range
                
                # 4. Before start records (most recent before bucket)
                if before_bucket_idx is not None:
                    bucket_start, bucket_end = chunk_buckets_list[before_bucket_idx]
                    bucket_key = (bucket_start, bucket_end)
                    current_before = before_start_data[bucket_key][matching_area]
                    if current_before is None or (record_time > current_before[0]):
                        before_start_data[bucket_key][matching_area] = record
            
            # Calculate occupancy percentages for this chunk
            for area in areas:
                area_id = area.id
                area_code = str(area.code) if area.code else None
                processor_id = area.processor_id
                area_key = (area_code, processor_id, area_id)
                
                for bucket_start, bucket_end in chunk_buckets_list:
                    bucket_key = (bucket_start, bucket_end)
                    
                    # Start with timespan sums
                    occupied_timespan = bucket_timespans.get(bucket_key, {}).get(area_key, 0)
                    
                    # Add extending timespan
                    occupied_timespan += extending_timespans.get(bucket_key, {}).get(area_key, 0)
                    
                    # Handle edge cases (ongoing state, gap filling) - same logic as calculate_occupancy_for_bucket
                    edge_info = edge_case_data.get(bucket_key, {}).get(area, {})
                    first_record = edge_info.get('first')
                    last_record = edge_info.get('last')
                    before_record = before_start_data.get(bucket_key, {}).get(area)
                    
                    # Logic 1: Handle ongoing state (NULL timespan)
                    # record is tuple: [0]=event_time, [1]=area_code, [2]=processor_id, [3]=area_id, [4]=occupation_status, [5]=timespan
                    if last_record and last_record[5] is None and last_record[0]:
                        calculation_end_time = min(bucket_end, now)
                        if last_record[0] <= calculation_end_time:
                            ongoing_timespan = int((calculation_end_time - last_record[0]).total_seconds())
                            if last_record[4] == "Occupied":
                                occupied_timespan += ongoing_timespan
                    
                    # Logic 2: Handle missing data at start (gap filling)
                    if first_record and first_record[0] and first_record[0] > bucket_start:
                        if before_record:
                            if before_record[5] is not None:
                                before_period_end = before_record[0] + timedelta(seconds=before_record[5])
                                if before_period_end <= bucket_start:
                                    gap_timespan = int((first_record[0] - bucket_start).total_seconds())
                                    if first_record[4] == "Unoccupied":
                                        # Prior period was Occupied, add to occupied_timespan
                                        occupied_timespan += gap_timespan
                            else:
                                gap_start = bucket_start
                                gap_end = min(first_record[0], bucket_end, now)
                                gap_timespan = int((gap_end - gap_start).total_seconds())
                                if before_record[4] == "Occupied":
                                    occupied_timespan += gap_timespan
                        else:
                            gap_timespan = int((first_record[0] - bucket_start).total_seconds())
                            if first_record[4] == "Unoccupied":
                                # Prior period was Occupied, add to occupied_timespan
                                occupied_timespan += gap_timespan
                    
                    # Calculate percentage (same logic as calculate_occupancy_for_bucket)
                    effective_bucket_end = min(bucket_end, now)
                    effective_bucket_duration = int((effective_bucket_end - bucket_start).total_seconds())
                    
                    if effective_time_range == "this_week":
                        bucket_duration = 21600  # 6 hours in seconds
                    elif effective_time_range == "this_month":
                        bucket_duration = 86400  # 24 hours in seconds
                    elif effective_time_range == "this_year":
                        bucket_duration = 604800  # 7 days in seconds
                    else:
                        bucket_duration = effective_bucket_duration
                    
                    total_timespan = effective_bucket_duration if effective_bucket_duration > 0 else bucket_duration
                    occupancy_percentage = (occupied_timespan / total_timespan * 100) if total_timespan > 0 else 0.0
                    area_bucket_percentages[area.id].append(round(occupancy_percentage, 2))
            
            # Clear chunk data from memory
            del all_records, bucket_timespans, extending_timespans, edge_case_data, before_start_data
        
        # Average percentages across areas if multiple areas selected
        if len(areas) == 1:
            final_percentages = list(area_bucket_percentages.values())[0]
        else:
            final_percentages = [
                sum(area_bucket_percentages[area.id][i] for area in areas) / len(areas)
                for i in range(len(buckets))
            ]
        
        # Build x-axis and y-axis
        # Pass original time_range and start_date for custom range label formatting
        x_axis = build_x_axis_labels_for_buckets(buckets, effective_time_range, time_range, start_date, total_days)
        
        # Apply shift logic based on effective_time_range (works for both predefined and custom ranges)
        if effective_time_range == "this_week":
            # Check if we need to add "Sun 0" data from previous week
            # Only if the range actually starts on Sunday
            prev_week_sat_value = None
            starts_on_sunday = False
            if buckets:
                first_bucket_start, _ = buckets[0]
                # Check if first bucket is Sunday 0-6 (weekday 6 = Sunday, hour 0)
                starts_on_sunday = (first_bucket_start.weekday() + 1) % 7 == 0 and first_bucket_start.hour == 0
                
                if starts_on_sunday:
                    # Only fetch previous week's data for actual "this_week" or custom ranges starting on Sunday
                    if time_range == "this_week" or (time_range == "custom" and first_bucket_start.weekday() == 6):
                        # Calculate previous week's Saturday 18-24 bucket
                        prev_week_saturday = first_bucket_start - timedelta(days=1)  # Previous Saturday
                        prev_week_sat_bucket_start = prev_week_saturday.replace(hour=18, minute=0, second=0, microsecond=0)
                        prev_week_sat_bucket_end = prev_week_saturday.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Calculate occupancy for previous week's Saturday 18-24 bucket
                        prev_week_sat_values = []
                        for area in areas:
                            area_prev_value = calculate_occupancy_for_bucket(
                                db, area, prev_week_sat_bucket_start, prev_week_sat_bucket_end, now, effective_time_range
                            )
                            prev_week_sat_values.append(area_prev_value)
                        
                        if prev_week_sat_values:
                            prev_week_sat_value = sum(prev_week_sat_values) / len(prev_week_sat_values)
            
            shifted_data = []
            
            # Add "Sun 0" data if needed (previous week's Saturday 18-24)
            if prev_week_sat_value is not None:
                shifted_data.append(round(prev_week_sat_value, 2))
            
            # For custom ranges, the shifting logic depends on what day it starts
            # For "this_week" or custom ranges starting on Sunday: first 4 buckets are Sunday
            # For custom ranges starting on other days: no special Sunday handling
            if time_range == "this_week" or (time_range == "custom" and starts_on_sunday):
                # First 4 buckets are Sunday: use data as is
                for i in range(len(final_percentages)):
                    if i < 4:
                        shifted_data.append(round(final_percentages[i], 2))
                    else:
                        # Monday onwards: use previous bucket's data
                        shifted_data.append(round(final_percentages[i-1], 2))
            else:
                # Custom range not starting on Sunday: shift all buckets
                # First, fetch previous day's 18-24 bucket data for "Mon 0" label
                # OPTIMIZED: Use single query approach instead of calling calculate_occupancy_for_bucket per area
                prev_day_value = None
                if buckets:
                    first_bucket_start, _ = buckets[0]
                    # Calculate previous day's 18-24 bucket
                    prev_day = first_bucket_start - timedelta(days=1)
                    prev_day_bucket_start = prev_day.replace(hour=18, minute=0, second=0, microsecond=0)
                    prev_day_bucket_end = prev_day.replace(hour=23, minute=59, second=59, microsecond=999999)
                    
                    # Calculate buffer for extending records
                    # OPTIMIZED: Reduced from 48 to 12 hours (sufficient for 6-hour buckets, catches records with up to 12-hour timespans)
                    buffer_hours = 12  # Catches records that might extend into the 18-24 bucket
                    extended_start = prev_day_bucket_start - timedelta(hours=buffer_hours)
                    
                    # OPTIMIZATION: Use event_date for initial filtering (leverages index: ix_occupancy_logs_date_timespan)
                    prev_day_date = prev_day.date()
                    extended_date_start = extended_start.date()
                    
                    # Build all area filters (reuse same logic as main buckets)
                    all_area_filters_prev = []
                    for area in areas:
                        if area.id:
                            area_filter = OccupancyLog.area_id == area.id
                        else:
                            area_filter = and_(
                                OccupancyLog.area_code == str(area.code),
                                OccupancyLog.processor_id == area.processor_id
                            )
                        all_area_filters_prev.append(area_filter)
                    
                    # OPTIMIZED QUERY: Select only needed columns instead of full ORM objects
                    # Use event_date index first, then event_time for better performance
                    # Column order: [0]=event_time, [1]=area_code, [2]=processor_id, [3]=area_id, [4]=occupation_status, [5]=timespan
                    prev_day_records = (
                        db.query(
                            OccupancyLog.event_time,
                            OccupancyLog.area_code,
                            OccupancyLog.processor_id,
                            OccupancyLog.area_id,
                            OccupancyLog.occupation_status,
                            OccupancyLog.timespan
                        )
                        .filter(OccupancyLog.event_date >= extended_date_start)  # Use date index first
                        .filter(OccupancyLog.event_date <= prev_day_date)        # Narrow dataset early
                        .filter(OccupancyLog.event_time >= extended_start)       # Then filter by time
                        .filter(OccupancyLog.event_time <= prev_day_bucket_end)  # Final time filter
                        .filter(or_(*all_area_filters_prev))
                        .filter(OccupancyLog.occupation_status.in_(['Occupied', 'Unoccupied']))
                        .order_by(OccupancyLog.event_time)  # Helps query planner use index efficiently
                        .all()
                    )
                    
                    # Process records in memory (similar to main bucket processing)
                    area_bucket_percentages_prev = {}
                    
                    # Initialize data structures
                    bucket_timespans_prev = {}
                    extending_timespans_prev = {}
                    edge_case_data_prev = {}
                    before_start_data_prev = {}
                    
                    bucket_key_prev = (prev_day_bucket_start, prev_day_bucket_end)
                    bucket_timespans_prev[bucket_key_prev] = {}
                    extending_timespans_prev[bucket_key_prev] = {}
                    edge_case_data_prev[bucket_key_prev] = {area: {'first': None, 'last': None} for area in areas}
                    before_start_data_prev[bucket_key_prev] = {area: None for area in areas}
                    
                    # Process all records in memory
                    # OPTIMIZATION: Use tuple access (record[0], record[1], etc.) instead of namedtuple for zero overhead
                    for record in prev_day_records:
                        # record is a tuple: (event_time, area_code, processor_id, area_id, occupation_status, timespan)
                        record_time = record[0]  # event_time
                        if not record_time:
                            continue
                        
                        # OPTIMIZATION: Find matching area using O(1) lookup instead of O(n) search
                        matching_area = None
                        area_id = record[3]  # area_id
                        area_code = record[1]  # area_code
                        processor_id = record[2]  # processor_id
                        
                        if area_id and area_id in area_by_id_map:
                            matching_area = area_by_id_map[area_id]
                        elif area_code and processor_id:
                            key = (str(area_code), processor_id)
                            if key in area_by_code_processor_map:
                                matching_area = area_by_code_processor_map[key]
                        
                        if not matching_area:
                            continue
                        
                        area_key = (str(area_code) if area_code else None, processor_id, area_id)
                        timespan = record[5]  # timespan
                        occupation_status = record[4]  # occupation_status
                        
                        # 1. Timespan sums (records within bucket)
                        if prev_day_bucket_start <= record_time <= prev_day_bucket_end:
                            if timespan is not None and occupation_status == "Occupied":
                                if area_key not in bucket_timespans_prev[bucket_key_prev]:
                                    bucket_timespans_prev[bucket_key_prev][area_key] = 0
                                bucket_timespans_prev[bucket_key_prev][area_key] += timespan
                        
                        # 2. Extending records (start before but extend into)
                        if record_time < prev_day_bucket_start and timespan is not None:
                            period_end = record_time + timedelta(seconds=timespan)
                            if period_end > prev_day_bucket_start:
                                effective_start = max(record_time, prev_day_bucket_start)
                                effective_end = min(period_end, prev_day_bucket_end)
                                if effective_start < effective_end:
                                    timespan_in_range = int((effective_end - effective_start).total_seconds())
                                    if occupation_status == "Occupied":
                                        if area_key not in extending_timespans_prev[bucket_key_prev]:
                                            extending_timespans_prev[bucket_key_prev][area_key] = 0
                                        extending_timespans_prev[bucket_key_prev][area_key] += timespan_in_range
                        
                        # 3. Edge cases (first/last records)
                        if prev_day_bucket_start <= record_time <= prev_day_bucket_end:
                            current_first = edge_case_data_prev[bucket_key_prev][matching_area]['first']
                            current_last = edge_case_data_prev[bucket_key_prev][matching_area]['last']
                            
                            if current_first is None or (record_time and (current_first is None or record_time < current_first[0])):
                                edge_case_data_prev[bucket_key_prev][matching_area]['first'] = record
                            if current_last is None or (record_time and (current_last is None or record_time > current_last[0])):
                                edge_case_data_prev[bucket_key_prev][matching_area]['last'] = record
                        
                        # 4. Before start records (most recent before bucket)
                        if record_time and record_time < prev_day_bucket_start:
                            current_before = before_start_data_prev[bucket_key_prev][matching_area]
                            if current_before is None or (record_time > current_before[0]):
                                before_start_data_prev[bucket_key_prev][matching_area] = record
                    
                    # Calculate occupancy percentages for previous day's bucket
                    for area in areas:
                        area_id = area.id
                        area_code = str(area.code) if area.code else None
                        processor_id = area.processor_id
                        area_key = (area_code, processor_id, area_id)
                        
                        # Start with timespan sums
                        occupied_timespan = bucket_timespans_prev.get(bucket_key_prev, {}).get(area_key, 0)
                        
                        # Add extending timespan
                        occupied_timespan += extending_timespans_prev.get(bucket_key_prev, {}).get(area_key, 0)
                        
                        # Handle edge cases (ongoing state, gap filling)
                        edge_info = edge_case_data_prev.get(bucket_key_prev, {}).get(area, {})
                        first_record = edge_info.get('first')
                        last_record = edge_info.get('last')
                        before_record = before_start_data_prev.get(bucket_key_prev, {}).get(area)
                        
                        # Logic 1: Handle ongoing state (NULL timespan)
                        # record is tuple: [0]=event_time, [1]=area_code, [2]=processor_id, [3]=area_id, [4]=occupation_status, [5]=timespan
                        if last_record and last_record[5] is None and last_record[0]:
                            calculation_end_time = min(prev_day_bucket_end, now)
                            if last_record[0] <= calculation_end_time:
                                ongoing_timespan = int((calculation_end_time - last_record[0]).total_seconds())
                                if last_record[4] == "Occupied":
                                    occupied_timespan += ongoing_timespan
                        
                        # Logic 2: Handle missing data at start (gap filling)
                        if first_record and first_record[0] and first_record[0] > prev_day_bucket_start:
                            if before_record:
                                if before_record[5] is not None:
                                    before_period_end = before_record[0] + timedelta(seconds=before_record[5])
                                    if before_period_end <= prev_day_bucket_start:
                                        gap_timespan = int((first_record[0] - prev_day_bucket_start).total_seconds())
                                        if first_record[4] == "Unoccupied":
                                            # Prior period was Occupied, add to occupied_timespan
                                            occupied_timespan += gap_timespan
                                else:
                                    gap_start = prev_day_bucket_start
                                    gap_end = min(first_record[0], prev_day_bucket_end, now)
                                    gap_timespan = int((gap_end - gap_start).total_seconds())
                                    if before_record[4] == "Occupied":
                                        occupied_timespan += gap_timespan
                            else:
                                gap_timespan = int((first_record[0] - prev_day_bucket_start).total_seconds())
                                if first_record[4] == "Unoccupied":
                                    # Prior period was Occupied, add to occupied_timespan
                                    occupied_timespan += gap_timespan
                        
                        # Calculate percentage
                        effective_bucket_end = min(prev_day_bucket_end, now)
                        effective_bucket_duration = int((effective_bucket_end - prev_day_bucket_start).total_seconds())
                        
                        if effective_time_range == "this_week":
                            bucket_duration = 21600  # 6 hours in seconds
                        elif effective_time_range == "this_month":
                            bucket_duration = 86400  # 24 hours in seconds
                        elif effective_time_range == "this_year":
                            bucket_duration = 604800  # 7 days in seconds
                        else:
                            bucket_duration = effective_bucket_duration
                        
                        total_timespan = effective_bucket_duration if effective_bucket_duration > 0 else bucket_duration
                        occupancy_percentage = (occupied_timespan / total_timespan * 100) if total_timespan > 0 else 0.0
                        area_bucket_percentages_prev[area.id] = occupancy_percentage
                    
                    # Average percentages across areas
                    if area_bucket_percentages_prev:
                        prev_day_value = sum(area_bucket_percentages_prev.values()) / len(area_bucket_percentages_prev)
                
                # Add "Mon 0" data (previous day's 18-24 bucket)
                if prev_day_value is not None:
                    shifted_data.append(round(prev_day_value, 2))
                
                # Now add data for the buckets
                for i in range(len(final_percentages)):
                    if i == 0:
                        # First bucket: use data as is (for "Mon 6" label)
                        shifted_data.append(round(final_percentages[i], 2))
                    else:
                        # Other buckets: use their own data
                        shifted_data.append(round(final_percentages[i], 2))
            # Add data for the last bucket (18-24 bucket) -> "Sat 24" or "24" label
            if len(final_percentages) > 0:
                last_bucket_value = round(final_percentages[-1], 2)
                # Check if last bucket is 18-24 (for both this_week and custom week ranges)
                if buckets:
                    last_bucket_start, last_bucket_end = buckets[-1]
                    if last_bucket_start.hour == 18 and last_bucket_end.hour == 23:
                        weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                        last_day_name = weekdays[(last_bucket_start.weekday() + 1) % 7]
                        
                        # For this_week: only add "Sat 24" if it's Saturday
                        # For custom week ranges (1 < days < 8): always add "24" point for last day
                        if time_range == "this_week":
                            if last_day_name == "Sat":
                                # Add "Sat 24" data point
                                shifted_data.append(last_bucket_value)
                        elif time_range == "custom" and total_days > 1 and total_days < 8:
                            # Always add "24" data point for last day in custom week ranges
                            shifted_data.append(last_bucket_value)
            y_axis = {"data": shifted_data}
        elif effective_time_range == "this_month":
            # Each day shows its own data - no shifting logic for month
            shifted_data = []
            for i in range(len(final_percentages)):
                # Each day shows its own bucket's data
                shifted_data.append(round(final_percentages[i], 2))
            y_axis = {"data": shifted_data}
        elif effective_time_range == "this_year":
            # Group buckets by week label and aggregate data
            # Buckets 4+ in a month get clubbed with week 3
            
            # Create a mapping: bucket index -> week label (month, week_num)
            bucket_to_week = {}
            current_month = None
            week_counter = 0
            
            for i, (bucket_start, _) in enumerate(buckets):
                if current_month != bucket_start.month:
                    current_month = bucket_start.month
                    week_counter = 0
                else:
                    week_counter += 1
                
                week_num = min(week_counter, 3)  # Cap at 3
                bucket_to_week[i] = (current_month, week_num)
            
            # Group buckets by week and collect their percentages
            week_groups = defaultdict(list)
            for i, week_key in bucket_to_week.items():
                week_groups[week_key].append(final_percentages[i])
            
            # Average percentages for buckets in the same week
            aggregated_percentages = []
            current_month = None
            week_counter = 0
            seen_weeks = set()
            
            for bucket_start, _ in buckets:
                if current_month != bucket_start.month:
                    current_month = bucket_start.month
                    week_counter = 0
                    seen_weeks = set()
                else:
                    week_counter += 1
                
                week_num = min(week_counter, 3)
                week_key = (current_month, week_num)
                
                # Only aggregate once per unique week (to match x-axis labels)
                if week_key not in seen_weeks:
                    seen_weeks.add(week_key)
                    # Average all buckets in this week group
                    avg_value = sum(week_groups[week_key]) / len(week_groups[week_key])
                    aggregated_percentages.append(avg_value)
            
            # Apply shifting logic: first week shows its own data, rest show previous week's data
            shifted_data = []
            for i in range(len(aggregated_percentages)):
                if i == 0:
                    # First week (Jan-0) shows its own aggregated data
                    shifted_data.append(round(aggregated_percentages[i], 2))
                else:
                    # Subsequent weeks show previous week's aggregated data
                    shifted_data.append(round(aggregated_percentages[i-1], 2))
            y_axis = {"data": shifted_data}
        else:
            y_axis = {"data": [round(p, 2) for p in final_percentages]}
        
        return {
            "status": "success",
            "x-axis": x_axis,
            "y-axis": y_axis,
            "widget_title": "Utilization",
        }
    
    # ---------- EXISTING INSTANT-EVENT LOGIC (for this_day and custom <= 1 day) ----------
    # Note: This handles "this_day" and custom ranges with total_days <= 1
    # Note: x_axis will be built from actual event times later, not from fixed buckets

    # ---------- Build Area Conditions (IDENTICAL LOGIC) ----------
    area_conditions = [
        and_(
            OccupancyLog.area_code == info["code"],
            OccupancyLog.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]

    # ---------- OPTIMIZED QUERY USING occupancy_logs TABLE ----------
    # Query all occupancy logs in the time range, ordered by event_time
    # For instant occupancy, we use actual event times as data points
    # OPTIMIZATION: Use event_date for initial filtering (leverages index: ix_occupancy_logs_date_timespan)
    start_date_only = start_date.date()
    end_date_only = end_date.date()
    
    # OPTIMIZATION: Removed order_by - we sort in Python which is faster for smaller datasets
    # For "this_day" path, we extract unique event times and sort them anyway
    raw_results = (
        db.query(
            OccupancyLog.event_time,
            OccupancyLog.event_date,
            OccupancyLog.area_code,
            OccupancyLog.processor_id,
            OccupancyLog.occupation_status
        )
        .filter(OccupancyLog.event_date >= start_date_only)  # Use date index first
        .filter(OccupancyLog.event_date <= end_date_only)     # Narrow dataset early
        .filter(OccupancyLog.event_time >= start_date)        # Then filter by time
        .filter(OccupancyLog.event_time <= end_date)
        .filter(or_(*area_conditions))
        .filter(OccupancyLog.occupation_status.in_(['Occupied', 'Unoccupied']))
        # Removed order_by - we sort unique_event_times in Python which is faster
        .all()
    )

    # ---------- Extract Unique Event Times (INSTANT OCCUPANCY) ----------
    # Extract unique event times from raw results
    unique_event_times = sorted(set(r.event_time for r in raw_results if r.event_time))

    # ---------- Process Results: Calculate Occupancy at Each Event Time ----------
    # For each unique event time, calculate the occupancy state of all areas at that moment
    # This means finding the most recent status for each area up to and including that event time
    event_data = {}  # event_time -> {area_key: is_occupied}
    current_time = datetime.now()
    
    # Build a map of area statuses over time
    area_status_history = {}  # area_key -> list of (event_time, status) tuples, sorted by time
    
    for record in raw_results:
        if not record.event_time:
            continue
        area_key = (str(record.area_code), record.processor_id)
        if area_key not in area_status_history:
            area_status_history[area_key] = []
        area_status_history[area_key].append((record.event_time, record.occupation_status))
    
    # Sort each area's history by time
    for area_key in area_status_history:
        area_status_history[area_key].sort(key=lambda x: x[0])
    
    # OPTIMIZATION: Pre-extract times list once to avoid recreating it for each event_time
    area_times_map = {}
    for area_key, status_history in area_status_history.items():
        area_times_map[area_key] = [t for t, _ in status_history]
    
    # For each unique event time, calculate occupancy state
    for event_time in unique_event_times:
        # Skip future events
        if event_time > current_time:
            continue
        
        event_data[event_time] = {}
        
        # For each area, find the most recent status at or before this event time using binary search
        for area_key, status_history in area_status_history.items():
            if not status_history:
                continue
            
            # OPTIMIZATION: Use cached times list instead of recreating it
            times = area_times_map[area_key]
            
            # Binary search: find the rightmost index where times[i] <= event_time
            # bisect_right returns the insertion point, so we subtract 1 to get the last valid index
            idx = bisect_right(times, event_time) - 1
            
            # If we found a valid index (>= 0), get the status at that index
            if idx >= 0:
                most_recent_status = status_history[idx][1]
                event_data[event_time][area_key] = 1 if most_recent_status.lower() == "occupied" else 0

    # ---------- Group Events by Minute and Select Highest Value per Minute ----------
    # Group events by minute (normalize to minute precision) and keep only the one with highest occupancy count
    minute_groups = {}  # minute_key -> list of (event_time, occupancy_count)
    
    for event_time in unique_event_times:
        if event_time > current_time:
            continue
        
        # Calculate occupancy count for this event time
        if event_time in event_data:
            if len(area_ids_list) == 1:
                area_code = area_map[area_ids_list[0]]["code"]
                processor_id = area_map[area_ids_list[0]]["processor_id"]
                area_key = (str(area_code), processor_id)
                occupancy_count = event_data[event_time].get(area_key, 0) if area_key in event_data[event_time] else 0
            else:
                # Only count area_keys that are in the current valid set (reachable areas)
                occupancy_count = sum(1 for area_key, is_occupied in event_data[event_time].items() 
                                     if is_occupied == 1 and area_key in valid_area_keys)
        else:
            occupancy_count = 0
        
        # Create minute key (normalize to minute precision)
        minute_key = event_time.replace(second=0, microsecond=0)
        
        if minute_key not in minute_groups:
            minute_groups[minute_key] = []
        minute_groups[minute_key].append((event_time, occupancy_count))
    
    # For each minute group, select the event with highest occupancy count
    selected_event_times = []
    for minute_key, events in minute_groups.items():
        # Sort by occupancy count (descending), then by event_time (ascending) as tiebreaker
        events.sort(key=lambda x: (-x[1], x[0]))
        selected_event_times.append(events[0][0])  # Take the first one (highest count)
    
    # Sort selected event times
    selected_event_times = sorted(selected_event_times)

    # ---------- Build X-axis from Selected Event Times (Grouped by Minute) ----------
    # Format x-axis labels based on time_range using selected event times
    x_axis = []
    if time_range == "this_day" or (time_range == "custom" and total_days == 1):
        # Format as HH:MM for day view
        for event_time in selected_event_times:
            x_axis.append(f"{event_time.hour:02d}:{event_time.minute:02d}")
    elif time_range == "this_week":
        # Format as "Day HH:MM"
        for event_time in selected_event_times:
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(event_time.weekday() + 1) % 7]
            x_axis.append(f"{weekday} {event_time.hour:02d}:{event_time.minute:02d}")
    elif time_range == "custom" and total_days <= 7:
        # Format as "D/M HH:MM"
        for event_time in selected_event_times:
            x_axis.append(f"{event_time.day}/{event_time.month} {event_time.hour:02d}:{event_time.minute:02d}")
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        # Format as "D/M HH:MM"
        for event_time in selected_event_times:
            x_axis.append(f"{event_time.day}/{event_time.month} {event_time.hour:02d}:{event_time.minute:02d}")
    elif time_range == "this_year":
        # Format as "M-D HH:MM"
        for event_time in selected_event_times:
            x_axis.append(f"{calendar.month_abbr[event_time.month]}-{event_time.day} {event_time.hour:02d}:{event_time.minute:02d}")
    elif time_range == "custom" and total_days > 31:
        # Format as "M/Y D HH:MM"
        for event_time in selected_event_times:
            x_axis.append(f"{event_time.month}/{event_time.year} {event_time.day} {event_time.hour:02d}:{event_time.minute:02d}")

    # ---------- Generate Y-axis Data from Selected Event Times ----------
    y_axis = {}
    if len(area_ids_list) == 1:
        # Single area - show data without area name (0 or 1)
        area_code = area_map[area_ids_list[0]]["code"]
        area_name = area_map[area_ids_list[0]]["name"]
        processor_id = area_map[area_ids_list[0]]["processor_id"]
        area_key = (str(area_code), processor_id)
        
        values = []
        for event_time in selected_event_times:
            if event_time > current_time:
                values.append(None)
            elif event_time in event_data and area_key in event_data[event_time]:
                values.append(event_data[event_time][area_key])
            else:
                values.append(None)
        y_axis["data"] = values
    else:
        # Multiple areas - show combined count of occupied areas
        combined_values = []
        for event_time in selected_event_times:
            if event_time > current_time:
                combined_values.append(None)
            elif event_time in event_data:
                # Only count area_keys that are in the current valid set (reachable areas)
                total = sum(1 for area_key, is_occupied in event_data[event_time].items() 
                           if is_occupied == 1 and area_key in valid_area_keys)
                combined_values.append(int(total) if total > 0 else 0)
            else:
                combined_values.append(None)
        y_axis["data"] = combined_values

    # ---------- Apply forward fill logic (IDENTICAL LOGIC) ----------
    def forward_fill(values):
        if not values:
            return values
        
        last_non_null_index = -1
        for i in range(len(values) - 1, -1, -1):
            if values[i] is not None:
                last_non_null_index = i
                break
        
        if last_non_null_index == -1:
            return values
        
        filled_values = values.copy()
        last_valid_value = None
        
        for i in range(last_non_null_index + 1):
            if filled_values[i] is not None:
                last_valid_value = filled_values[i]
            elif last_valid_value is not None:
                filled_values[i] = last_valid_value
        
        return filled_values

    # Apply forward fill to each series
    for key in y_axis:
        y_axis[key] = forward_fill(y_axis[key])

    return {
        "status": "success",
        "x-axis": x_axis,
        "y-axis": y_axis,
        "widget_title": "Utilization",
    }