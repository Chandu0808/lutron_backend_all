from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.models.occupancy_logs import OccupancyLog
from app.models.area import Area
from app.models.events import ProcessorAreaEvent
from datetime import datetime, date, time, timedelta
from typing import Optional, Dict, Any, List
from app.crud.area import get_area_occupancy_status
from app.crud.floor import get_area_occupancy_status_by_floor


def log_occupancy_change(
    db: Session,
    processor_id: int,
    area_id: Optional[int],
    area_code: Optional[str],
    occupancy_status: str,
    event_time: Optional[datetime] = None
) -> Optional[OccupancyLog]:
    """
    Log occupancy status changes to the occupancy_logs table.
    When occupation_status changes, calculates the time difference
    and updates the timespan of the previous log entry.
    
    Args:
        db: Database session
        processor_id: ID of the processor
        area_id: ID of the area (optional)
        area_code: Code of the area (optional)
        occupancy_status: New occupancy status ("Occupied" or "Unoccupied")
        event_time: Event timestamp (defaults to current time if not provided)
    
    Returns:
        The newly created OccupancyLog entry, or None if invalid status
    """
    # Validate occupancy status (only accept "Occupied" or "Unoccupied")
    if occupancy_status not in ["Occupied", "Unoccupied"]:
        return None
    
    # Use current time if not provided
    if event_time is None:
        event_time = datetime.now()
    
    # Remove milliseconds from event_time (round to seconds)
    event_time = event_time.replace(microsecond=0)
    
    # Extract area information if area_id is provided
    floor_id = None
    if area_id:
        area = db.query(Area).filter(Area.id == area_id).first()
        if area:
            floor_id = area.floor_id
            if not area_code:
                area_code = area.code
    
    # Extract date and time from event_time
    event_date = event_time.date()
    event_time_only = event_time.time()
    
    # Find the previous occupancy log for this area/processor
    # Filter by processor_id and (area_id or area_code)
    query = db.query(OccupancyLog).filter(
        OccupancyLog.processor_id == processor_id
    )
    
    if area_id:
        query = query.filter(OccupancyLog.area_id == area_id)
    elif area_code:
        query = query.filter(OccupancyLog.area_code == str(area_code))
    else:
        # No area identifier provided, can't track changes properly
        return None
    
    # Get the most recent log with a non-null occupation_status
    # ordered by event_time descending, then by id descending as fallback
    previous_log = query.filter(
        OccupancyLog.occupation_status.isnot(None),
        OccupancyLog.occupation_status != ""
    ).order_by(
        OccupancyLog.event_time.desc().nulls_last(),
        OccupancyLog.id.desc()
    ).first()
    
    # If there's a previous log and the status has changed, update its timespan
    if previous_log and previous_log.occupation_status != occupancy_status:
        # Calculate time difference
        if previous_log.event_time:
            time_diff = event_time - previous_log.event_time
            total_seconds = int(time_diff.total_seconds())
            
            # Update the previous log's timespan as integer (seconds)
            previous_log.timespan = total_seconds
            db.flush()
    
    # Calculate count based on the most recent log entry (regardless of area)
    # This represents the running total of occupied areas
    latest_log = db.query(OccupancyLog).order_by(
        OccupancyLog.event_time.desc().nulls_last(),
        OccupancyLog.id.desc()
    ).first()
    
    # Get the count from the latest log, or start at 0 if no logs exist
    current_count = latest_log.count if latest_log and latest_log.count is not None else 0
    
    # Update count based on occupancy status
    if occupancy_status == "Occupied":
        new_count = current_count + 1
    elif occupancy_status == "Unoccupied":
        new_count = max(0, current_count - 1)  # Ensure count doesn't go below 0
    else:
        new_count = current_count  # Keep same count for invalid status
    
    # Create new occupancy log entry
    new_log = OccupancyLog(
        processor_id=processor_id,
        area_id=area_id,
        area_code=str(area_code) if area_code else None,
        floor_id=floor_id,
        occupation_status=occupancy_status,
        event_date=event_date,
        event_time=event_time,
        time=event_time_only,
        count=new_count
    )
    
    db.add(new_log)
    db.flush()
    
    return new_log


