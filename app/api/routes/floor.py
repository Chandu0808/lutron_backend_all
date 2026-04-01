#E:\Gcon\lutron\Lutron_backend_app\app\api\routes\floors.py
import os
import shutil
import random
import json
from typing import List, Optional, Union

import natsort
from pydantic import BaseModel,ValidationError



from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query,Path,Body
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.database.session import get_db
from app.models.area import Area
from app.models.coordinate import Coordinate
from app.models.floor import Floor
from app.models.processor import Processor
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.user_model import User
from app.schemas.floor import FloorListOut,ModifyCoordinatesRequest, ModifyCoordinatesResponse
from app.crud.floor import (
    create_floor, get_area_light_status_by_floor,
    get_area_occupancy_status_by_floor, get_area_energy_status_by_floor,
    modify_coordinates_in_db, generate_and_save_area_tree, update_floor_boundaries
)
from app.crud.occupancy_logs import track_floor_occupancy_logs
from app.crud.floor_proc_mapping import create_floor_proc_mapping
from app.crud.area_tree import get_area_tree_by_floor
from app.utils.activity_logger import log_activity 
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.activity_report_logger import activity_report_log
from app.models.user_model import UserPermission
from app.models.zone import Zone
from app.utils.processor_trim import fetch_zone_trims_from_processor
from app.crud.zone_sync import sync_zones_for_floor



router = APIRouter()

# Ensure upload directory exists
UPLOAD_DIR = os.path.join("app", "floor_plans")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _energy_logger_manual_enabled() -> bool:
    value = (os.getenv("energy_logger_manual") or os.getenv("energy_logger_mannual") or "").strip().lower()
    return value in ("true", "1", "yes")


@router.post("/{floor_id}/sync-zones")
def sync_zones_endpoint(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_operator_permission_for_scope(
        required_level=2,
        floor_ids=[floor_id],
        enforce_on_empty_scope=True,
        db=db,
        current_user=current_user,
    )

    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    return sync_zones_for_floor(db, floor_id)


def _sync_zone_trims_for_processor(db: Session, processor_id: int) -> None:
    """
    Best-effort sync for a processor. Does not raise by design.
    Updates loadcontroller_code for every zone with an AssociatedZone; updates trim columns only for dimmed.
    """
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        return

    trim_map, loadcontroller_map, _errors = fetch_zone_trims_from_processor(processor=processor)
    if not trim_map and not loadcontroller_map:
        return

    def _resolve_zone(zone_code_str: str):
        zone = (
            db.query(Zone)
            .join(Area, Zone.area_id == Area.id)
            .filter(Area.processor_id == processor_id, Zone.code == str(zone_code_str))
            .first()
        )
        if not zone:
            try:
                zone_id_value = int(zone_code_str)
            except (TypeError, ValueError):
                zone_id_value = None
            if zone_id_value is not None:
                zone = (
                    db.query(Zone)
                    .join(Area, Zone.area_id == Area.id)
                    .filter(Area.processor_id == processor_id, Zone.id == zone_id_value)
                    .first()
                )
        return zone

    for zone_code, lc_id in loadcontroller_map.items():
        zone = _resolve_zone(str(zone_code))
        if zone is not None:
            zone.loadcontroller_code = lc_id

    for zone_code, trim_values in trim_map.items():
        zone = _resolve_zone(str(zone_code))
        if not zone or not isinstance(trim_values, dict):
            continue

        if trim_values.get("high_end_trim") is not None:
            zone.high_end_trim = trim_values["high_end_trim"]
        if trim_values.get("energy_trim") is not None:
            zone.energy_trim = trim_values["energy_trim"]
        if trim_values.get("low_end_trim") is not None:
            zone.low_end_trim = trim_values["low_end_trim"]

class ProcessorAreaMapping(BaseModel):
    processor_id: Optional[int]
    area_ids: Optional[List[int]]

class FloorCreateRequest(BaseModel):
    floor_name: str
    processors: List[ProcessorAreaMapping]

class FloorUpdateRequest(BaseModel):
    floor_name: Optional[str]
    processors: Optional[List[ProcessorAreaMapping]]

class FloorListOut(BaseModel):
    id: int
    floor_name: str
    floor_image: Optional[str]
    processors: List[dict]

    class Config:
        from_attributes = True


@router.post("/create", response_model=FloorListOut)
async def upload_floor(
    json_data: str = Form(...),
    floor_plan: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- Superadmin-only Permission Check ----------
    require_operator_permission_for_scope(
        required_level=5,   # only Superadmin can create floors
        db=db,
        current_user=user
    )

    try:
        request = FloorCreateRequest.parse_raw(json_data)

        # Save uploaded file
        ext = floor_plan.filename.split('.')[-1]
        filename = f"{request.floor_name}_{random.randint(100, 999)}.{ext}"
        file_path = os.path.join(UPLOAD_DIR, filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(floor_plan.file, buffer)

        image_path = f"/floor_plans/{filename}"
        db_floor = create_floor(db, name=request.floor_name, image_path=image_path)

        updated_areas = []
        processor_data = []

        for mapping in request.processors:
            create_floor_proc_mapping(db, floor_id=db_floor.id, processor_id=mapping.processor_id)
            proc = db.query(Processor).filter(Processor.id == mapping.processor_id).first()

            proc_areas = []
            for aid in mapping.area_ids:
                area = db.query(Area).filter(Area.id == aid).first()
                if area:
                    area.floor_id = db_floor.id
                    updated_areas.append(area)
                    proc_areas.append(area)

            processor_data.append({
                "processor_id": mapping.processor_id,
                "server": proc.server if proc else None,
                "areas": [{"area_id": a.id, "name": a.name} for a in proc_areas]
            })

        # Log floor creation (existing)
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=db_floor.id,
            activity_type="GUI Triggered",
            activity_description=f"{db_floor.name} created by user {user.id}|{user.name}."
        )

        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=None,                   # multiple areas → keep NULL
            activity_type="Floor",
            activity_description=f"Floor {db_floor.name} created by {user.name}.",
            area_name=db_floor.name         # <-- put floor name into area_name column
        )

        db.commit()

        if not _energy_logger_manual_enabled():
            processor_ids = sorted({
                int(m.processor_id) for m in request.processors
                if getattr(m, "processor_id", None) is not None
            })
            for processor_id in processor_ids:
                try:
                    _sync_zone_trims_for_processor(db=db, processor_id=processor_id)
                except Exception:
                    # Keep floor create behavior unchanged if trim sync fails.
                    pass
            db.commit()

        # Log each updated area (existing only, no new activity_report_log here)
        for area in updated_areas:
            log_activity(
                db=db,
                user_id=user.id,
                area_id=area.id,
                activity_type="GUI Triggered",
                activity_description=f"Area {area.name} assigned to floor {db_floor.name} by user {user.id}|{user.name}."
            )

        # Generate and save area_tree
        generate_and_save_area_tree(db, db_floor.id)
        
        # Update floor boundaries after areas are assigned
        update_floor_boundaries(db, db_floor.id)
        
        # Track occupancy logs for areas on this floor
        track_floor_occupancy_logs(db, db_floor.id)

        return {
            "id": db_floor.id,
            "floor_name": db_floor.name,
            "floor_image": image_path,
            "processors": processor_data
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))





