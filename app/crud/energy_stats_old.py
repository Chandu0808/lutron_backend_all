import math
from sqlalchemy.orm import Session
from sqlalchemy import func, case, distinct, cast, String, and_, or_
from fastapi import HTTPException
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Union
import os
import csv
import time
import calendar
from app.models.area_energy_stats import AreaEnergyStat
from app.models.area import Area
from app.models.area_occupancy_stats import AreaOccupancyStat
from app.models.area_group import AreaGroupMapping ,AreaGroup
from app.models.energy_saving import AreaEnergySavingByStrategy
from app.models.activity_logs import ActivityLog
from app.models.events import CurrentAreaEvent
from app.models.occupancy_logs import OccupancyLog
from sqlalchemy import desc
from app.utils.energy_unit_converter import convert_energy_dict, convert_single_energy_value
from app.crud.energy_stats import get_saving_by_strategy


DEFAULT_INTERVALS = 10  # Change to 12 if needed


from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy import func
import calendar

from app.models.area import Area
from app.models.area_energy_stats import AreaEnergyStat

def get_energy_consumption(
    db,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
    intervals: int = 10
) -> Dict[str, Any]:
    now = datetime.now()

    # ---------- Determine Date Range ----------
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

    # ---------- Fetch Areas ----------
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

    # ---------- Determine Time Range Type ----------
    total_days = (end_date.date() - start_date.date()).days + 1
    is_same_date = total_days == 1

    # ---------- Build X-axis Labels ----------
    if time_range == "this_day" or (time_range == "custom" and is_same_date):
        # Raw 15-minute data
        x_axis = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        bucket_type = "raw"
    elif time_range == "this_week":
        # 4 data points per day: Sun 0, Sun 6, Sun 12, Sun 18, Mon 0, etc.
        # Saturday has 5 points: Sat 0, Sat 6, Sat 12, Sat 18, Sun 0 (next week start)
        x_axis = []
        for i in range(7):
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][i]
            if i == 6:  # Last day (Saturday) - 5 labels, last one is next Sunday 0
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", "Sun 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "custom" and total_days <= 7:
        # 4 data points per day: 1/1 0, 1/1 6, 1/1 12, 1/1 18, 2/1 0, etc.
        # Last day has 5 points: last one is next day's 0
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            base = f"{day.day}/{day.month}"
            if i == total_days - 1:  # Last day - 5 labels, last one is next day 0
                next_day = day + timedelta(days=1)
                next_base = f"{next_day.day}/{next_day.month}"
                x_axis.extend([f"{base} 0", f"{base} 6", f"{base} 12", f"{base} 18", f"{next_base} 0"])
            else:
                x_axis.extend([f"{base} 0", f"{base} 6", f"{base} 12", f"{base} 18"])
        bucket_type = "6h"
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        # 1 data point per day: 1/1, 2/1, 3/1, etc.
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            x_axis.append(f"{day.day}/{day.month}")
        bucket_type = "day"
    elif time_range == "this_year":
        # 4 data points per month: Jan-1, Jan-2, Jan-3, Jan-4, Feb-1, etc.
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

    # ---------- Fetch Raw Data using composite key (area_code, processor_id) ----------
    # Build OR conditions for each (area_code, processor_id) pair
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    results = (
        db.query(
            AreaEnergyStat.created_at,
            AreaEnergyStat.instantaneous_power,
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id
        )
        .filter(AreaEnergyStat.created_at >= start_date)
        .filter(AreaEnergyStat.created_at <= end_date)
        .filter(or_(*area_conditions))  # Use composite key filtering
        .all()
    )

    # ---------- Helper Functions ----------
    def get_bucket_key(ts: datetime, bucket_type: str) -> str:
        if bucket_type == "raw":
            minute_bucket = (ts.minute // 15) * 15
            return f"{ts.hour:02d}:{minute_bucket:02d}"
        elif bucket_type == "6h":
            # New logic for both "this_week" and custom ≤ 7 days
            # Each label represents the PREVIOUS 6-hour period
            # 0-6 hours -> current day label "6"
            # 6-12 hours -> current day label "12"
            # 12-18 hours -> current day label "18"
            # 18-24 hours -> next day label "0"
            if time_range == "this_week":
                if ts.hour < 6:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 6"
                elif ts.hour < 12:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 12"
                elif ts.hour < 18:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 18"
                else:  # 18-23:59
                    # Map to next day's "0" label (Saturday maps to Sun 0)
                    next_day_weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 2) % 7]
                    return f"{next_day_weekday} 0"
            else:  # custom ≤ 7 days
                if ts.hour < 6:
                    return f"{ts.day}/{ts.month} 6"
                elif ts.hour < 12:
                    return f"{ts.day}/{ts.month} 12"
                elif ts.hour < 18:
                    return f"{ts.day}/{ts.month} 18"
                else:  # 18-23:59
                    # Map to next day's "0" label
                    next_day = ts + timedelta(days=1)
                    return f"{next_day.day}/{next_day.month} 0"
        elif bucket_type == "day":
            return f"{ts.day}/{ts.month}"
        elif bucket_type == "month4":
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

    # ---------- Aggregate Data by Area ----------
    area_data = {}
    for area_id in area_ids:
        area_code = str(area_map[area_id]["code"])
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key (area_code + processor_id)
        area_records = [r for r in results if str(r.area_code) == area_code and r.processor_id == processor_id]
        
        if bucket_type == "raw":
            # For raw data, we need to sum all records for each 15-minute bucket
            bucket_values = {}
            bucket_has_data = {}
            for record in area_records:
                bucket_key = get_bucket_key(record.created_at, bucket_type)
                if bucket_key not in bucket_values:
                    bucket_values[bucket_key] = 0
                    bucket_has_data[bucket_key] = False
                if record.instantaneous_power is not None:
                    bucket_values[bucket_key] += record.instantaneous_power
                    bucket_has_data[bucket_key] = True
            
            # Divide by 4 for final values
            final_values = {}
            for bucket_key, total_power in bucket_values.items():
                if bucket_has_data.get(bucket_key):
                    averaged_value = round(total_power / 4, 2)
                    final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                else:
                    final_values[bucket_key] = None
        else:
            # Aggregate data into buckets
            bucket_values = {}
            bucket_has_data = {}
            for record in area_records:
                bucket_key = get_bucket_key(record.created_at, bucket_type)
                if bucket_key not in bucket_values:
                    bucket_values[bucket_key] = 0
                    bucket_has_data[bucket_key] = False
                if record.instantaneous_power is not None:
                    bucket_values[bucket_key] += record.instantaneous_power
                    bucket_has_data[bucket_key] = True
            
            # Divide by 4 for final values
            final_values = {}
            for bucket_key, total_power in bucket_values.items():
                if bucket_has_data.get(bucket_key):
                    averaged_value = round(total_power / 4, 2)
                    final_values[bucket_key] = averaged_value if averaged_value != 0 else 0
                else:
                    final_values[bucket_key] = None
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data ----------
    y_axis = {}
    if len(area_ids) < 5:
        # Individual areas
        for area_name, data in area_data.items():
            values = []
            for label in x_axis:
                # First "0" label is always fixed at 0 for week-like views
                if (time_range == "this_week" and label == "Sun 0") or (bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]):
                    values.append(None)
                else:
                    values.append(data.get(label))
            y_axis[area_name] = values
    else:
        # Combined areas
        combined_values = []
        for label in x_axis:
            # First "0" label is always fixed at 0 for week-like views
            if (time_range == "this_week" and label == "Sun 0") or (bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]):
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

    # Apply energy unit conversion
    conversion_result = convert_energy_dict(y_axis)
    converted_y_axis = conversion_result["data"]
    unit = conversion_result["unit"]
    
    return {
        "status": "success", 
        "x-axis": x_axis, 
        "y-axis": converted_y_axis, 
        "unit": unit,
        "widget_title": "Consumption"
    }



