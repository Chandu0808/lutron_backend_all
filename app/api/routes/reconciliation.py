from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.models.area import Area
from app.models.occupancy_logs import OccupancyLog
from app.crud.occupancy_logs import reconcile_occupancy_logs
from app.scheduler import reconciliation_lock

router = APIRouter()


@router.post("/reconciliation/trigger")
def trigger_reconciliation(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Trigger the reconciliation function instantly and return all areas with reconcile values.
    
    This endpoint:
    1. Runs the reconciliation function to compare occupancy logs with actual area occupancy status
    2. Returns all areas with their latest occupancy log entries including reconcile values
    
    Returns:
        Dictionary containing:
        - reconciliation_stats: Statistics from the reconciliation process
        - areas_with_reconcile: List of areas with their reconcile values from occupancy_logs
    """
    # Track if we acquired the lock
    lock_acquired = False
    
    try:
        # Acquire lock to prevent concurrent execution with scheduled job
        if not reconciliation_lock.acquire(blocking=False):
            # If reconciliation is already running, return current state without triggering
            areas = db.query(Area).all()
            areas_with_reconcile = []
            
            for area in areas:
                latest_log = db.query(OccupancyLog).filter(
                    OccupancyLog.area_id == area.id,
                    OccupancyLog.occupation_status.isnot(None),
                    OccupancyLog.occupation_status != ""
                ).order_by(
                    OccupancyLog.event_time.desc().nulls_last(),
                    OccupancyLog.id.desc()
                ).first()
                
                if latest_log:
                    areas_with_reconcile.append({
                        "area_id": area.id,
                        "area_code": area.code,
                        "area_name": area.name,
                        "processor_id": area.processor_id,
                        "floor_id": area.floor_id,
                        "occupation_status": latest_log.occupation_status,
                        "event_time": latest_log.event_time.isoformat() if latest_log.event_time else None,
                        "reconcile": latest_log.reconcile,
                        "timespan": latest_log.timespan,
                        "count": latest_log.count
                    })
                else:
                    areas_with_reconcile.append({
                        "area_id": area.id,
                        "area_code": area.code,
                        "area_name": area.name,
                        "processor_id": area.processor_id,
                        "floor_id": area.floor_id,
                        "occupation_status": None,
                        "event_time": None,
                        "reconcile": None,
                        "timespan": None,
                        "count": None
                    })
            
            return {
                "status": "info",
                "message": "Reconciliation already in progress. Returning current state without triggering new reconciliation.",
                "reconciliation_stats": None,
                "areas_with_reconcile": areas_with_reconcile,
                "total_areas": len(areas_with_reconcile)
            }
        
        # Lock acquired successfully
        lock_acquired = True
        
        try:
            # Run the reconciliation function
            reconciliation_result = reconcile_occupancy_logs(db)
            
            # Query all areas with their latest occupancy log entries
            areas = db.query(Area).all()
            areas_with_reconcile = []
            
            for area in areas:
                # Get the latest occupancy log for this area
                latest_log = db.query(OccupancyLog).filter(
                    OccupancyLog.area_id == area.id,
                    OccupancyLog.occupation_status.isnot(None),
                    OccupancyLog.occupation_status != ""
                ).order_by(
                    OccupancyLog.event_time.desc().nulls_last(),
                    OccupancyLog.id.desc()
                ).first()
                
                if latest_log:
                    areas_with_reconcile.append({
                        "area_id": area.id,
                        "area_code": area.code,
                        "area_name": area.name,
                        "processor_id": area.processor_id,
                        "floor_id": area.floor_id,
                        "occupation_status": latest_log.occupation_status,
                        "event_time": latest_log.event_time.isoformat() if latest_log.event_time else None,
                        "reconcile": latest_log.reconcile,
                        "timespan": latest_log.timespan,
                        "count": latest_log.count
                    })
                else:
                    # Include areas without logs but with None reconcile value
                    areas_with_reconcile.append({
                        "area_id": area.id,
                        "area_code": area.code,
                        "area_name": area.name,
                        "processor_id": area.processor_id,
                        "floor_id": area.floor_id,
                        "occupation_status": None,
                        "event_time": None,
                        "reconcile": None,
                        "timespan": None,
                        "count": None
                    })
            
            return {
                "status": "success",
                "reconciliation_stats": reconciliation_result,
                "areas_with_reconcile": areas_with_reconcile,
                "total_areas": len(areas_with_reconcile)
            }
        finally:
            # Release lock when done (only if we acquired it)
            if lock_acquired:
                reconciliation_lock.release()
    
    except Exception as e:
        # Ensure lock is released even on error (only if we acquired it)
        if lock_acquired:
            try:
                reconciliation_lock.release()
            except:
                pass
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger reconciliation: {str(e)}"
        )

