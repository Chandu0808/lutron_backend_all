from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import io
import csv
import calendar

from app.models.area_energy_stats import AreaEnergyStat
from app.models.area import Area
from app.models.area_occupancy_stats import AreaOccupancyStat
from app.models.floor import Floor
from app.crud.energy_stats import get_energy_consumption, get_energy_savings, get_occupancy_count_over_time, get_total_consumption_by_area_id, get_space_utilization_by_area, spaceutilization_by_area_group, get_peak_min_occupancy
from app.crud.energy_stats_optimized import get_instant_occupancy_count_optimized, get_space_utilization_by_area_optimized, get_space_utilization_by_area_from_logs_optimized
from app.crud.area_group import occupancy_percentage_by_area_group_from_logs
from app.utils.energy_unit_converter import convert_single_energy_value


def generate_energy_consumption_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for energy consumption export.
    Returns CSV as string.
    """
    # Get chart data from existing function
    try:
        chart_response = get_energy_consumption(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    x_axis = chart_response.get("x-axis", [])
    y_axis = chart_response.get("y-axis", {})
    unit = chart_response.get("unit", "Wh")
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Fetch areas for "Full On" calculation
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()
    
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}
    
    # Build OR conditions for composite key
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    # Calculate "Full On" capacity (sum of max power for all areas)
    max_power_records = (
        db.query(AreaEnergyStat.instantaneous_max_power)
        .filter(AreaEnergyStat.created_at >= start_dt)
        .filter(AreaEnergyStat.created_at <= end_dt)
        .filter(or_(*area_conditions))
        .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
        .limit(len(areas) * 10)  # Get some samples
        .all()
    )
    
    # Calculate Full On as sum of max powers / 4
    full_on_capacity = 0
    if max_power_records:
        # Group by timestamp to get one value per area, then sum
        unique_max_values = {}
        temp_records = (
            db.query(
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id,
                AreaEnergyStat.instantaneous_max_power
            )
            .filter(AreaEnergyStat.created_at >= start_dt)
            .filter(AreaEnergyStat.created_at <= end_dt)
            .filter(or_(*area_conditions))
            .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
            .limit(len(areas) * 10)
            .all()
        )
        
        for record in temp_records:
            key = f"{record.area_code}_{record.processor_id}"
            if key not in unique_max_values:
                unique_max_values[key] = record.instantaneous_max_power
        
        full_on_capacity = round(sum(unique_max_values.values()) / 4, 2)
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Energy Consumption", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Chart Data section
    writer.writerow(["Chart Data", "", ""])
    
    # Chart headers
    area_names = list(y_axis.keys())
    unit_suffix = f" ({unit})" if unit else ""
    chart_headers = ["Time"] + [
        f"{name}{unit_suffix}" if name.lower() == "combined areas" else name
        for name in area_names
    ]
    writer.writerow(chart_headers)
    
    # Chart rows
    for i, time_label in enumerate(x_axis):
        row = [time_label]
        for area_name in area_names:
            value = y_axis[area_name][i]
            row.append(value if value is not None else "")
        writer.writerow(row)
    
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    
    # Raw Data section (skip for this_day or custom single day)
    total_days = (end_dt.date() - start_dt.date()).days + 1
    is_single_day = total_days == 1
    
    if not (time_range == "this_day" or (time_range == "custom" and is_single_day)):
        writer.writerow(["Raw Data", "", ""])
        
        # Fetch raw 15-minute data
        raw_results = (
            db.query(
                AreaEnergyStat.created_at,
                AreaEnergyStat.instantaneous_power,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .filter(AreaEnergyStat.created_at >= start_dt)
            .filter(AreaEnergyStat.created_at <= end_dt)
            .filter(or_(*area_conditions))
            .order_by(AreaEnergyStat.created_at)
            .all()
        )
        
        # Group by 15-minute buckets
        raw_data_by_time = {}
        for record in raw_results:
            # Create 15-min bucket key
            minute_bucket = (record.created_at.minute // 15) * 15
            time_key = record.created_at.replace(minute=minute_bucket, second=0, microsecond=0)
            
            if time_key not in raw_data_by_time:
                raw_data_by_time[time_key] = {}
            
            # Find area name from area_map
            area_name = None
            for aid, info in area_map.items():
                if str(record.area_code) == info["code"] and record.processor_id == info["processor_id"]:
                    area_name = info["name"]
                    break
            
            if area_name:
                if area_name not in raw_data_by_time[time_key]:
                    raw_data_by_time[time_key][area_name] = 0
                if record.instantaneous_power is not None:
                    raw_data_by_time[time_key][area_name] += record.instantaneous_power
        
        # Write raw data headers
        individual_area_names = [info["name"] for info in area_map.values()]
        raw_headers = ["Time"] + individual_area_names
        writer.writerow(raw_headers)
        
        # Write raw data rows
        for time_key in sorted(raw_data_by_time.keys()):
            row = [time_key.strftime("%b-%d-%Y %H:%M:%S")]
            for area_name in individual_area_names:
                value = raw_data_by_time[time_key].get(area_name, 0)
                # Divide by 4
                final_value = round(value / 4, 2) if value > 0 else ""
                row.append(final_value)
            writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_energy_savings_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for energy savings export.
    Returns CSV as string.
    """
    
    # Get chart data from existing function
    chart_response = get_energy_savings(
        db=db,
        area_ids=area_ids,
        floor_ids=floor_ids,
        time_range=time_range,
        start_date=start_date,
        end_date=end_date
    )
    
    x_axis = chart_response.get("x-axis", [])
    y_axis = chart_response.get("y-axis", {})
    unit = chart_response.get("unit", "Wh")
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Fetch areas for "Max Savings" calculation
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()
    
    area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}
    
    # Build OR conditions for composite key
    area_conditions = [
        and_(
            AreaEnergyStat.area_code == int(info["code"]),
            AreaEnergyStat.processor_id == info["processor_id"]
        )
        for info in area_map.values()
    ]
    
    # Calculate "Max Savings" (sum of max saved power for all areas)
    # Max savings = sum of instantaneous_max_power (which would be saved if all were off)
    max_savings_records = (
        db.query(
            AreaEnergyStat.area_code,
            AreaEnergyStat.processor_id,
            AreaEnergyStat.instantaneous_max_power
        )
        .filter(AreaEnergyStat.created_at >= start_dt)
        .filter(AreaEnergyStat.created_at <= end_dt)
        .filter(or_(*area_conditions))
        .filter(AreaEnergyStat.instantaneous_max_power.isnot(None))
        .limit(len(areas) * 10)
        .all()
    )
    
    # Calculate Max Savings as sum of max powers / 4
    max_savings_capacity = 0
    if max_savings_records:
        unique_max_values = {}
        for record in max_savings_records:
            key = f"{record.area_code}_{record.processor_id}"
            if key not in unique_max_values:
                unique_max_values[key] = record.instantaneous_max_power
        
        max_savings_capacity = round(sum(unique_max_values.values()) / 4, 2)
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Energy Savings", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Chart Data section
    writer.writerow(["Chart Data", "", ""])
    
    # Chart headers with unit information
    area_names = list(y_axis.keys())
    unit_suffix = f" ({unit})"
    chart_headers = ["Time"] + [f"{name}{unit_suffix}" for name in area_names]
    writer.writerow(chart_headers)
    
    # Chart rows
    for i, time_label in enumerate(x_axis):
        row = [time_label]
        for area_name in area_names:
            value = y_axis[area_name][i]
            row.append(value if value is not None else "")
        writer.writerow(row)
    
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    
    # Raw Data section (skip for this_day or custom single day)
    total_days = (end_dt.date() - start_dt.date()).days + 1
    is_single_day = total_days == 1
    
    if not (time_range == "this_day" or (time_range == "custom" and is_single_day)):
        writer.writerow(["Raw Data", "", ""])
        
        # Fetch raw 15-minute data
        raw_results = (
            db.query(
                AreaEnergyStat.created_at,
                AreaEnergyStat.instantaneous_saved_power,
                AreaEnergyStat.area_code,
                AreaEnergyStat.processor_id
            )
            .filter(AreaEnergyStat.created_at >= start_dt)
            .filter(AreaEnergyStat.created_at <= end_dt)
            .filter(or_(*area_conditions))
            .order_by(AreaEnergyStat.created_at)
            .all()
        )
        
        # Group by 15-minute buckets
        raw_data_by_time = {}
        for record in raw_results:
            # Create 15-min bucket key
            minute_bucket = (record.created_at.minute // 15) * 15
            time_key = record.created_at.replace(minute=minute_bucket, second=0, microsecond=0)
            
            if time_key not in raw_data_by_time:
                raw_data_by_time[time_key] = {}
            
            # Find area name from area_map
            area_name = None
            for aid, info in area_map.items():
                if str(record.area_code) == info["code"] and record.processor_id == info["processor_id"]:
                    area_name = info["name"]
                    break
            
            if area_name:
                if area_name not in raw_data_by_time[time_key]:
                    raw_data_by_time[time_key][area_name] = 0
                if record.instantaneous_saved_power is not None:
                    raw_data_by_time[time_key][area_name] += record.instantaneous_saved_power
        
        # Write raw data headers
        individual_area_names = [info["name"] for info in area_map.values()]
        raw_headers = ["Time"] + individual_area_names
        writer.writerow(raw_headers)
        
        # Write raw data rows
        for time_key in sorted(raw_data_by_time.keys()):
            row = [time_key.strftime("%b-%d-%Y %H:%M:%S")]
            for area_name in individual_area_names:
                value = raw_data_by_time[time_key].get(area_name, 0)
                # Divide by 4
                final_value = round(value / 4, 2) if value > 0 else ""
                row.append(final_value)
            writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_occupancy_count_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for occupancy count export.
    Returns CSV as string.
    """
    
    # Get chart data from existing function
    chart_response = get_occupancy_count_over_time(
        db=db,
        area_ids=area_ids,
        floor_ids=floor_ids,
        time_range=time_range,
        start_date=start_date,
        end_date=end_date
    )
    
    x_axis = chart_response.get("x-axis", [])
    y_axis = chart_response.get("y-axis", {})
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Occupancy Count", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Chart Data section
    writer.writerow(["Chart Data", "", ""])
    
    # Chart headers
    chart_headers = ["Time", "Occupancy"]
    writer.writerow(chart_headers)
    
    # Chart rows
    for i, time_label in enumerate(x_axis):
        row = [time_label]
        value = y_axis["data"][i]
        # Round occupancy values to whole numbers (0 or 1)
        if value is not None:
            rounded_value = round(value)
            row.append(rounded_value)
        else:
            row.append("")
        writer.writerow(row)
    
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    writer.writerow(["", "", ""])
    
    # Raw Data section (skip for this_day or custom single day)
    total_days = (end_dt.date() - start_dt.date()).days + 1
    is_single_day = total_days == 1
    
    if not (time_range == "this_day" or (time_range == "custom" and is_single_day)):
        writer.writerow(["Raw Data", "", ""])
        
        # Fetch areas for raw data
        query = db.query(Area)
        if floor_ids:
            query = query.filter(Area.floor_id.in_(floor_ids))
        if area_ids:
            query = query.filter(Area.id.in_(area_ids))
        areas = query.all()
        if not areas:
            areas = db.query(Area).all()
        
        area_map = {a.id: {"code": str(a.code), "name": a.name, "processor_id": a.processor_id} for a in areas}
        
        # Build OR conditions for composite key
        area_conditions = [
            and_(
                AreaOccupancyStat.area_code == info["code"],
                AreaOccupancyStat.processor_id == info["processor_id"]
            )
            for info in area_map.values()
        ]
        
        # Fetch raw occupancy data
        raw_results = (
            db.query(
                AreaOccupancyStat.created_at,
                AreaOccupancyStat.occupancy_status,
                AreaOccupancyStat.area_code,
                AreaOccupancyStat.processor_id
            )
            .filter(AreaOccupancyStat.created_at >= start_dt)
            .filter(AreaOccupancyStat.created_at <= end_dt)
            .filter(or_(*area_conditions))
            .order_by(AreaOccupancyStat.created_at)
            .all()
        )
        
        # Group by 15-minute buckets
        raw_data_by_time = {}
        for record in raw_results:
            # Create 15-min bucket key
            minute_bucket = (record.created_at.minute // 15) * 15
            time_key = record.created_at.replace(minute=minute_bucket, second=0, microsecond=0)
            
            if time_key not in raw_data_by_time:
                raw_data_by_time[time_key] = {}
            
            # Find area name from area_map
            area_name = None
            for aid, info in area_map.items():
                if str(record.area_code) == info["code"] and record.processor_id == info["processor_id"]:
                    area_name = info["name"]
                    break
            
            if area_name:
                if area_name not in raw_data_by_time[time_key]:
                    raw_data_by_time[time_key][area_name] = []
                # Convert occupancy status to binary value
                occupancy_value = 1 if record.occupancy_status == "Occupied" else 0
                raw_data_by_time[time_key][area_name].append(occupancy_value)
        
        # Write raw data headers
        individual_area_names = [info["name"] for info in area_map.values()]
        raw_headers = ["Time"] + individual_area_names
        writer.writerow(raw_headers)
        
        # Write raw data rows
        for time_key in sorted(raw_data_by_time.keys()):
            row = [time_key.strftime("%b-%d-%Y %H:%M:%S")]
            for area_name in individual_area_names:
                values = raw_data_by_time[time_key].get(area_name, [])
                # Calculate average occupancy and round to whole number (0 or 1)
                if values:
                    avg_value = round(sum(values) / len(values))
                else:
                    avg_value = ""
                row.append(avg_value)
            writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_total_consumption_by_group_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for total consumption by group export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = get_total_consumption_by_area_id(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Extract data from response
    special_area_groups = data.get("special_area_groups", [])
    widget_title = data.get("widget_title", "Consumption by area group")
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow([widget_title, "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Data section
    writer.writerow(["Area Group Data", "", ""])
    writer.writerow(["Group Name", "Consumption Percentage", "Actual Energy"])
    
    # Data rows
    for group in special_area_groups:
        writer.writerow([
            group.get("name", ""),
            group.get("consumption_percentage", ""),
            group.get("actual_energy", "")
        ])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_space_utilization_per_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for space utilization per area export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = get_space_utilization_by_area(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Extract data from response
    utilized_areas = data.get("utilized_area", [])
    widget_title = data.get("widget_title", "Utilization by area")
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow([widget_title, "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Data section
    writer.writerow(["Area Utilization Data", "", ""])
    writer.writerow(["Area Name", "Occupancy Percentage", ""])
    
    # Data rows
    for area in utilized_areas:
        occupancy_percent = area.get("occupied", 0)
        formatted_percent = f"{occupancy_percent}%" if occupancy_percent is not None else "0%"
        writer.writerow([
            area.get("name", ""),
            formatted_percent,
            ""
        ])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_occupancy_by_group_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for occupancy by group export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = spaceutilization_by_area_group(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Utilization by Area Group", "", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", "", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), "", ""])
    writer.writerow(["", "", "", ""])
    
    # Data section
    writer.writerow(["Area Group Data", "", "", ""])
    writer.writerow(["Group Name", "Total Occupied", "Total Possible", "Occupancy Percentage"])
    
    # Data rows
    for group in data:
        total_occupied = group.get("total_occupied", 0)
        total_possible = group.get("total_possible", 0)
        occupancy_percent = (total_occupied / total_possible * 100) if total_possible > 0 else 0.0
        formatted_percent = f"{round(occupancy_percent, 2)}%"
        
        writer.writerow([
            group.get("area_group_name", ""),
            total_occupied,
            total_possible,
            formatted_percent
        ])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_instant_occupancy_count_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for instant occupancy count export.
    Returns CSV as string.
    """
    # Get chart data from existing function
    try:
        chart_response = get_instant_occupancy_count_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    x_axis = chart_response.get("x-axis", [])
    y_axis = chart_response.get("y-axis", {})
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Instant Occupancy Count", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Chart Data section
    writer.writerow(["Chart Data", "", ""])
    
    # Chart headers
    chart_headers = ["Time", "Occupancy Count"]
    writer.writerow(chart_headers)
    
    # Chart rows - extract data from y_axis
    data_series = y_axis.get("data", []) if isinstance(y_axis, dict) else []
    if not data_series and isinstance(y_axis, dict):
        # Try to get first key if "data" doesn't exist
        if y_axis:
            first_key = next(iter(y_axis))
            data_series = y_axis[first_key] if isinstance(y_axis[first_key], list) else []
    
    for i, time_label in enumerate(x_axis):
        row = [time_label]
        if i < len(data_series):
            value = data_series[i]
            # Round occupancy values to whole numbers
            if value is not None:
                rounded_value = round(value)
                row.append(rounded_value)
            else:
                row.append("")
        else:
            row.append("")
        writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_occupancy_by_group_from_logs_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for occupancy by group from logs export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = occupancy_percentage_by_area_group_from_logs(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Occupancy by Group (From Logs)", "", "", "", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", "", "", "", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), "", "", "", ""])
    writer.writerow(["", "", "", "", "", ""])
    
    # Data section
    writer.writerow(["Area Group Data", "", "", "", "", ""])
    writer.writerow(["Group Name", "Occupied Percentage", "Unoccupied Percentage", "Total Occupied (seconds)", "Total Unoccupied (seconds)", "Total Time (seconds)"])
    
    # Data rows
    for group in data:
        writer.writerow([
            group.get("area_group_name", ""),
            f"{group.get('occupied_percentage', 0):.2f}%",
            f"{group.get('unoccupied_percentage', 0):.2f}%",
            group.get("total_occupied_seconds", 0),
            group.get("total_unoccupied_seconds", 0),
            group.get("total_time_seconds", 0)
        ])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_space_utilization_per_from_logs_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for space utilization per area from logs export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = get_space_utilization_by_area_from_logs_optimized(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Extract data from response
    utilized_areas = data.get("utilized_area", [])
    widget_title = data.get("widget_title", "Utilization by area (From Logs)")
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow([widget_title, "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Data section
    writer.writerow(["Area Utilization Data", "", ""])
    writer.writerow(["Area Name", "Occupancy Percentage", ""])
    
    # Data rows
    for area in utilized_areas:
        occupancy_percent = area.get("occupied", 0)
        formatted_percent = f"{occupancy_percent}%" if occupancy_percent is not None else "0%"
        writer.writerow([
            area.get("name", ""),
            formatted_percent,
            ""
        ])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content


def generate_peak_min_occupancy_from_logs_csv(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> str:
    """
    Generate CSV content for peak/min occupancy from logs export.
    Returns CSV as string.
    """
    # Get data from existing function
    try:
        data = get_peak_min_occupancy(
            db=db,
            area_ids=area_ids,
            floor_ids=floor_ids,
            time_range=time_range,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise
    
    # Determine actual date range
    now = datetime.now()
    if time_range == "this_day":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_dt = (now - timedelta(days=(now.weekday() + 1) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = (start_dt + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_month":
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(now.year, now.month)[1]
        end_dt = now.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_year":
        start_dt = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "custom":
        if not (start_date and end_date):
            raise ValueError("Custom range requires both start_date and end_date")
        start_dt = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        raise ValueError("Invalid time_range value")
    
    # Build CSV content
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Get selected area/floor names
    selected_names = []
    if area_ids:
        selected_areas = db.query(Area).filter(Area.id.in_(area_ids)).all()
        selected_names = [area.name for area in selected_areas if area.name]
    elif floor_ids:
        selected_floors = db.query(Floor).filter(Floor.id.in_(floor_ids)).all()
        selected_names = [floor.name for floor in selected_floors if floor.name]
    else:
        selected_names = ["All Areas"]
    
    # Header section
    writer.writerow(["Peak/Min Occupancy (From Logs)", "", ""])
    writer.writerow(["Date Range", f"From: {start_dt.strftime('%b-%d-%Y')} to: {end_dt.strftime('%b-%d-%Y')}", ""])
    writer.writerow(["Selected Areas:", ", ".join(selected_names), ""])
    writer.writerow(["", "", ""])
    
    # Data section
    writer.writerow(["Peak/Min Occupancy Data", "", ""])
    writer.writerow(["Metric", "Value", "Time"])
    
    # Extract peak and min data
    peak = data.get("peak", {})
    min_data = data.get("min", {})
    
    peak_value = peak.get("value")
    peak_time = peak.get("time", "")
    min_value = min_data.get("value")
    min_time = min_data.get("time", "")
    
    # Format values as percentages
    peak_formatted = f"{peak_value:.2f}%" if peak_value is not None else "N/A"
    min_formatted = f"{min_value:.2f}%" if min_value is not None else "N/A"
    
    writer.writerow(["Peak Occupancy", peak_formatted, peak_time])
    writer.writerow(["Min Occupancy", min_formatted, min_time])
    
    csv_content = output.getvalue()
    output.close()
    return csv_content