def get_energy_savings(
    db,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
    intervals: int = 10
) -> Dict[str, Any]:
    now = datetime.now()

    # ---------- Determine Date Range ----------
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

    # ---------- Fetch Areas ----------
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

    # ---------- Determine Time Range Type ----------
    total_days = (end_date.date() - start_date.date()).days + 1
    is_same_date = total_days == 1

    # ---------- Build X-axis Labels ----------
    if time_range == "this_day" or (time_range == "custom" and is_same_date):
        # Raw 15-minute data
        x_axis = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
        bucket_type = "raw"
    elif time_range == "this_week":
        # 4 data points per day: Sun 0, Sun 6, Sun 12, Sun 18, Mon 0, etc.
        # Saturday has 5 points: Sat 0, Sat 6, Sat 12, Sat 18, Sun 0 (next week start)
        x_axis = []
        for i in range(7):
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][i]
            if i == 6:  # Last day (Saturday) - 5 labels, last one is next Sunday 0
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18", "Sun 0"])
            else:
                x_axis.extend([f"{weekday} 0", f"{weekday} 6", f"{weekday} 12", f"{weekday} 18"])
        bucket_type = "6h"
    elif time_range == "custom" and total_days <= 7:
        # 4 data points per day: 1/1 0, 1/1 6, 1/1 12, 1/1 18, 2/1 0, etc.
        # Last day has 5 points: last one is next day's 0
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            base = f"{day.day}/{day.month}"
            if i == total_days - 1:  # Last day - 5 labels, last one is next day 0
                next_day = day + timedelta(days=1)
                next_base = f"{next_day.day}/{next_day.month}"
                x_axis.extend([f"{base} 0", f"{base} 6", f"{base} 12", f"{base} 18", f"{next_base} 0"])
            else:
                x_axis.extend([f"{base} 0", f"{base} 6", f"{base} 12", f"{base} 18"])
        bucket_type = "6h"
    elif time_range == "this_month" or (time_range == "custom" and total_days <= 31):
        # 1 data point per day: 1/1, 2/1, 3/1, etc.
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            x_axis.append(f"{day.day}/{day.month}")
        bucket_type = "day"
    elif time_range == "this_year":
        # 4 data points per month: Jan-1, Jan-2, Jan-3, Jan-4, Feb-1, etc.
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

    # ---------- Fetch Raw Data using composite key (area_code, processor_id) ----------
    # Build OR conditions for each (area_code, processor_id)
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    results = (
        db.query(
            AreaEnergyStat.created_at,
            AreaEnergyStat.instantaneous_power,
            AreaEnergyStat.instantaneous_max_power,
            AreaEnergyStat.instantaneous_saved_power,
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id
        )
        .filter(AreaEnergyStat.created_at >= start_date)
        .filter(AreaEnergyStat.created_at <= end_date)
        .filter(or_(*area_conditions))  # Use composite key filtering
        .all()
    )

    # ---------- Helper Functions ----------
    def get_bucket_key(ts: datetime, bucket_type: str) -> str:
        if bucket_type == "raw":
            minute_bucket = (ts.minute // 15) * 15
            return f"{ts.hour:02d}:{minute_bucket:02d}"
        elif bucket_type == "6h":
            # New logic for both "this_week" and custom ≤ 7 days
            # Each label represents the PREVIOUS 6-hour period
            # 0-6 hours -> current day label "6"
            # 6-12 hours -> current day label "12"
            # 12-18 hours -> current day label "18"
            # 18-24 hours -> next day label "0"
            if time_range == "this_week":
                if ts.hour < 6:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 6"
                elif ts.hour < 12:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 12"
                elif ts.hour < 18:
                    weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
                    return f"{weekday} 18"
                else:  # 18-23:59
                    # Map to next day's "0" label (Saturday maps to Sun 0)
                    next_day_weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 2) % 7]
                    return f"{next_day_weekday} 0"
            else:  # custom ≤ 7 days
                if ts.hour < 6:
                    return f"{ts.day}/{ts.month} 6"
                elif ts.hour < 12:
                    return f"{ts.day}/{ts.month} 12"
                elif ts.hour < 18:
                    return f"{ts.day}/{ts.month} 18"
                else:  # 18-23:59
                    # Map to next day's "0" label
                    next_day = ts + timedelta(days=1)
                    return f"{next_day.day}/{next_day.month} 0"
        elif bucket_type == "day":
            return f"{ts.day}/{ts.month}"
        elif bucket_type == "month4":
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

    # ---------- Aggregate Data by Area ----------
    area_data = {}
    for area_id in area_ids:
        area_code = str(area_map[area_id]["code"])
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key (area_code + processor_id)
        area_records = [r for r in results if str(r.area_code) == area_code and r.processor_id == processor_id]
        
        if bucket_type == "raw":
            # For raw data, we need to sum all savings for each 15-minute bucket
            bucket_values = {}
            for record in area_records:
                bucket_key = get_bucket_key(record.created_at, bucket_type)
                if bucket_key not in bucket_values:
                    bucket_values[bucket_key] = 0
                if record.instantaneous_saved_power is not None:
                    savings = record.instantaneous_saved_power
                    bucket_values[bucket_key] += savings
            
            # Divide by 4 for final values
            final_values = {}
            for bucket_key, total_savings in bucket_values.items():
                if total_savings > 0:
                    final_values[bucket_key] = round(total_savings / 4, 2)
                else:
                    final_values[bucket_key] = None
        else:
            # Aggregate data into buckets
            bucket_values = {}
            for record in area_records:
                bucket_key = get_bucket_key(record.created_at, bucket_type)
                if bucket_key not in bucket_values:
                    bucket_values[bucket_key] = 0
                if record.instantaneous_saved_power is not None:
                    savings = record.instantaneous_saved_power
                    bucket_values[bucket_key] += savings
            
            # Divide by 4 for final values
            final_values = {}
            for bucket_key, total_savings in bucket_values.items():
                if total_savings > 0:
                    final_values[bucket_key] = round(total_savings / 4, 2)
                else:
                    final_values[bucket_key] = None
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data ----------
    y_axis = {}
    if len(area_ids) < 5:
        # Individual areas
        for area_name, data in area_data.items():
            values = []
            for label in x_axis:
                # First "0" label is always fixed at 0 for week-like views
                if (time_range == "this_week" and label == "Sun 0") or (bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]):
                    values.append(None)
                else:
                    values.append(data.get(label))
            y_axis[area_name] = values
    else:
        # Combined areas
        combined_values = []
        for label in x_axis:
            # First "0" label is always fixed at 0 for week-like views
            if (time_range == "this_week" and label == "Sun 0") or (bucket_type == "6h" and label.endswith(" 0") and label == x_axis[0]):
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

    # Apply energy unit conversion
    conversion_result = convert_energy_dict(y_axis)
    converted_y_axis = conversion_result["data"]
    unit = conversion_result["unit"]
    
    return {
        "status": "success", 
        "x-axis": x_axis, 
        "y-axis": converted_y_axis, 
        "unit": unit,
        "widget_title": "Savings"
    }