@router.put("/update/{floor_id}", response_model=FloorListOut)
async def update_floor(
    floor_id: int = Path(..., description="ID of the floor to update"),
    floor_name: Optional[str] = Form(None, description="New name of the floor (optional)"),
    processors: Optional[str] = Form(None, description="JSON list of processor-area mappings"),
    floor_plan: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- Superadmin-only Permission Check ----------
    require_operator_permission_for_scope(
        required_level=5,   # only Superadmin can update floors
        db=db,
        current_user=user
    )

    try:
        floor = db.query(Floor).filter(Floor.id == floor_id).first()
        if not floor:
            raise HTTPException(status_code=404, detail="Floor not found")

        if floor_name:
            floor.name = floor_name

        # Upload floor plan if provided
        if floor_plan and getattr(floor_plan, "filename", None):
            # Delete old file if exists
            if floor.image_path:
                old_path = os.path.join("app", floor.image_path.lstrip("/"))
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

            # Save new file
            ext = floor_plan.filename.split(".")[-1]
            new_filename = f"{floor.name}_{random.randint(100, 999)}.{ext}"
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            file_location = os.path.join(UPLOAD_DIR, new_filename)
            with open(file_location, "wb") as buffer:
                shutil.copyfileobj(floor_plan.file, buffer)

            floor.image_path = f"/floor_plans/{new_filename}"

        updated_areas = []
        processor_data = []

        # Parse and apply processor mappings
        if processors:
            try:
                parsed_processors = json.loads(processors)
                validated_processors = [ProcessorAreaMapping(**p) for p in parsed_processors]

                db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).delete()

                for mapping in validated_processors:
                    if not mapping.processor_id and not mapping.area_ids:
                        continue

                    proc = db.query(Processor).filter(Processor.id == mapping.processor_id).first() if mapping.processor_id else None

                    if mapping.processor_id:
                        create_floor_proc_mapping(db, floor_id=floor_id, processor_id=mapping.processor_id)

                    proc_areas = []
                    for aid in mapping.area_ids or []:
                        area = db.query(Area).filter(Area.id == aid).first()
                        if area:
                            area.floor_id = floor_id
                            updated_areas.append(area)
                            proc_areas.append(area)

                    processor_data.append({
                        "processor_id": mapping.processor_id,
                        "server": proc.server if proc else None,
                        "areas": [{"area_id": a.id, "name": a.name} for a in proc_areas]
                    })

            except (json.JSONDecodeError, ValidationError) as e:
                raise HTTPException(status_code=400, detail=f"Invalid 'processors' input: {str(e)}")

        db.commit()

        # Best-effort: refresh area names + zones for this floor
        # Keep floor update behavior unchanged if sync fails.
        try:
            sync_zones_for_floor(db, floor_id)
        except Exception:
            pass

        if not _energy_logger_manual_enabled():
            processor_ids = set()
            if processors:
                for mapping in validated_processors:
                    if getattr(mapping, "processor_id", None) is not None:
                        processor_ids.add(int(mapping.processor_id))
            else:
                mapped = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).all()
                for mapping in mapped:
                    if getattr(mapping, "processor_id", None) is not None:
                        processor_ids.add(int(mapping.processor_id))
            for processor_id in sorted(processor_ids):
                try:
                    _sync_zone_trims_for_processor(db=db, processor_id=processor_id)
                except Exception:
                    # Keep floor update behavior unchanged if trim sync fails.
                    pass
            db.commit()

        # Existing log
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=floor.id,
            activity_type="GUI Triggered",
            activity_description=f"Floor updated: ID={floor.id}, Name={floor.name}"
        )

        # New activity_report_log
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=None,                   # multiple areas → keep NULL
            activity_type="Floor",
            activity_description=f"Floor {floor.name} updated by {user.name}.",
            area_name=floor.name            # <-- floor name stored in area_name column
        )

        # Regenerate and save area_tree
        generate_and_save_area_tree(db, floor.id)
        
        # Update floor boundaries after areas are updated
        update_floor_boundaries(db, floor.id)
        
        # Track occupancy logs for areas on this floor
        track_floor_occupancy_logs(db, floor.id)

        return {
            "id": floor.id,
            "floor_name": floor.name,
            "floor_image": floor.image_path,
            "processors": processor_data
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/list", response_model=List[FloorListOut])
def list_floors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    floors = db.query(Floor).all()
    response = []

    for floor in floors:
        try:
            #  Permission check — Operators must have at least "monitor" access for this floor
            require_operator_permission_for_scope(
                required_level=1,                # 1 = view/monitor
                floor_ids=[floor.id],            # Check permission for this specific floor
                enforce_on_empty_scope=True,     # Don’t allow empty scope
                db=db,
                current_user=current_user
            )
        except HTTPException as e:
            if e.status_code == 403:  
                # Skip floors without access instead of raising error
                continue
            raise   # re-raise other unexpected errors

        # Build processor data
        mappings = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor.id).all()
        processors = []
        for mapping in mappings:
            processor = db.query(Processor).filter(Processor.id == mapping.processor_id).first()
            if processor:
                areas = db.query(Area).filter(
                    Area.processor_id == processor.id,
                    Area.floor_id == floor.id
                ).all()
                processors.append({
                    "processor_id": processor.id,
                    "server": processor.server,
                    "areas": [{"area_id": a.id, "name": a.name} for a in areas]
                })

        response.append({
            "id": floor.id,
            "floor_name": floor.name,
            "floor_image": floor.image_path,
            "processors": processors
        })

    response = natsort.natsorted(response, key=lambda x: x.get("floor_name") or "")
    return response



