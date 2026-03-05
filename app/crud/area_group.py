from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func, case
from app.models.area_group import AreaGroup, AreaGroupMapping
from app.models.area import Area
from app.models.floor import Floor
from app.models.occupancy_logs import OccupancyLog
from app.schemas.area_group import AreaGroupCreate, AreaGroupMappingCreate, AreaGroupOut, AreaGroupArea, CombinedAreaGroupCreate
import csv
from io import StringIO
from fastapi import UploadFile, HTTPException, File
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import calendar

def create_group_with_area_codes(payload: CombinedAreaGroupCreate, db: Session, current_user):
    # Enforce that only Superadmin can create special groups
    if payload.special:
        if current_user.role != "Superadmin":
            raise HTTPException(status_code=403, detail="Only Superadmin can create special area groups")

    # Build distinct set and validate areas
    seen_areas = set()  # Will store (area_code, processor_id) tuples for deduplication
    validated_areas = []  # Will store validated Area objects with their floor_id
    
    for floor in payload.floors:
        for area_id in floor.area_ids:
            # Query area by ID
            area = db.query(Area).filter(Area.id == area_id).first()
            if not area:
                raise HTTPException(status_code=404, detail=f"Area with ID {area_id} not found")
            
            # Create composite key for deduplication: (area_code, processor_id)
            composite_key = (area.code, area.processor_id)
            
            # Skip if we've already processed this area (distinct check)
            if composite_key in seen_areas:
                continue  # Skip duplicate
            
            seen_areas.add(composite_key)
            validated_areas.append({
                'area': area,
                'floor_id': floor.floor_id
            })

    # Special group validation - check for conflicts
    if payload.special:
        for validated_area in validated_areas:
            area = validated_area['area']
            existing_mappings = (
                db.query(AreaGroupMapping, AreaGroup)
                .join(AreaGroup, AreaGroup.id == AreaGroupMapping.group_id)
                .filter(
                    AreaGroup.special == True,
                    AreaGroupMapping.area_id == area.id
                )
                .first()
            )
            if existing_mappings:
                mapping, group = existing_mappings
                raise HTTPException(
                    status_code=400,
                    detail=f"Area '{area.name}' (Code: {area.code}, Processor ID: {area.processor_id}) is already part of another special group '{group.name}'"
                )

    # Create the new area group
    new_group = AreaGroup(name=payload.name, special=payload.special)
    db.add(new_group)
    db.commit()
    db.refresh(new_group)

    # Create mappings for all distinct validated areas
    for validated_area in validated_areas:
        area = validated_area['area']
        floor_id = validated_area['floor_id']
        
        mapping = AreaGroupMapping(
            group_id=new_group.id,
            area_id=area.id,  
            floor_id=floor_id
        )
        db.add(mapping)

    db.commit()

    return {
        "status": "success",
        "group_id": new_group.id,
        "message": "Area group created successfully"
    }


def get_area_groups(db: Session)-> dict[str, list[AreaGroupOut]]:
    groups = db.query(AreaGroup).options(
        joinedload(AreaGroup.mappings).joinedload(AreaGroupMapping.area)
    ).all()
    special_groups = []
    user_groups = []

    for group in groups:
        seen_areas = set() 
        areas = []
        for m in group.mappings:
            key = (m.area_id, m.floor_id)
            if key in seen_areas:
                continue  
            seen_areas.add(key)

            if m.area:
                areas.append(AreaGroupArea(
                    area_id=m.area.id,
                    floor_id=m.floor_id,  # Use mapping's floor_id instead of area's floor_id
                    name=m.area.name
                ))
            else:
                areas.append(AreaGroupArea(
                    area_id=m.area_id,
                    floor_id=m.floor_id,
                    name=None
                ))

        group_data = AreaGroupOut(
            group_id=group.id,
            name=group.name,
            special=group.special,
            areas=areas
        )

        if group.special:
            special_groups.append(group_data)
        else:
            user_groups.append(group_data)


    return {
        "special_area_groups": special_groups,
        "user_area_groups": user_groups
    }