def get_peak_min_consumption(
    db: Session,
    area_ids: Optional[List[int]] = None,
    floor_ids: Optional[List[int]] = None,
    time_range: str = "this_day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    # ---------- Use consumption graph instead of raw table ----------
    graph_data = get_energy_consumption(
        db=db,
        area_ids=area_ids,
        floor_ids=floor_ids,
        time_range=time_range,
        start_date=start_date,
        end_date=end_date
    )

    x_axis = graph_data.get("x-axis", [])
    y_axis = graph_data.get("y-axis", {})

    if not x_axis or not y_axis:
        return {
            "status": "success",
            "peak": {"value": 0.0, "time": None},
            "min": {"value": 0.0, "time": None}
        }

    # If multiple areas → Combined Areas series
    if "Combined Areas" in y_axis:
        series = y_axis["Combined Areas"]
    else:
        # If multiple series exist, just take the first one
        first_key = next(iter(y_axis))
        series = y_axis[first_key]

    # ---------- Find Peak and Min ----------
    peak_value, peak_time = None, None
    min_value, min_time = None, None

    for idx, val in enumerate(series):
        if val is None or val == 0:
            continue
        if peak_value is None or val > peak_value:
            peak_value = val
            peak_time = x_axis[idx]
        if min_value is None or val < min_value:
            min_value = val
            min_time = x_axis[idx]

    return {
        "status": "success",
        "peak": {
            "value": peak_value if peak_value is not None else 0.0,
            "time": peak_time
        },
        "min": {
            "value": min_value if min_value is not None else 0.0,
            "time": min_time
        }
    }


def scale_si_unit(value: Optional[float], base_unit: str) -> Optional[str]:
    """
    Scale the value into W, mW, or µW with unit suffix.
    Returns string like '1.1 mW/m²' or None if value is None.
    """
    if value is None:
        return None

    abs_val = abs(value)

    if abs_val >= 1:
        return f"{round(value, 5)} W{base_unit}"
    elif abs_val >= 0.001:
        return f"{round(value * 1000, 5)} mW{base_unit}"
    else:
        return f"{round(value * 1_000_000, 5)} µW{base_unit}"





def get_light_power_density(
    db: Session,
    floor_ids: Optional[List[int]] = None,
    area_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Compute Light Power Density (LPD) summary using the latest energy readings.
    - If floor_ids is None → include all floors
    - If area_ids is None → include all areas under the given floor(s)
    The computation mirrors the area selection logic used by the energy consumption API:
    we resolve the selected areas, pull the latest `energy_consumed_in_Wh` entry for each
    `(area_code, processor_id)` pair, then compute density using the total selected area.
    """

    areas_query = db.query(Area)
    if floor_ids:
        areas_query = areas_query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        areas_query = areas_query.filter(Area.id.in_(area_ids))

    areas = areas_query.all()
    if not areas:
        raise HTTPException(status_code=404, detail="No areas found for given criteria")

    area_infos: List[Dict[str, Any]] = []
    area_conditions = []
    total_sqft = 0.0
    total_sqm = 0.0

    for area in areas:
        sqft = float(area.area_sqft or 0)
        sqm = float(area.area_sqm or 0)
        total_sqft += sqft
        total_sqm += sqm

        code_int: Optional[int] = None
        if area.code is not None:
            try:
                code_int = int(area.code)
            except (TypeError, ValueError):
                code_int = None

        processor_id = area.processor_id

        area_infos.append(
            {
                "code_int": code_int,
                "processor_id": processor_id,
                "area_sqft": sqft,
                "area_sqm": sqm,
            }
        )

        if code_int is not None:
            area_conditions.append(
                and_(
                    AreaEnergyStat.area_code == code_int,
                    AreaEnergyStat.processor_id == processor_id,
                )
            )

    energy_by_area: Dict[Tuple[Optional[int], Optional[int]], float] = {
        (info["code_int"], info["processor_id"]): 0.0
        for info in area_infos
        if info["code_int"] is not None
    }

    if area_conditions:
        latest_stat_subquery = (
            db.query(
                AreaEnergyStat.area_code.label("area_code"),
                AreaEnergyStat.processor_id.label("processor_id"),
                func.max(AreaEnergyStat.created_at).label("max_created_at"),
            )
            .filter(or_(*area_conditions))
            .group_by(AreaEnergyStat.area_code, AreaEnergyStat.processor_id)
            .subquery()
        )

        latest_stats = (
            db.query(
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                AreaEnergyStat.energy_consumed_in_Wh,
            )
            .join(
                latest_stat_subquery,
                and_(
                    AreaEnergyStat.area_code == latest_stat_subquery.c.area_code,
                    AreaEnergyStat.processor_id == latest_stat_subquery.c.processor_id,
                    AreaEnergyStat.created_at == latest_stat_subquery.c.max_created_at,
                ),
            )
            .all()
        )

        for stat in latest_stats:
            key = (stat.area_code, stat.processor_id)
            energy_by_area[key] = float(stat.energy_consumed_in_Wh or 0.0)

    total_energy_wh = 0.0
    for info in area_infos:
        key = (info["code_int"], info["processor_id"])
        total_energy_wh += energy_by_area.get(key, 0.0)

    # Avoid division by zero; if no area surface available, density is zero.
    wh_per_sqft = round((total_energy_wh * 4) / total_sqft, 4) if total_sqft else 0.0
    wh_per_sqm = round((total_energy_wh * 4)/ total_sqm, 4) if total_sqm else 0.0

    return {
        "status": "success",
        "total_energy_consumed_in_Wh": round(total_energy_wh, 2),
        "total_instantaneous_power": round(total_energy_wh, 2),  # Backward compatibility
        "total_area_sqft": round(total_sqft, 2),
        "total_area_sqm": round(total_sqm, 2),
        "wh_per_sqft": wh_per_sqft,
        "wh_per_sqm": wh_per_sqm,
        "watt_per_sqft": wh_per_sqft,  # Backward compatibility
        "watt_per_sqm": wh_per_sqm,    # Backward compatibility
    }





def get_total_consumption_by_area_id(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    now = datetime.now()

    # ---------- Determine Date Range ----------
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

    # ---------- Resolve areas ----------
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

    # ---------- Get special groups ----------
    special_groups = (
        db.query(AreaGroup)
        .join(AreaGroupMapping)
        .filter(AreaGroupMapping.area_id.in_(area_ids))
        .filter(AreaGroup.special == True)
        .distinct()
        .all()
    )

    # ---------- Fetch Raw Data using composite key (area_code, processor_id) ----------
    # Build OR conditions for each (area_code, processor_id) pair
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    results = (
        db.query(
            AreaEnergyStat.created_at,
            AreaEnergyStat.instantaneous_power,
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id
        )
        .filter(AreaEnergyStat.created_at >= start_date)
        .filter(AreaEnergyStat.created_at <= end_date)
        .filter(or_(*area_conditions))  # Use composite key filtering
        .all()
    )

    # ---------- Calculate Total Consumption for Verification ----------
    total_consumption = 0
    for record in results:
        if record.instantaneous_power is not None:
            total_consumption += record.instantaneous_power
    total_consumption = total_consumption / 4  # Apply division by 4

    # ---------- Process Special Groups ----------
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
                "consumption_value": 0.0
            })
            continue

        # Get area_code AND processor_id (composite key) for proper filtering
        area_keys = [
            (str(code), proc_id) for (code, proc_id) in db.query(Area.code, Area.processor_id)
            .filter(Area.id.in_(mapped_area_ids))
            .all()
        ]
        
        if not area_keys:
            group_results.append({
                "name": group.name,
                "consumption_percentage": "0 %",
                "consumption_value": 0.0
            })
            continue

        # Filter data for this group using BOTH area_code AND processor_id
        group_records = [
            r for r in results 
            if (str(r.area_code), r.processor_id) in area_keys
        ]
        
        # Calculate group consumption (same logic as get_energy_consumption)
        group_consumption = 0
        for record in group_records:
            if record.instantaneous_power is not None:
                group_consumption += record.instantaneous_power
        group_consumption = group_consumption / 4  # Apply division by 4

        # Store raw consumption value (will convert later with global unit)
        group_results.append({
            "name": group.name,
            "consumption_percentage": "0 %",  # Will be calculated below
            "consumption_value": group_consumption
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

    # ---------- Convert All Groups Using Same Unit ----------
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


from collections import defaultdict

def get_occupancy_count_over_time(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict[str, Any]:
    now = datetime.now()

    # ---------- Determine Date Range ----------
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

    # ---------- Fetch Areas ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()

    # Create area_map with area details
    area_ids_list = [a.id for a in areas]
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}

    # ---------- Build X-axis ----------
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
        x_axis = []
        for i in range(total_days):
            day = start_date + timedelta(days=i)
            base = f"{day.day}/{day.month}"
            x_axis.extend([f"{base} 0", f"{base} 6", f"{base} 12", f"{base} 18"])
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

    # ---------- Fetch Raw Data using composite key (area_code, processor_id) ----------
    area_conditions = [
        and_(
            AreaOccupancyStat.area_code == info["code"],
            AreaOccupancyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    results = (
        db.query(AreaOccupancyStat.area_code, AreaOccupancyStat.processor_id, AreaOccupancyStat.occupancy_status, AreaOccupancyStat.created_at)
        .filter(AreaOccupancyStat.created_at >= start_date)
        .filter(AreaOccupancyStat.created_at <= end_date)
        .filter(or_(*area_conditions))
        .all()
    )

    # ---------- Helper function to get bucket key ----------
    def get_bucket_key(ts: datetime, bucket_type: str) -> str:
        if bucket_type == "15min":
            return f"{ts.hour:02d}:{(ts.minute // 15) * 15:02d}"
        elif bucket_type == "week6h":
            weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][(ts.weekday() + 1) % 7]
            seg = ts.hour // 6
            return f"{weekday} {seg * 6}"
        elif bucket_type == "6h":
            seg = ts.hour // 6
            return f"{ts.day}/{ts.month} {seg * 6}"
        elif bucket_type == "day":
            return f"{ts.day}/{ts.month}"
        elif bucket_type == "month4":
            d = ts.day
            w = "1" if d <= 7 else "2" if d <= 15 else "3"
            return f"{calendar.month_abbr[ts.month]}-{w}"
        elif bucket_type == "custom_month4":
            d = ts.day
            w = "1" if d <= 7 else "2" if d <= 15 else "3"
            return f"{ts.month}/{ts.year}-{w}"
        return ""

    # ---------- Aggregate Data by Area ----------
    area_data = {}
    for area_id in area_ids_list:
        area_code = area_map[area_id]["code"]
        area_name = area_map[area_id]["name"]
        processor_id = area_map[area_id]["processor_id"]
        
        # Filter data for this area using composite key (area_code + processor_id)
        area_records = [r for r in results if str(r.area_code) == area_code and r.processor_id == processor_id]
        
        # Normalize timestamps to 15-minute slots and convert occupancy to binary (1 or 0)
        slot_data: Dict[datetime, List[int]] = defaultdict(list)
        for record in area_records:
            ts = record.created_at.replace(second=0, microsecond=0, minute=(record.created_at.minute // 15) * 15)
            occupancy_value = 1 if record.occupancy_status == "Occupied" else 0
            slot_data[ts].append(occupancy_value)
        
        # Average within 15-minute slots (in case of multiple records)
        slot_averages: Dict[datetime, float] = {}
        for ts, values in slot_data.items():
            slot_averages[ts] = sum(values) / len(values) if values else 0
        
        # Aggregate into display buckets
        bucket_values: Dict[str, List[float]] = defaultdict(list)
        for ts, avg_value in slot_averages.items():
            bucket_key = get_bucket_key(ts, bucket_type)
            if bucket_key:
                bucket_values[bucket_key].append(avg_value)
        
        # Average within buckets
        final_values = {}
        for bucket_key, values in bucket_values.items():
            if values:
                final_values[bucket_key] = int(round(sum(values) / len(values)))
            else:
                final_values[bucket_key] = None
        
        area_data[area_name] = final_values

    # ---------- Generate Y-axis Data ----------
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

    # ---------- Apply forward fill logic ----------
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




def spaceutilization_by_area_group(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    now = datetime.now()

    # Resolve time range
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
    
     # ---------- Resolve areas ----------
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
    
    # Step 1: Find all unique special groups for input area_ids
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
        # Step 2: All area_ids in this group
        group_area_ids = db.query(AreaGroupMapping.area_id) \
            .filter(AreaGroupMapping.group_id == group.id).all()
        group_area_ids = [row[0] for row in group_area_ids]

        if not group_area_ids:
            continue

        # Step 3: Fetch area_code and processor_id from Area table for composite key filtering
        area_info = db.query(Area.code, Area.processor_id).filter(Area.id.in_(group_area_ids)).all()
        
        if not area_info:
            continue
        
        # Build composite key tuples (area_code, processor_id)
        area_keys = [(str(code), proc_id) for code, proc_id in area_info]

        # Step 4: Count occupied using composite key (area_code, processor_id)
        occupied_conditions = [
            and_(
                AreaOccupancyStat.area_code == str(code),
                AreaOccupancyStat.processor_id == proc_id
            )
            for code, proc_id in area_keys
        ]
        total_occupied = db.query(func.count()) \
            .select_from(AreaOccupancyStat) \
            .filter(or_(*occupied_conditions)) \
            .filter(AreaOccupancyStat.occupancy_status.ilike("occupied")) \
            .filter(AreaOccupancyStat.created_at >= start_date) \
            .filter(AreaOccupancyStat.created_at < end_date) \
            .scalar() or 0

        # Step 5: Count unoccupied using composite key (area_code, processor_id)
        total_unoccupied = db.query(func.count()) \
            .select_from(AreaOccupancyStat) \
            .filter(or_(*occupied_conditions)) \
            .filter(AreaOccupancyStat.occupancy_status.ilike("unoccupied")) \
            .filter(AreaOccupancyStat.created_at >= start_date) \
            .filter(AreaOccupancyStat.created_at < end_date) \
            .scalar() or 0

        total_possible = total_occupied + total_unoccupied

        results.append({
            "area_group_id": group.id,
            "area_group_name": group.name,
            "total_occupied": total_occupied,
            "total_possible": total_possible
        })

    return results


def spaceutilization_by_area_group_from_logs(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Space utilization by area group using occupancy_logs table instead of area_occupancy_stats.
    This function has the same functionality as spaceutilization_by_area_group but uses
    the occupancy_logs table which tracks occupancy status changes over time.
    """
    now = datetime.now()

    # Resolve time range
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
    
    # Resolve areas
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

    area_ids_list = [a.id for a in areas]
    
    # Step 1: Find all unique special groups for input area_ids
    special_groups = (
        db.query(AreaGroup)
        .join(AreaGroupMapping)
        .filter(AreaGroupMapping.area_id.in_(area_ids_list))
        .filter(AreaGroup.special.is_(True))
        .distinct()
        .all()
    )

    results = []

    for group in special_groups:
        # Step 2: All area_ids in this group
        group_area_ids = db.query(AreaGroupMapping.area_id) \
            .filter(AreaGroupMapping.group_id == group.id).all()
        group_area_ids = [row[0] for row in group_area_ids]

        if not group_area_ids:
            continue

        # Step 3: Filter occupancy logs by area_id (more reliable than area_code + processor_id)
        # Count occupied logs
        total_occupied = db.query(func.count(OccupancyLog.id)) \
            .filter(OccupancyLog.area_id.in_(group_area_ids)) \
            .filter(OccupancyLog.occupation_status == "Occupied") \
            .filter(OccupancyLog.event_time >= start_date) \
            .filter(OccupancyLog.event_time <= end_date) \
            .scalar() or 0

        # Count unoccupied logs
        total_unoccupied = db.query(func.count(OccupancyLog.id)) \
            .filter(OccupancyLog.area_id.in_(group_area_ids)) \
            .filter(OccupancyLog.occupation_status == "Unoccupied") \
            .filter(OccupancyLog.event_time >= start_date) \
            .filter(OccupancyLog.event_time <= end_date) \
            .scalar() or 0

        total_possible = total_occupied + total_unoccupied

        results.append({
            "area_group_id": group.id,
            "area_group_name": group.name,
            "total_occupied": total_occupied,
            "total_possible": total_possible
        })

    return results
  
def get_space_utilization_by_area(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = datetime.now()

    # Inclusive time range
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
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

    # ---------- Resolve areas ----------
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))

    areas = query.all()
    if not areas:  # fallback to all
        areas = db.query(Area).all()
    if not areas:
        return {"status": "success", "utilized_area": []}

    utilized_area = []

    for area in areas:
        # Fetch area_code and processor_id for composite key filtering
        area_code = area.code
        processor_id = area.processor_id
        
        # Single query for total + occupied counts using composite key
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
            .filter(AreaOccupancyStat.created_at >= start_date)
            .filter(AreaOccupancyStat.created_at <= end_date)
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
            "name": area.name,
            "occupied": occupied_percent
        })

    return {
        "status": "success",
        "utilized_area": utilized_area
    }