def get_latest_occupancy_status(
    db: Session,
    processor_id: int,
    area_id: Optional[int] = None,
    area_code: Optional[str] = None
) -> Optional[str]:
    """
    Get the latest occupancy status for a given area.
    
    Args:
        db: Database session
        processor_id: ID of the processor
        area_id: ID of the area (optional)
        area_code: Code of the area (optional)
    
    Returns:
        The latest occupancy status, or None if not found
    """
    query = db.query(OccupancyLog).filter(
        OccupancyLog.processor_id == processor_id
    )
    
    if area_id:
        query = query.filter(OccupancyLog.area_id == area_id)
    elif area_code:
        query = query.filter(OccupancyLog.area_code == str(area_code))
    else:
        return None
    
    latest_log = query.order_by(
        OccupancyLog.event_time.desc().nulls_last(),
        OccupancyLog.id.desc()
    ).first()
    
    return latest_log.occupation_status if latest_log else None


def process_occupancy_from_area_status(
    db: Session,
    area_status: Dict[str, Any],
    processor_id: int,
    area: Optional[Area] = None,
    event_time: Optional[datetime] = None
) -> Optional[OccupancyLog]:
    """
    Process occupancy status from area status JSON message and log to occupancy_logs table.
    This function filters for occupancy-related information and stores it.
    
    Args:
        db: Database session
        area_status: Area status dictionary from listener JSON message
        processor_id: ID of the processor
        area: Area model instance (optional, will be looked up if not provided)
        event_time: Event timestamp (defaults to current time if not provided)
    
    Returns:
        The newly created OccupancyLog entry, or None if no valid occupancy status
    """
    # Extract occupancy status from the message
    occupancy_status = area_status.get("OccupancyStatus")
    
    # Only process valid occupancy statuses
    if not occupancy_status or occupancy_status not in ["Occupied", "Unoccupied"]:
        return None
    
    # Extract area information
    href = area_status.get("href")
    area_code = None
    
    if href:
        # Extract area code from href (e.g., "/area/123/status" -> "123")
        try:
            parts = href.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "area":
                area_code = parts[1]
        except (ValueError, IndexError):
            pass
    
    # Look up area if not provided
    area_id = None
    if not area and area_code:
        area = db.query(Area).filter(
            Area.code == str(area_code),
            Area.processor_id == processor_id
        ).first()
    
    if area:
        area_id = area.id
        if not area_code:
            area_code = area.code
    
    # Use current time if not provided
    if event_time is None:
        event_time = datetime.now()
    
    # Log the occupancy change
    return log_occupancy_change(
        db=db,
        processor_id=processor_id,
        area_id=area_id,
        area_code=area_code,
        occupancy_status=occupancy_status,
        event_time=event_time
    )