def update_group_with_area_codes(group_id: int, payload: CombinedAreaGroupCreate, db: Session, current_user):
    group = db.query(AreaGroup).filter(AreaGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Area group not found")

    # Only Superadmin can mark group as special
    if payload.special and current_user.role != "Superadmin":
        raise HTTPException(status_code=403, detail="Only Superadmin can make a group special")

    # Build distinct set and validate areas
    seen_areas = set()  # Will store (area_code, processor_id) tuples for deduplication
    validated_areas = []  # Will store validated Area objects with their floor_id
    
    for floor in payload.floors:
        for area_id in floor.area_ids:
            # Query area by ID
            area = db.query(Area).filter(Area.id == area_id).first()
            if not area:
                raise HTTPException(status_code=404, detail=f"Area with ID {area_id} not found")
            
            # Create composite key for deduplication: (area_code, processor_id)
            composite_key = (area.code, area.processor_id)
            
            # Skip if we've already processed this area (distinct check)
            if composite_key in seen_areas:
                continue  # Skip duplicate
            
            seen_areas.add(composite_key)
            validated_areas.append({
                'area': area,
                'floor_id': floor.floor_id
            })

    # Special group conflict check (exclude current group)
    if payload.special:
        for validated_area in validated_areas:
            area = validated_area['area']
            exists = (
                db.query(AreaGroupMapping, AreaGroup)
                .join(AreaGroup, AreaGroup.id == AreaGroupMapping.group_id)
                .filter(
                    AreaGroupMapping.area_id == area.id,
                    AreaGroupMapping.group_id != group_id,  # exclude current group
                    AreaGroup.special == True
                )
                .first()
            )
            if exists:
                existing_map, existing_group = exists
                raise HTTPException(
                    status_code=400,
                    detail=f"Area '{area.name}' (Code: {area.code}, Processor ID: {area.processor_id}) already exists in another special group '{existing_group.name}'"
                )

    # Update group fields
    group.name = payload.name
    group.special = payload.special
    db.commit()

    # Delete existing mappings
    db.query(AreaGroupMapping).filter(AreaGroupMapping.group_id == group.id).delete()

    # Recreate mappings for all distinct validated areas
    for validated_area in validated_areas:
        area = validated_area['area']
        floor_id = validated_area['floor_id']
        
        mapping = AreaGroupMapping(
            group_id=group.id,
            area_id=area.id,
            floor_id=floor_id
        )
        db.add(mapping)

    db.commit()

    return {
        "status": "success",
        "message": "Area group updated successfully"
    }



def upload_area_group_csv(file: UploadFile, db: Session):
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(StringIO(content))

    row_num = 1
    for row in reader:
        row_num += 1
        group_name = row.get("group_name", "").strip()
        floor_name = row.get("floor_name", "").strip()
        area_names = [a.strip() for a in row.get("area_names", "").split(";") if a.strip()]

        if not group_name or not floor_name or not area_names:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields in row {row_num}"
            )

        floor = db.query(Floor).filter_by(name=floor_name).first()
        if not floor:
            raise HTTPException(
                status_code=400,
                detail=f"Floor '{floor_name}' not found in row {row_num}"
            )

        group = db.query(AreaGroup).filter_by(name=group_name).first()
        if not group:
            group = AreaGroup(name=group_name)
            db.add(group)
            db.commit()
            db.refresh(group)

        for area_name in area_names:
            area = db.query(Area).filter_by(name=area_name, floor_id=floor.id).first()
            if not area:
                raise HTTPException(
                    status_code=400,
                    detail=f"Area '{area_name}' not found in floor '{floor_name}' (row {row_num})"
                )

            exists = db.query(AreaGroupMapping).filter_by(group_id=group.id, area_id=area.id).first()
            if not exists:
                mapping = AreaGroupMapping(group_id=group.id, area_id=area.id, floor_id=floor.id)
                db.add(mapping)

    db.commit()


def generate_area_group_csv(db) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["group_name", "floor_name", "area_names"])  # Template only
    return output.getvalue()