def get_peak_min_occupancy(
    db: Session,
    area_ids: Optional[List[int]] = None,
    floor_ids: Optional[List[int]] = None,
    time_range: str = "this_day",
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    # Reuse occupancy graph function
    graph_data = get_occupancy_count_over_time(
        db=db,
        area_ids=area_ids,
        floor_ids=floor_ids,
        time_range=time_range,
        start_date=start_date,
        end_date=end_date
    )

    x_axis = graph_data["x-axis"]
    y_axis = graph_data["y-axis"]

    # filter out nulls (no data buckets)
    valid_points = [(x, y) for x, y in zip(x_axis, y_axis) if y is not None]

    if not valid_points:
        return {
            "status": "success",
            "peak": {"value": None, "time": None},
            "min": {"value": None, "time": None}
        }

    # Peak = max value
    peak_time, peak_value = max(valid_points, key=lambda p: p[1])
    # Min = min value
    min_time, min_value = min(valid_points, key=lambda p: p[1])

    return {
        "status": "success",
        "peak": {"value": peak_value, "time": peak_time},
        "min": {"value": min_value, "time": min_time}
    }

def get_unified_energy_data_of_a_day(db: Session, area_ids, floor_ids, data_date):
    
    x_axis = ["00:00", "00:15", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45","02:00", "02:15", "02:30", "02:45", 
              "03:00", "03:15", "03:30", "03:45","04:00", "04:15", "04:30", "04:45", "05:00", "05:15", "05:30", "05:45",
              "06:00", "06:15", "06:30", "06:45", "07:00", "07:15", "07:30", "07:45","08:00", "08:15", "08:30", "08:45", 
              "09:00", "09:15", "09:30", "09:45","10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45",
              "12:00", "12:15", "12:30", "12:45", "13:00", "13:15", "13:30", "13:45","14:00", "14:15", "14:30", "14:45", 
              "15:00", "15:15", "15:30", "15:45","16:00", "16:15", "16:30", "16:45", "17:00", "17:15", "17:30", "17:45",
              "18:00", "18:15", "18:30", "18:45", "19:00", "19:15", "19:30", "19:45","20:00", "20:15", "20:30", "20:45", 
              "21:00", "21:15", "21:30", "21:45","22:00", "22:15", "22:30", "22:45", "23:00", "23:15", "23:30", "23:45"]
    # Query for the data of the day
    if area_ids:
        rows = (
            db.query(
                AreaEnergyStat.timespan_15min,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .filter(AreaEnergyStat.area_id.in_(area_ids))
            .group_by(AreaEnergyStat.timespan_15min)
            .all()
        )
    elif floor_ids:
        rows = (
            db.query(
                AreaEnergyStat.timespan_15min,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .filter(Area.id == AreaEnergyStat.area_id)
            .filter(Area.floor_id.in_(floor_ids))
            .group_by(AreaEnergyStat.timespan_15min)
            .all()
        )
    else:
        rows = (
            db.query(
                AreaEnergyStat.timespan_15min,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .group_by(AreaEnergyStat.timespan_15min)
            .all()
        )
    time_series = {}
    if rows:
        for row in rows:
            time_series[row.timespan_15min] = {
                "energy_consumed_in_Wh": row.energy_consumed_in_Wh,
                "energy_saved_in_Wh": row.energy_saved_in_Wh
            }
    consumption = []
    savings = []
    peak_consumption = 0
    peak_consumption_time = None
    min_consumption = 0
    min_consumption_time = None
    peak_savings = 0
    for timespan_with_colon in x_axis:
        timespan = timespan_with_colon.replace(":", "")
        if timespan in time_series:
            consumption.append(time_series[timespan]["energy_consumed_in_Wh"])
            savings.append(time_series[timespan]["energy_saved_in_Wh"])
            if peak_consumption_time:
                if peak_consumption < time_series[timespan]["energy_consumed_in_Wh"]:
                    peak_consumption = time_series[timespan]["energy_consumed_in_Wh"]
                    peak_consumption_time = timespan_with_colon
            else:
                peak_consumption = time_series[timespan]["energy_consumed_in_Wh"]
                peak_consumption_time = timespan_with_colon

            if min_consumption_time:
                if min_consumption > time_series[timespan]["energy_consumed_in_Wh"]:
                    min_consumption = time_series[timespan]["energy_consumed_in_Wh"]
                    min_consumption_time = timespan_with_colon
                    peak_savings = time_series[timespan]["energy_saved_in_Wh"]
            else:
                min_consumption = time_series[timespan]["energy_consumed_in_Wh"]
                min_consumption_time = timespan_with_colon
                peak_savings = time_series[timespan]["energy_saved_in_Wh"]

        else:
            consumption.append('null')
            savings.append('null')
    
    # if it is today's data, add 24:00 to x_axis and append null values
    if(data_date == date.today()):
        x_axis.append("24:00")
        consumption.append('null')
        savings.append('null')
    else:
        # if it is not of today then we need to append first data of the next day
        next_day = data_date + timedelta(days=1)
        if area_ids:
            rows = (
                db.query(
                    AreaEnergyStat.timespan_15min,
                    func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                    func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
                )
                .filter(AreaEnergyStat.created_date == next_day)
                .filter(AreaEnergyStat.timespan_15min == "0000")
                .filter(AreaEnergyStat.area_id.in_(area_ids))
                .group_by(AreaEnergyStat.timespan_15min)
                .all()
            )
        elif floor_ids:
            rows = (
                db.query(
                    AreaEnergyStat.timespan_15min,
                    func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                    func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
                )
                .filter(AreaEnergyStat.created_date == next_day)
                .filter(AreaEnergyStat.timespan_15min == "0000")
                .filter(Area.id == AreaEnergyStat.area_id)
                .filter(Area.floor_id.in_(floor_ids))
                .group_by(AreaEnergyStat.timespan_15min)
                .all()
            )
        else:
            rows = (
                db.query(
                    AreaEnergyStat.timespan_15min,
                    func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                    func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
                )
                .filter(AreaEnergyStat.created_date == next_day)
                .filter(AreaEnergyStat.timespan_15min == "0000")
                .group_by(AreaEnergyStat.timespan_15min)
                .all()
            )
        if rows:
            row = rows[0]
            consumption.append(row.energy_consumed_in_Wh)
            savings.append(row.energy_saved_in_Wh)

            if peak_consumption < row.energy_consumed_in_Wh:
                peak_consumption = row.energy_consumed_in_Wh
                peak_consumption_time = "24:00"

            if min_consumption > row.energy_consumed_in_Wh:
                min_consumption = row.energy_consumed_in_Wh
                min_consumption_time = "24:00"
                peak_savings = row.energy_saved_in_Wh
        else:
            consumption.append('null')
            savings.append('null')
        x_axis.append("24:00")
        

    # Now we have all the data, lets compute derived values
    limit = math.ceil(max(peak_consumption, peak_savings))
    if limit <= 2000:
        unit = "Wh"
    elif limit <= 2000000:
        unit = "kWh"
        limit = math.ceil(limit / 1000)
        peak_consumption = round(peak_consumption / 1000, 2)
        min_consumption = round(min_consumption / 1000, 2)
        peak_savings = round(peak_savings / 1000, 2)

    else:
        unit = "MWh"
        limit = math.ceil(limit / 1000000)
        peak_consumption = round(peak_consumption / 1000000, 2)
        min_consumption = round(min_consumption / 1000000, 2)
        peak_savings = round(peak_savings / 1000000, 2)

    for i in range(len(x_axis)):
        if unit == "kWh":
            consumption[i] = round(consumption[i] / 1000, 2) if consumption[i] != 'null' else 'null'
            savings[i] = round(savings[i] / 1000, 2) if savings[i] != 'null' else 'null'

        if unit == "MWh":
            consumption[i] = round(consumption[i] / 1000000, 2) if consumption[i] != 'null' else 'null'
            savings[i] = round(savings[i] / 1000000, 2) if savings[i] != 'null' else 'null'

    return {
        "chart-type": "day",
        "x-axis": x_axis, 
        "consumption": consumption, 
        "savings": savings, 
        "max_limit": limit, 
        "unit": unit, 
        "consumption_peak":{"value": peak_consumption, "time": peak_consumption_time}, 
        "consumption_min":{"value": min_consumption, "time": min_consumption_time}
        }

def get_data_of_6_hrs(db: Session, area_ids, floor_ids, data_date, data_time_span_6_hrs):

    if area_ids:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .filter(AreaEnergyStat.area_id.in_(area_ids))
            .filter(AreaEnergyStat.timespan_6hr == data_time_span_6_hrs)
            .group_by(AreaEnergyStat.timespan_6hr)
            .all()
        )
    elif floor_ids:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .filter(Area.id == AreaEnergyStat.area_id)
            .filter(Area.floor_id.in_(floor_ids))
            .filter(AreaEnergyStat.timespan_6hr == data_time_span_6_hrs)
            .group_by(AreaEnergyStat.timespan_6hr)
            .all()
        )
    else:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date == data_date)
            .filter(AreaEnergyStat.timespan_6hr == data_time_span_6_hrs)
            .group_by(AreaEnergyStat.timespan_6hr)
            .all()
        )
    if rows:
        row = rows[0]
        return {"consumption": row.energy_consumed_in_Wh, "savings": row.energy_saved_in_Wh}
    return {"consumption": 0, "savings": 0}# This would happen when we query for the date and time where there were no data