def sync_occupancy_logs_from_processor_events(
    db: Session,
    processor_id: Optional[int] = None,
    area_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    batch_size: int = 1000
) -> Dict[str, int]:
    """
    Fetches occupancy-related logs from processor_area_events table
    and stores them in occupancy_logs table.
    
    This function processes records that have a valid occupancy_status
    ("Occupied" or "Unoccupied") and logs them with proper timespan calculation.
    
    Args:
        db: Database session
        processor_id: Optional processor ID to filter by specific processor
        area_id: Optional area ID to filter by specific area
        start_date: Optional start date to filter events (defaults to all time)
        end_date: Optional end date to filter events (defaults to all time)
        batch_size: Number of records to process in each batch (default: 1000)
    
    Returns:
        Dictionary with counts of processed and logged records
    """
    # Build query for processor_area_events with occupancy status
    query = db.query(ProcessorAreaEvent).filter(
        ProcessorAreaEvent.occupancy_status.isnot(None),
        ProcessorAreaEvent.occupancy_status.in_(["Occupied", "Unoccupied"])
    )
    
    # Filter by processor_id if provided
    if processor_id:
        query = query.filter(ProcessorAreaEvent.processor_id == processor_id)
    
    # Filter by area_id if provided
    if area_id:
        query = query.filter(ProcessorAreaEvent.area_id == area_id)
    
    # Filter by date range if provided
    if start_date:
        query = query.filter(ProcessorAreaEvent.created_at >= start_date)
    if end_date:
        query = query.filter(ProcessorAreaEvent.created_at <= end_date)
    
    # Order by created_at and processor_id, area_id for chronological processing
    query = query.order_by(
        ProcessorAreaEvent.processor_id,
        ProcessorAreaEvent.area_id,
        ProcessorAreaEvent.created_at
    )
    
    # Get all matching records
    events = query.all()
    
    if not events:
        return {
            "processed": 0,
            "logged": 0,
            "skipped": 0,
            "errors": 0
        }
    
    logged_count = 0
    skipped_count = 0
    error_count = 0
    
    # Process events in batches
    for i in range(0, len(events), batch_size):
        batch = events[i:i + batch_size]
        
        try:
            # Group events by processor_id and area_id/area_code for proper timespan calculation
            grouped_events = {}
            
            for event in batch:
                # Create a key for grouping (processor_id + area_id or area_code)
                area_key = event.area_id if event.area_id else event.area_code
                group_key = (event.processor_id, area_key)
                
                if group_key not in grouped_events:
                    grouped_events[group_key] = []
                grouped_events[group_key].append(event)
            
            # Process each group chronologically
            for (proc_id, area_key), event_list in grouped_events.items():
                # Sort events by created_at within each group
                event_list.sort(key=lambda e: e.created_at or datetime.min)
                
                for event in event_list:
                    try:
                        # Extract area information
                        area_code = str(event.area_code) if event.area_code else None
                        area_id_val = event.area_id
                        
                        # Use created_at as event_time
                        event_time = event.created_at
                        if not event_time:
                            event_time = datetime.now()
                        
                        # Remove microseconds to store as HH:MM:SS format (no milliseconds)
                        event_time = event_time.replace(microsecond=0)
                        
                        # Check if this log already exists (to avoid duplicates)
                        existing_query = db.query(OccupancyLog).filter(
                            OccupancyLog.processor_id == proc_id,
                            OccupancyLog.event_time == event_time
                        )
                        
                        # Add area filter based on what's available
                        if area_id_val:
                            existing_query = existing_query.filter(OccupancyLog.area_id == area_id_val)
                        elif area_code:
                            existing_query = existing_query.filter(OccupancyLog.area_code == area_code)
                        
                        existing_log = existing_query.first()
                        
                        if existing_log:
                            skipped_count += 1
                            continue
                        
                        # Log the occupancy change using existing function
                        result = log_occupancy_change(
                            db=db,
                            processor_id=proc_id,
                            area_id=area_id_val,
                            area_code=area_code,
                            occupancy_status=event.occupancy_status,
                            event_time=event_time
                        )
                        
                        if result:
                            logged_count += 1
                        else:
                            skipped_count += 1
                    
                    except Exception as e:
                        error_count += 1
                        # Log error but continue processing
                        continue
            
            # Commit batch
            db.commit()
        
        except Exception as e:
            db.rollback()
            error_count += len(batch)
            continue
    
    return {
        "processed": len(events),
        "logged": logged_count,
        "skipped": skipped_count,
        "errors": error_count
    }


def sync_occupancy_logs_for_area(
    db: Session,
    area_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, int]:
    """
    Convenience function to sync occupancy logs for a specific area.
    
    Args:
        db: Database session
        area_id: Area ID to sync logs for
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        Dictionary with counts of processed and logged records
    """
    return sync_occupancy_logs_from_processor_events(
        db=db,
        area_id=area_id,
        start_date=start_date,
        end_date=end_date
    )