def occupancy_percentage_by_area_group_from_logs(
    db: Session,
    area_ids: Optional[List[int]],
    floor_ids: Optional[List[int]],
    time_range: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Calculate occupancy percentage for area groups based on occupancy_logs table.
    
    This function calculates the percentage of time each area group is occupied vs unoccupied
    by analyzing occupancy status changes over time. An area group is considered:
    - Occupied: if at least one area in the group is occupied
    - Unoccupied: if all areas in the group are unoccupied
    
    Args:
        db: Database session
        area_ids: Optional list of area IDs to filter by
        floor_ids: Optional list of floor IDs to filter by
        time_range: Time range type ("this_day", "this_week", "this_month", "this_year", "custom")
        start_date: Start datetime (required for custom time_range)
        end_date: End datetime (required for custom time_range)
    
    Returns:
        List of dictionaries with occupancy statistics for each area group:
        - area_group_id: ID of the area group
        - area_group_name: Name of the area group
        - occupied_percentage: Percentage of time the group was occupied (0-100)
        - unoccupied_percentage: Percentage of time the group was unoccupied (0-100)
        - total_occupied_seconds: Total seconds the group was occupied
        - total_unoccupied_seconds: Total seconds the group was unoccupied
        - total_time_seconds: Total time range in seconds
    """
    now = datetime.now()
    
    # Resolve time range
    if time_range == "this_day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif time_range == "this_week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
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
    
    # Resolve areas
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    
    areas = query.all()
    if not areas:
        areas = db.query(Area).all()
    if not areas:
        return []
    
    area_ids_list = [a.id for a in areas]
    
    # Find all unique special groups for input area_ids
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
        # Get all area_ids in this group
        group_area_ids = db.query(AreaGroupMapping.area_id) \
            .filter(AreaGroupMapping.group_id == group.id).all()
        group_area_ids = [row[0] for row in group_area_ids]
        
        if not group_area_ids:
            continue
        
        # Calculate occupancy percentage for this group
        group_stats = _calculate_group_occupancy_percentage(
            db=db,
            group_id=group.id,
            group_area_ids=group_area_ids,
            start_date=start_date,
            end_date=end_date
        )
        
        if group_stats:
            group_stats["area_group_id"] = group.id
            group_stats["area_group_name"] = group.name
            results.append(group_stats)
    
    return results


def _calculate_group_occupancy_percentage(
    db: Session,
    group_id: int,
    group_area_ids: List[int],
    start_date: datetime,
    end_date: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Helper function to calculate occupancy percentage for a single area group.
    Uses the same logic as get_space_utilization_by_area_from_logs_optimized:
    calculates occupancy percentage for each area in the group, then averages them.
    
    Args:
        db: Database session
        group_id: ID of the area group
        group_area_ids: List of area IDs in the group
        start_date: Start datetime
        end_date: End datetime
    
    Returns:
        Dictionary with occupancy statistics or None if no data
    """
    if not group_area_ids:
        return {
            "occupied_percentage": 0.0,
            "unoccupied_percentage": 0.0
        }
    
    # Get areas for this group
    areas = db.query(Area).filter(Area.id.in_(group_area_ids)).all()
    if not areas:
        return {
            "occupied_percentage": 0.0,
            "unoccupied_percentage": 0.0
        }
    
    now = datetime.now()
    area_percentages = []
    
    # Calculate occupancy percentage for each area using the same logic as get_space_utilization_by_area_from_logs_optimized
    for area in areas:
        area_id = area.id
        area_code = str(area.code) if area.code else None
        processor_id = area.processor_id
        
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
        
        # Calculate occupancy percentage for this area (don't round here to preserve precision)
        if total_timespan > 0:
            occupied_percent = (occupied_timespan / total_timespan) * 100
            area_percentages.append(occupied_percent)
    
    # Calculate average occupancy percentage for the group
    if not area_percentages:
        occupied_percentage = 0.0
    else:
        occupied_percentage = round(sum(area_percentages) / len(area_percentages), 2)
    
    # Calculate unoccupied percentage as 100 - occupied
    unoccupied_percentage = round(100.0 - occupied_percentage, 2)
    
    return {
        "occupied_percentage": occupied_percentage,
        "unoccupied_percentage": unoccupied_percentage
    }