def get_unified_energy_data_of_a_week(db: Session, area_ids, floor_ids, time_range, start_date, end_date):
    
    today = datetime.today().date()
    x_axis = []
    if(time_range == "this_week"):
        start_date = today - timedelta(days=(today.weekday() + 1) % 7)
        days_until_saturday = (5 - today.weekday()) % 7
        end_date = today + timedelta(days=days_until_saturday)
        x_axis = ["Sun 0", "Sun 6", "Sun 12", "Sun 18", 
                  "Mon 0", "Mon 6", "Mon 12", "Mon 18",
                  "Tue 0", "Tue 6", "Tue 12", "Tue 18",
                  "Wed 0", "Wed 6", "Wed 12", "Wed 18",
                  "Thu 0", "Thu 6", "Thu 12", "Thu 18",
                  "Fri 0", "Fri 6", "Fri 12", "Fri 18",
                  "Sat 0", "Sat 6", "Sat 12", "Sat 18", "Sat 24"]
 
    else:
        index_date = start_date
        while index_date <= end_date:
            x_axis.append(index_date.strftime(f"{index_date.day}/{index_date.month} 0"))
            x_axis.append(index_date.strftime(f"{index_date.day}/{index_date.month} 6"))
            x_axis.append(index_date.strftime(f"{index_date.day}/{index_date.month} 12"))
            x_axis.append(index_date.strftime(f"{index_date.day}/{index_date.month} 18"))
            index_date += timedelta(days=1)
        index_date -= timedelta(days=1)
        x_axis.append(index_date.strftime(f"{index_date.day}/{index_date.month} 24"))
       
    consumption = []
    savings = []
    peak_consumption = 0
    peak_consumption_time = None
    min_consumption = 0
    min_consumption_time = None
    peak_savings = 0
    data_date = start_date - timedelta(days=1) # First dot in the chart belongs last 6 Hrs of previous day
    data_time_span_6_hrs = 18 
    last_span_of_today = 18 if datetime.now().hour >= 18 else 12 if datetime.now().hour >= 12 else 6 if datetime.now().hour >= 6 else 0
    for timespan in x_axis:
        if (
            data_date < today or
            (
                data_date == today and data_time_span_6_hrs < last_span_of_today
            )
        ):
            data_of_6_hrs = get_data_of_6_hrs(db, area_ids, floor_ids, data_date, data_time_span_6_hrs)
            consumption.append(data_of_6_hrs["consumption"])
            savings.append(data_of_6_hrs["savings"])

            if peak_consumption_time:
                if data_of_6_hrs["consumption"] > peak_consumption:
                    peak_consumption = data_of_6_hrs["consumption"]
                    peak_consumption_time = timespan
            else:
                peak_consumption = data_of_6_hrs["consumption"]
                peak_consumption_time = timespan

            if min_consumption_time:
                if data_of_6_hrs["consumption"] < min_consumption:
                    min_consumption = data_of_6_hrs["consumption"]
                    min_consumption_time = timespan
                    peak_savings = data_of_6_hrs["savings"]
            else:
                min_consumption = data_of_6_hrs["consumption"]
                min_consumption_time = timespan
                peak_savings = data_of_6_hrs["savings"]
        else:
            consumption.append('null')
            savings.append('null')
        
        data_time_span_6_hrs = (data_time_span_6_hrs + 6) % 24
        if data_time_span_6_hrs == 0:
            data_date += timedelta(days=1)

    limit = math.ceil(max(peak_consumption, peak_savings))
    if limit <= 2000:
        unit = "Wh"
    elif limit <= 2000000:
        unit = "kWh"
        limit = math.ceil(limit / 1000)
        peak_consumption = round(peak_consumption / 1000, 2)
        min_consumption = round(min_consumption / 1000, 2)
        peak_savings = round(peak_savings / 1000, 2)

    else:
        unit = "MWh"
        limit = math.ceil(limit / 1000000)
        peak_consumption = round(peak_consumption / 1000000, 2)
        min_consumption = round(min_consumption / 1000000, 2)
        peak_savings = round(peak_savings / 1000000, 2)

    for i in range(len(x_axis)):
        if unit == "kWh":
            consumption[i] = round(consumption[i] / 1000, 2) if consumption[i] != 'null' else 'null'
            savings[i] = round(savings[i] / 1000, 2) if savings[i] != 'null' else 'null'

        if unit == "MWh":
            consumption[i] = round(consumption[i] / 1000000, 2) if consumption[i] != 'null' else 'null'
            savings[i] = round(savings[i] / 1000000, 2) if savings[i] != 'null' else 'null'
    
    return {
        "chart-type": "week",
        "x-axis": x_axis, 
        "consumption": consumption, 
        "savings": savings, 
        "max_limit": limit, 
        "unit": unit, 
        "consumption_peak":{"value": peak_consumption, "time": peak_consumption_time}, 
        "consumption_min":{"value": min_consumption, "time": min_consumption_time}
        }    