def sync_occupancy_logs_for_processor(
    db: Session,
    processor_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, int]:
    """
    Convenience function to sync occupancy logs for a specific processor.
    
    Args:
        db: Database session
        processor_id: Processor ID to sync logs for
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        Dictionary with counts of processed and logged records
    """
    return sync_occupancy_logs_from_processor_events(
        db=db,
        processor_id=processor_id,
        start_date=start_date,
        end_date=end_date
    )


def reconcile_occupancy_logs(db: Session) -> Dict[str, Any]:
    """
    Reconciliation function to compare occupancy logs with actual area occupancy status.
    If there are any mismatches, updates the logs and sets reconcile=True.
    
    This function:
    1. Gets all areas from the database
    2. For each area, gets the latest occupancy log entry
    3. Calls get_area_occupancy_status to get the current actual status
    4. Compares the log status with the actual status
    5. If mismatch found, updates the log entry and sets reconcile=True
    
    Args:
        db: Database session
    
    Returns:
        Dictionary with reconciliation statistics:
        - total_areas: Total number of areas processed
        - matched: Number of areas where status matched
        - mismatched: Number of areas where status mismatched and was updated
        - errors: Number of areas that encountered errors during reconciliation
        - skipped: Number of areas skipped (no log entry or no current status)
    """
    areas = db.query(Area).all()
    
    total_areas = len(areas)
    matched = 0
    mismatched = 0
    errors = 0
    skipped = 0
    
    current_time = datetime.now()
    
    for area in areas:
        try:
            # Get the latest occupancy log for this area
            latest_log = db.query(OccupancyLog).filter(
                OccupancyLog.area_id == area.id,
                OccupancyLog.occupation_status.isnot(None),
                OccupancyLog.occupation_status != ""
            ).order_by(
                OccupancyLog.event_time.desc().nulls_last(),
                OccupancyLog.id.desc()
            ).first()
            
            # If no log entry exists, skip this area
            if not latest_log:
                skipped += 1
                continue
            
            # Get current actual occupancy status from the processor
            status_result = get_area_occupancy_status(db, area.id)
            
            # If status retrieval failed, skip this area
            if status_result.get("status") != "success":
                skipped += 1
                continue
            
            actual_status = status_result.get("occupancy_status")
            
            # Skip if actual status is None or "Unknown"
            if not actual_status or actual_status == "Unknown":
                skipped += 1
                continue
            
            # Only process valid statuses ("Occupied" or "Unoccupied")
            if actual_status not in ["Occupied", "Unoccupied"]:
                skipped += 1
                continue
            
            # Compare log status with actual status
            log_status = latest_log.occupation_status
            
            if log_status == actual_status:
                # Status matches, no update needed
                matched += 1
            else:
                # Status mismatch found, update the log
                # Create a new log entry with the correct status
                updated_log = log_occupancy_change(
                    db=db,
                    processor_id=area.processor_id,
                    area_id=area.id,
                    area_code=area.code,
                    occupancy_status=actual_status,
                    event_time=current_time
                )
                
                # Mark the previous log entry as reconciled
                if latest_log:
                    latest_log.reconcile = True
                    db.flush()
                
                # Mark the new log entry as reconciled as well
                if updated_log:
                    updated_log.reconcile = True
                    db.flush()
                
                mismatched += 1
        
        except Exception as e:
            # Log error but continue processing other areas
            errors += 1
            continue
    
    # Commit all changes
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        errors += total_areas
    
    return {
        "total_areas": total_areas,
        "matched": matched,
        "mismatched": mismatched,
        "errors": errors,
        "skipped": skipped
    }