@router.get("/get/{floor_id}", response_model=FloorListOut)
def get_floor_by_id(
    floor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    mappings = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).all()
    processor_data = []
    for mapping in mappings:
        processor = db.query(Processor).filter(Processor.id == mapping.processor_id).first()
        if processor:
            areas = db.query(Area).filter(Area.processor_id == processor.id, Area.floor_id == floor_id).all()
            processor_data.append({
                "processor_id": processor.id,
                "server": processor.server,
                "areas": [{"area_id": a.id, "name": a.name} for a in areas]
            })

    return {
        "id": floor.id,
        "floor_name": floor.name,
        "floor_image": floor.image_path,
        "processors": processor_data
    }


@router.delete("/delete/{floor_id}")
def delete_floor(
    floor_id: int = Path(..., description="ID of the floor to delete"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Delete a floor by ID.
    - Deletes the floor plan image file
    - Removes user permissions for the floor
    - Removes floor-processor mappings
    - Unassigns areas from the floor
    - Deletes the floor record
    Only Superadmin (level 5) can delete floors.
    """
    # ---------- Superadmin-only Permission Check ----------
    require_operator_permission_for_scope(
        required_level=5,   # only Superadmin can delete floors
        db=db,
        current_user=user
    )

    try:
        # Find the floor
        floor = db.query(Floor).filter(Floor.id == floor_id).first()
        if not floor:
            raise HTTPException(status_code=404, detail="Floor not found")

        floor_name = floor.name
        image_path = floor.image_path

        # Log activity before deletion (before any changes)
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=floor_id,
            activity_type="GUI Triggered",
            activity_description=f"Floor {floor_name} (ID: {floor_id}) deleted by user {user.id}|{user.name}."
        )

        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=None,
            activity_type="Floor",
            activity_description=f"Floor {floor_name} deleted by {user.name}.",
            area_name=floor_name
        )

        # Unassign areas from this floor (set floor_id to NULL)
        db.query(Area).filter(Area.floor_id == floor_id).update({"floor_id": None})

        # Delete user permissions for this floor
        db.query(UserPermission).filter(UserPermission.floor_id == floor_id).delete()

        # Delete floor-processor mappings
        db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).delete()

        # Delete the floor
        db.delete(floor)
        
        # Commit all changes
        db.commit()

        # Delete floor plan image file if exists (after successful DB delete)
        if image_path:
            file_path = os.path.join("app", image_path.lstrip("/"))
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"Error deleting floor plan file: {e}")

        return {
            "status": "success",
            "message": f"Floor '{floor_name}' (ID: {floor_id}) deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete floor: {str(e)}")


@router.get("/occupancy_status")
def occupancy_status(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # Permission check — Operators must have at least "monitor" access for this floor
        require_operator_permission_for_scope(
            required_level=1,                # 1 = view/monitor
            floor_ids=[floor_id],
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            # User-friendly: skip unauthorized instead of raising error
            return {
                "status": "failed",
                "message": f"Not authorized to view occupancy for floor {floor_id}"
            }
        raise   # re-raise any other error (422, 500, etc.)

    #  Get occupancy status for this floor
    result = get_area_occupancy_status_by_floor(db, floor_id)
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Unknown error"))
    return result



@router.get("/light_status")
def light_status(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Operators must have at least "monitor" access for this floor
    require_operator_permission_for_scope(
        required_level=1,             # 1 = view/monitor
        floor_ids=[floor_id],         # check this specific floor
        enforce_on_empty_scope=True,  # no empty scope
        db=db,
        current_user=current_user,
    )

    result = get_area_light_status_by_floor(db, floor_id)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
    return result

@router.get("/energy_status")
def energy_status(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        #  Permission check — Operators must have at least "monitor" access for this floor
        require_operator_permission_for_scope(
            required_level=1,                # view/monitor
            floor_ids=[floor_id],
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            # User-friendly: skip unauthorized instead of hard fail
            return {
                "status": "failed",
                "message": f"Not authorized to view energy status for floor {floor_id}"
            }
        raise   # re-raise unexpected errors

    # Fetch energy status
    result = get_area_energy_status_by_floor(db, floor_id)
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Unknown error"))
    return result

@router.get("/area_tree/{floor_id}")
def get_area_tree(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    API endpoint to retrieve the full area tree for a given floor_id from database.
    Fetches cached area_tree from floors.area_tree column.
    Includes both area_code (from LEAP) and area_id (from DB).
    Operators must have at least monitor (level 1) access to this floor.
    """

    # ---------- Permission Check ----------
    try:
        require_operator_permission_for_scope(
            required_level=1,                # 1 = view/monitor
            floor_ids=[floor_id],
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": f"Not authorized to view area tree for floor {floor_id}"
            }
        raise   # re-raise other errors (422, 500)

    # ---------- Core Logic ----------
    try:
        # Fetch floor with area_tree from database
        floor = db.query(Floor).filter(Floor.id == floor_id).first()
        
        if not floor:
            raise HTTPException(status_code=404, detail="Floor not found")
        
        # Return cached area_tree from database
        tree = floor.area_tree if floor.area_tree else []
        
        return {
            "status": "success",
            "floor_id": floor_id,
            "tree": tree
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve area tree: {str(e)}"
        )
    


@router.post("/modify_coordinates", response_model=ModifyCoordinatesResponse)
def modify_coordinates(
    payload: ModifyCoordinatesRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Operators must have at least "control" access for this floor
    require_operator_permission_for_scope(
        required_level=2,             # 2 = control
        floor_ids=[payload.floor_id],  # Assumes floor_id is part of the payload
        enforce_on_empty_scope=True,
        db=db,
        current_user=current_user
    )

    return modify_coordinates_in_db(db, payload)