def get_unified_energy_data_of_a_month(db, area_ids, floor_ids, time_range, start_date, end_date):

    today = date.today()
    if time_range == "this_month":
        start_date = today.replace(day=1)
        end_date = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    x_axis = []
    current_date = start_date
    while current_date <= end_date:
        if time_range == "this_month":
            x_axis.append(current_date.strftime("%d"))
        else:
            x_axis.append(current_date.strftime("%d/%m"))
        current_date += timedelta(days=1)

    if time_range == "this_month":
        # if it is this month, we want to get the data till yesterday only. Today's data is incomplete
        end_date = today - timedelta(days=1)

    if area_ids:
        rows = (
            db.query(
                AreaEnergyStat.created_date,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .filter(AreaEnergyStat.area_id.in_(area_ids))
            .group_by(AreaEnergyStat.created_date)
            .all()
        )
    elif floor_ids:
        rows = (
            db.query(
                AreaEnergyStat.created_date,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .filter(Area.id == AreaEnergyStat.area_id)
            .filter(Area.floor_id.in_(floor_ids))
            .group_by(AreaEnergyStat.created_date)
            .all()
        )
    else:
        rows = (
            db.query(
                AreaEnergyStat.created_date,
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .group_by(AreaEnergyStat.created_date)
            .all()
        )
    time_series = {}
    peak_consumption = 0
    peak_consumption_time = None
    min_consumption = 0
    min_consumption_time = None
    peak_savings = 0
    if rows:
        for row in rows:
            created_date = row.created_date
            if time_range == "this_month":
                created_date_short_str = created_date.strftime("%d")
            else:
                created_date_short_str = created_date.strftime("%d/%m")

            time_series[created_date_short_str] = {
                "energy_consumed_in_Wh": row.energy_consumed_in_Wh,
                "energy_saved_in_Wh": row.energy_saved_in_Wh
            }
            if peak_consumption_time:
                if row.energy_consumed_in_Wh > peak_consumption:
                    peak_consumption = row.energy_consumed_in_Wh
                    peak_consumption_time = created_date_short_str
            else:
                peak_consumption = row.energy_consumed_in_Wh
                peak_consumption_time = created_date_short_str

            if min_consumption_time:
                if row.energy_consumed_in_Wh < min_consumption:
                    min_consumption = row.energy_consumed_in_Wh
                    min_consumption_time = created_date_short_str
                    peak_savings = row.energy_saved_in_Wh
            else:
                min_consumption = row.energy_consumed_in_Wh
                min_consumption_time = created_date_short_str
                peak_savings = row.energy_saved_in_Wh

    limit = math.ceil(max(peak_consumption, peak_savings))
    if limit <= 2000:
        unit = "Wh"
    elif limit <= 2000000:
        unit = "kWh"
        limit = math.ceil(limit / 1000)
        peak_consumption = round(peak_consumption / 1000, 2)
        min_consumption = round(min_consumption / 1000, 2)
        peak_savings = round(peak_savings / 1000, 2)

    else:
        unit = "MWh"
        limit = math.ceil(limit / 1000000)
        peak_consumption = round(peak_consumption / 1000000, 2)
        min_consumption = round(min_consumption / 1000000, 2)
        peak_savings = round(peak_savings / 1000000, 2)

    consumption = []
    savings = []
    for i in range(len(x_axis)):
        date_str = x_axis[i]
        if date_str in time_series:
            if unit == "Wh":
                consumption.append(time_series[date_str]["energy_consumed_in_Wh"])
                savings.append(time_series[date_str]["energy_saved_in_Wh"])
            elif unit == "kWh":
                consumption.append(round(time_series[date_str]["energy_consumed_in_Wh"] / 1000, 2))
                savings.append(round(time_series[date_str]["energy_saved_in_Wh"] / 1000, 2)) 

            if unit == "MWh":
                consumption.append(round(time_series[date_str]["energy_consumed_in_Wh"] / 1000000, 2))
                savings.append(round(time_series[date_str]["energy_saved_in_Wh"] / 1000000, 2)) 
        else:
            consumption.append('null')
            savings.append('null')

    return {
        "chart-type": "month",
        "x-axis": x_axis, 
        "consumption": consumption, 
        "savings": savings, 
        "max_limit": limit, 
        "unit": unit, 
        "consumption_peak":{"value": peak_consumption, "time": peak_consumption_time}, 
        "consumption_min":{"value": min_consumption, "time": min_consumption_time},
        }    

def get_data_of_a_week(db, area_ids, floor_ids, start_date, end_date):
    if area_ids:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .filter(AreaEnergyStat.area_id.in_(area_ids))
            .all()
        )
    elif floor_ids:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .filter(Area.id == AreaEnergyStat.area_id)
            .filter(Area.floor_id.in_(floor_ids))
            .all()
        )
    else:
        rows = (
            db.query(
                func.sum(AreaEnergyStat.energy_consumed_in_Wh).label("energy_consumed_in_Wh"),
                func.sum(AreaEnergyStat.energy_saved_in_Wh).label("energy_saved_in_Wh"),
            )
            .filter(AreaEnergyStat.created_date.between(start_date, end_date))
            .all()
        )
    row = rows[0]
    if row.energy_consumed_in_Wh is not None and row.energy_saved_in_Wh is not None:
        return {
            "energy_consumed_in_Wh": row.energy_consumed_in_Wh,
            "energy_saved_in_Wh": row.energy_saved_in_Wh
        }
    return None


def get_unified_energy_data_of_a_year(db, area_ids, floor_ids, time_range, start_date, end_date):
    today = datetime.today().date()
    x_axis = []
    if(time_range == "this_year"):
        start_date = today.replace(year=today.year - 1, month=12, day=22)
        today_day = int(today.strftime("%d"))
        end_span = 1 if today_day < 7 else 2 if today_day < 14 else 3 if today_day < 21 else 4
        end_date = today.replace(day=1) - timedelta(days=1) if end_span == 1 else today.replace(day=7) if end_span == 2 else today.replace(day=14) if end_span == 3 else today.replace(day=21)
        x_axis = [
            "Jan 01", "Jan 08", "Jan 15", "Jan 22", 
            "Feb 01", "Feb 08", "Feb 15", "Feb 22",
            "Mar 01", "Mar 08", "Mar 15", "Mar 22",
            "Apr 01", "Apr 08", "Apr 15", "Apr 22",
            "May 01", "May 08", "May 15", "May 22",
            "Jun 01", "Jun 08", "Jun 15", "Jun 22",
            "Jul 01", "Jul 08", "Jul 15", "Jul 22",
            "Aug 01", "Aug 08", "Aug 15", "Aug 22",
            "Sep 01", "Sep 08", "Sep 15", "Sep 22",
            "Oct 01", "Oct 08", "Oct 15", "Oct 22",
            "Nov 01", "Nov 08", "Nov 15", "Nov 22",
            "Dec 01", "Dec 08", "Dec 15", "Dec 22", "Dec 31"
        ]
    else:
        start_day = int(start_date.strftime("%d"))
        start_span = 1 if start_day < 7 else 2 if start_day < 14 else 3 if start_day < 21 else 4
        start_date = start_date.replace(day=1) if start_span == 1 else start_date.replace(day=8) if start_span == 2 else start_date.replace(day=15) if start_span == 3 else start_date.replace(day=22)
        end_span = 1 if end_date.day < 7 else 2 if end_date.day < 14 else 3 if end_date.day < 21 else 4
        end_date = end_date.replace(day=7) if end_span == 1 else end_date.replace(day=14) if end_span == 2 else end_date.replace(day=21) if end_span == 3 else end_date.replace(day=calendar.monthrange(end_date.year, end_date.month)[1])

        date_index = start_date + timedelta(days=7)
        while date_index <= end_date:
            x_axis.append(date_index.strftime("%b %d"))
            if date_index.strftime("%d") == "22":
                date_index = date(date_index.year + (date_index.month == 12), (date_index.month % 12) + 1, 1)
            else:
                date_index += timedelta(days=7)
    
    time_series = {}
    peak_consumption = 0
    peak_consumption_time = None
    min_consumption = 0
    min_consumption_time = None
    peak_savings = 0
    date_index = start_date
    while date_index < end_date:
        data = get_data_of_a_week(db, area_ids, floor_ids, date_index, date_index+timedelta(days=6))
        if date_index.strftime("%d") == "22":
            date_index = date(date_index.year + (date_index.month == 12), (date_index.month % 12) + 1, 1)
        else:
            date_index += timedelta(days=7)

        date_str = date_index.strftime("%b %d")
        if data:
            print (data)
            time_series[date_str] = {
                "energy_consumed_in_Wh": data['energy_consumed_in_Wh'],
                "energy_saved_in_Wh": data['energy_saved_in_Wh']
            }
            if peak_consumption_time:
                if data['energy_consumed_in_Wh'] > peak_consumption:
                    peak_consumption = data['energy_consumed_in_Wh']
                    peak_consumption_time = date_str
            else:
                peak_consumption = data['energy_consumed_in_Wh']
                peak_consumption_time = date_str

            if min_consumption_time:
                if data['energy_consumed_in_Wh'] < min_consumption:
                    min_consumption = data['energy_consumed_in_Wh']
                    min_consumption_time = date_str
                    peak_savings = data["energy_saved_in_Wh"]
            else:
                min_consumption = data['energy_consumed_in_Wh']
                min_consumption_time = date_str
                peak_savings = data["energy_saved_in_Wh"]
        else:
            time_series[date_str] = {
                "energy_consumed_in_Wh": 0,
                "energy_saved_in_Wh": 0
            }

    limit = math.ceil(max(peak_consumption, peak_savings))
    if limit <= 2000:
        unit = "Wh"
    elif limit <= 2000000:
        unit = "kWh"
        limit = math.ceil(limit / 1000)
        peak_consumption = round(peak_consumption / 1000, 2)
        min_consumption = round(min_consumption / 1000, 2)
        peak_savings = round(peak_savings / 1000, 2)
    else:
        unit = "MWh"
        limit = math.ceil(limit / 1000000)
        peak_consumption = round(peak_consumption / 1000000, 2)
        min_consumption = round(min_consumption / 1000000, 2)
        peak_savings = round(peak_savings / 1000000, 2)

    consumption = []
    savings = []
    for i in range(len(x_axis)):
        date_str = x_axis[i]
        if date_str in time_series:
            if unit == "Wh":
                consumption.append(time_series[date_str]["energy_consumed_in_Wh"])
                savings.append(time_series[date_str]["energy_saved_in_Wh"])
            elif unit == "kWh":
                consumption.append(round(time_series[date_str]["energy_consumed_in_Wh"] / 1000, 2))
                savings.append(round(time_series[date_str]["energy_saved_in_Wh"] / 1000, 2)) 

            if unit == "MWh":
                consumption.append(round(time_series[date_str]["energy_consumed_in_Wh"] / 1000000, 2))
                savings.append(round(time_series[date_str]["energy_saved_in_Wh"] / 1000000, 2)) 
        else:
            consumption.append('null')
            savings.append('null')

    return {
        "chart-type": "year",
        "x-axis": x_axis, 
        "consumption": consumption, 
        "savings": savings, 
        "max_limit": limit, 
        "unit": unit, 
        "consumption_peak":{"value": peak_consumption, "time": peak_consumption_time}, 
        "consumption_min":{"value": min_consumption, "time": min_consumption_time},
        }  