def fill_current_occupancy_status_for_all_areas(
    db: Session,
    area_ids: Optional[List[int]] = None,
    floor_ids: Optional[List[int]] = None,
    use_processor: bool = True
) -> Dict[str, Any]:
    """
    Fill the occupancy_logs table with the current occupancy status for all areas.
    
    This function:
    1. Gets all areas (optionally filtered by area_ids or floor_ids)
    2. For each area, retrieves the current occupancy status
       - If use_processor=True: Gets status from processor using get_area_occupancy_status
       - If use_processor=False: Gets status from latest log in occupancy_logs table
    3. Logs the status to occupancy_logs table if it's different from the latest log
    
    Args:
        db: Database session
        area_ids: Optional list of area IDs to filter by
        floor_ids: Optional list of floor IDs to filter by
        use_processor: If True, get status from processor; if False, use latest log status
    
    Returns:
        Dictionary with statistics:
        - total_areas: Total number of areas processed
        - logged: Number of areas with new logs created
        - skipped: Number of areas skipped (no status change or error)
        - errors: Number of areas that encountered errors
    """
    # Fetch areas
    query = db.query(Area)
    if floor_ids:
        query = query.filter(Area.floor_id.in_(floor_ids))
    if area_ids:
        query = query.filter(Area.id.in_(area_ids))
    areas = query.all()
    
    if not areas:
        return {
            "status": "success",
            "message": "No areas found",
            "total_areas": 0,
            "logged": 0,
            "skipped": 0,
            "errors": 0
        }
    
    total_areas = len(areas)
    logged_count = 0
    skipped_count = 0
    error_count = 0
    current_time = datetime.now().replace(microsecond=0)
    
    for area in areas:
        try:
            current_occupancy_status = None
            
            if use_processor:
                # Get current status from processor
                status_result = get_area_occupancy_status(db, area.id)
                if status_result.get("status") == "success":
                    current_occupancy_status = status_result.get("occupancy_status")
            else:
                # Get latest status from occupancy_logs table
                latest_status = get_latest_occupancy_status(
                    db=db,
                    processor_id=area.processor_id,
                    area_id=area.id,
                    area_code=area.code
                )
                current_occupancy_status = latest_status
            
            # Skip if status is None, "Unknown", or not valid
            if not current_occupancy_status or current_occupancy_status == "Unknown":
                skipped_count += 1
                continue
            
            # Only process valid statuses
            if current_occupancy_status not in ["Occupied", "Unoccupied"]:
                skipped_count += 1
                continue
            
            # Get the latest log for this area
            latest_log = db.query(OccupancyLog).filter(
                OccupancyLog.area_id == area.id,
                OccupancyLog.occupation_status.isnot(None),
                OccupancyLog.occupation_status != ""
            ).order_by(
                OccupancyLog.event_time.desc().nulls_last(),
                OccupancyLog.id.desc()
            ).first()
            
            # Check if status has changed or if no log exists
            if not latest_log or latest_log.occupation_status != current_occupancy_status:
                # Log the occupancy change
                result = log_occupancy_change(
                    db=db,
                    processor_id=area.processor_id,
                    area_id=area.id,
                    area_code=area.code,
                    occupancy_status=current_occupancy_status,
                    event_time=current_time
                )
                
                if result:
                    logged_count += 1
                else:
                    skipped_count += 1
            else:
                # Status hasn't changed, skip
                skipped_count += 1
        
        except Exception as e:
            error_count += 1
            continue
    
    # Commit all changes
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": f"Failed to commit changes: {str(e)}",
            "total_areas": total_areas,
            "logged": logged_count,
            "skipped": skipped_count,
            "errors": error_count
        }
    
    return {
        "status": "success",
        "message": f"Processed {total_areas} areas",
        "total_areas": total_areas,
        "logged": logged_count,
        "skipped": skipped_count,
        "errors": error_count
    }


def track_floor_occupancy_logs(db: Session, floor_id: int) -> Dict[str, Any]:
    """
    Track occupancy of areas for a floor.
    This function is called on floor creation and floor update.
    
    For each area on the floor:
    1. Get occupancy status using get_area_occupancy_status_by_floor
    2. Check if area_id exists in occupancy_logs
    3. If not present, read latest entry and insert new row
    4. If present, check if current status matches last known status
    5. If not matched, insert new row
    6. Increment count if occupied, else keep as is
    
    Args:
        db: Database session
        floor_id: ID of the floor to track
    
    Returns:
        Dictionary with tracking statistics
    """
    # Get all areas of the floor from DB
    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    
    if not areas:
        return {
            "status": "success",
            "message": "No areas found on this floor",
            "processed": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": 0
        }
    
    # Get occupancy status for all areas on the floor
    occupancy_result = get_area_occupancy_status_by_floor(db, floor_id)
    
    if occupancy_result.get("status") != "success":
        return {
            "status": "error",
            "message": occupancy_result.get("message", "Failed to get occupancy status"),
            "processed": 0,
            "inserted": 0,
            "skipped": 0,
            "errors": len(areas)
        }
    
    # Create a map of area_id to occupancy_status from the result
    occupancy_map = {}
    for area_data in occupancy_result.get("areas", []):
        area_id = area_data.get("id")
        occupancy_status = area_data.get("occupancy_status")
        if area_id:
            occupancy_map[area_id] = occupancy_status
    
    processed = 0
    inserted = 0
    skipped = 0
    errors = 0
    current_time = datetime.now().replace(microsecond=0)
    event_date = current_time.date()
    event_time_only = current_time.time()
    
    # Get the latest count from the most recent log entry (before processing)
    latest_log = db.query(OccupancyLog).order_by(
        OccupancyLog.event_time.desc().nulls_last(),
        OccupancyLog.id.desc()
    ).first()
    
    # Track running count as we process areas
    running_count = latest_log.count if latest_log and latest_log.count is not None else 0
    
    # Process each area
    for area in areas:
        try:
            area_id = area.id
            current_occupancy_status = occupancy_map.get(area_id)
            
            # Skip if occupancy status is None or not valid
            if current_occupancy_status not in ["Occupied", "Unoccupied"]:
                skipped += 1
                continue
            
            # Check if area_id exists in occupancy_logs
            existing_log = db.query(OccupancyLog).filter(
                OccupancyLog.area_id == area_id
            ).order_by(
                OccupancyLog.event_time.desc().nulls_last(),
                OccupancyLog.id.desc()
            ).first()
            
            if not existing_log:
                # Area not present in occupancy_logs
                # Read latest entry using get_area_occupancy_status_by_floor (already done above)
                # Insert new row for this area
                new_count = running_count
                if current_occupancy_status == "Occupied":
                    new_count = running_count + 1
                    running_count = new_count
                
                new_log = OccupancyLog(
                    processor_id=area.processor_id,
                    area_id=area_id,
                    area_code=area.code,
                    floor_id=floor_id,
                    occupation_status=current_occupancy_status,
                    event_date=event_date,
                    event_time=current_time,
                    time=event_time_only,
                    count=new_count
                )
                db.add(new_log)
                inserted += 1
            else:
                # Area present in occupancy_logs
                # Check if current occupancy status matches last known status
                last_known_status = existing_log.occupation_status
                
                if last_known_status != current_occupancy_status:
                    # Status changed, insert new row
                    # Calculate timespan for the previous log entry
                    if existing_log.event_time:
                        time_diff = current_time - existing_log.event_time
                        total_seconds = int(time_diff.total_seconds())
                        existing_log.timespan = total_seconds
                    
                    # Insert new row
                    new_count = running_count
                    if current_occupancy_status == "Occupied":
                        new_count = running_count + 1
                        running_count = new_count
                    
                    new_log = OccupancyLog(
                        processor_id=area.processor_id,
                        area_id=area_id,
                        area_code=area.code,
                        floor_id=floor_id,
                        occupation_status=current_occupancy_status,
                        event_date=event_date,
                        event_time=current_time,
                        time=event_time_only,
                        count=new_count
                    )
                    db.add(new_log)
                    inserted += 1
                else:
                    # Status matches, no need to insert
                    skipped += 1
            
            processed += 1
            
        except Exception as e:
            errors += 1
            print(f"Error processing area {area.id}: {e}")
            continue
    
    # Commit all changes
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": f"Failed to commit changes: {str(e)}",
            "processed": processed,
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors
        }
    
    return {
        "status": "success",
        "message": f"Processed {processed} areas",
        "processed": processed,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors
    }

