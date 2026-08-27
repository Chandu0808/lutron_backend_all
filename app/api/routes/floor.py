#E:\Gcon\lutron\Lutron_backend_app\app\api\routes\floors.py
import os
import shutil
import random
import json
from typing import List, Optional, Union

import natsort
from pydantic import BaseModel,ValidationError



from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query,Path,Body, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.dependencies.auth import get_current_user
from app.database.session import get_db, engine
from app.database.migrate_floor_sort_order import ensure_floor_sort_order_column
from app.models.area import Area
from app.models.coordinate import Coordinate
from app.models.floor import Floor
from app.models.processor import Processor
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.user_model import User
from app.schemas.floor import (
    FloorListOut,
    FloorListResponse,
    FloorSortSettingsUpdate,
    FloorSortSettingsResponse,
    FloorReorderRequest,
    FloorReorderResponse,
    ModifyCoordinatesRequest,
    ModifyCoordinatesResponse,
)
from app.crud.floor_sort import (
    get_manual_sort_enabled,
    set_manual_sort_enabled,
    sort_floor_dicts,
    reorder_floors,
)
from app.crud.floor import (
    create_floor, get_area_light_status_by_floor,
    get_area_occupancy_status_by_floor, get_area_energy_status_by_floor,
    modify_coordinates_in_db, generate_and_save_area_tree, update_floor_boundaries
)
from app.crud.occupancy_logs import track_floor_occupancy_logs
from app.crud.floor_proc_mapping import (
    create_floor_proc_mapping,
    ensure_floor_processor_mappings_from_areas,
)
from app.crud.area_tree import get_area_tree_by_floor
from app.utils.activity_logger import log_activity 
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.activity_report_logger import activity_report_log
from app.models.user_model import UserPermission
from app.models.zone import Zone
from app.utils.processor_trim import fetch_zone_trims_from_processor
from app.crud.zone_sync import sync_zones_for_floor
from app.utils.floor_plan_media import (
    floor_plan_client_url,
    floor_plan_file_response_parts,
)



router = APIRouter()

# Ensure upload directory exists
UPLOAD_DIR = os.path.join("app", "floor_plans")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/{floor_id}/plan")
def download_floor_plan(
    floor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream floor plan file for authenticated users with floor access.
    Replaces public StaticFiles /floor_plans/* so PDFs are not anonymously downloadable.
    """
    require_operator_permission_for_scope(
        required_level=1,
        floor_ids=[floor_id],
        enforce_on_empty_scope=True,
        db=db,
        current_user=current_user,
    )

    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    disk_path, media_type, filename = floor_plan_file_response_parts(floor.image_path)
    return FileResponse(
        disk_path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
    )


def _energy_logger_manual_enabled() -> bool:
    from app.installation_config import is_energy_logger_manual

    return is_energy_logger_manual()


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
    sort_order: Optional[int] = None

    class Config:
        from_attributes = True


def _processors_for_floor(db: Session, floor_id: int) -> List[dict]:
    ensure_floor_processor_mappings_from_areas(db, floor_id)
    mappings = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).all()
    processors = []
    for mapping in mappings:
        processor = db.query(Processor).filter(Processor.id == mapping.processor_id).first()
        if processor:
            areas = db.query(Area).filter(
                Area.processor_id == processor.id,
                Area.floor_id == floor_id,
            ).all()
            processors.append({
                "processor_id": processor.id,
                "server": processor.server or "",
                "areas": [{"area_id": a.id, "name": a.name} for a in areas],
            })
    return processors


def _floor_to_dict(floor: Floor, processors: List[dict]) -> dict:
    return {
        "id": floor.id,
        "floor_name": floor.name,
        "floor_image": floor_plan_client_url(floor.id) or "",
        "processors": processors,
        "sort_order": getattr(floor, "sort_order", None),
    }


def _build_accessible_floor_list(db: Session, current_user: User) -> List[dict]:
    floors = db.query(Floor).all()
    response = []

    for floor in floors:
        try:
            require_operator_permission_for_scope(
                required_level=1,
                floor_ids=[floor.id],
                enforce_on_empty_scope=True,
                db=db,
                current_user=current_user,
            )
        except HTTPException as e:
            if e.status_code == 403:
                continue
            raise

        response.append(_floor_to_dict(floor, _processors_for_floor(db, floor.id)))

    return response


def _floor_list_response(db: Session, current_user: User) -> dict:
    floors = _build_accessible_floor_list(db, current_user)
    # Persist any repaired floor↔processor mappings from the loop above
    db.commit()
    manual_sort_enabled = get_manual_sort_enabled(db)
    floors = sort_floor_dicts(floors, manual_sort_enabled)
    return {
        "manual_sort_enabled": manual_sort_enabled,
        "floors": floors,
    }


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
        processor_ids_to_map = set()
        areas_by_processor = {}

        for mapping in request.processors:
            processor_ids_to_map.add(int(mapping.processor_id))
            for aid in mapping.area_ids or []:
                area = db.query(Area).filter(Area.id == aid).first()
                if area:
                    area.floor_id = db_floor.id
                    updated_areas.append(area)
                    # Use the area's real processor_id (CSV source of truth)
                    actual_pid = int(area.processor_id) if area.processor_id is not None else int(mapping.processor_id)
                    processor_ids_to_map.add(actual_pid)
                    areas_by_processor.setdefault(actual_pid, []).append(area)

        for processor_id in sorted(processor_ids_to_map):
            create_floor_proc_mapping(db, floor_id=db_floor.id, processor_id=processor_id)

        ensure_floor_processor_mappings_from_areas(db, db_floor.id)

        processor_data = []
        for processor_id in sorted(processor_ids_to_map):
            proc = db.query(Processor).filter(Processor.id == processor_id).first()
            proc_areas = areas_by_processor.get(processor_id, [])
            # Include any areas already on this floor for this processor
            if not proc_areas:
                proc_areas = (
                    db.query(Area)
                    .filter(Area.floor_id == db_floor.id, Area.processor_id == processor_id)
                    .all()
                )
            processor_data.append({
                "processor_id": processor_id,
                "server": proc.server if proc else None,
                "areas": [{"area_id": a.id, "name": a.name} for a in proc_areas],
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
            "floor_image": floor_plan_client_url(db_floor.id),
            "processors": processor_data,
            "sort_order": db_floor.sort_order,
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
                            # Keep mapping for the area's real processor as well
                            if area.processor_id is not None:
                                create_floor_proc_mapping(
                                    db, floor_id=floor_id, processor_id=int(area.processor_id)
                                )

                    processor_data.append({
                        "processor_id": mapping.processor_id,
                        "server": proc.server if proc else None,
                        "areas": [{"area_id": a.id, "name": a.name} for a in proc_areas]
                    })

                ensure_floor_processor_mappings_from_areas(db, floor_id)

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
            "floor_image": floor_plan_client_url(floor.id),
            "processors": processor_data,
            "sort_order": floor.sort_order,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/list", response_model=FloorListResponse)
def list_floors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        return _floor_list_response(db, current_user)
    except ProgrammingError:
        db.rollback()
        ensure_floor_sort_order_column(engine)
        return _floor_list_response(db, current_user)


@router.get("/sort-settings", response_model=FloorSortSettingsResponse)
def get_floor_sort_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )
    payload = _floor_list_response(db, current_user)
    return {
        "manual_sort_enabled": payload["manual_sort_enabled"],
        "floors": payload["floors"],
    }


@router.put("/sort-settings", response_model=FloorSortSettingsResponse)
def update_floor_sort_settings(
    payload: FloorSortSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )
    set_manual_sort_enabled(
        db,
        payload.manual_sort_enabled,
        updated_by=current_user.id,
    )
    log_activity(
        db=db,
        user_id=current_user.id,
        floor_id=None,
        activity_type="GUI Triggered",
        activity_description=(
            f"Floor sorting set to {'manual' if payload.manual_sort_enabled else 'auto'} "
            f"by user {current_user.id}|{current_user.name}."
        ),
    )
    result = _floor_list_response(db, current_user)
    return {
        "manual_sort_enabled": result["manual_sort_enabled"],
        "floors": result["floors"],
    }


@router.put("/reorder", response_model=FloorReorderResponse)
def reorder_floor_list(
    payload: FloorReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_operator_permission_for_scope(
        required_level=5,
        db=db,
        current_user=current_user,
    )
    if not payload.floor_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="floor_ids must not be empty",
        )
    try:
        reorder_floors(db, payload.floor_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    log_activity(
        db=db,
        user_id=current_user.id,
        floor_id=None,
        activity_type="GUI Triggered",
        activity_description=(
            f"Floor order updated by user {current_user.id}|{current_user.name}."
        ),
    )
    result = _floor_list_response(db, current_user)
    return {
        "status": "success",
        "manual_sort_enabled": result["manual_sort_enabled"],
        "floors": result["floors"],
    }


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
    # Repair: include processors that own areas on this floor even if mapping was missing
    ensure_floor_processor_mappings_from_areas(db, floor_id)
    db.commit()
    mappings = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).all()
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
        "floor_image": floor_plan_client_url(floor.id),
        "processors": processor_data,
        "sort_order": floor.sort_order,
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
    live: int = Query(1, ge=0, le=1, description="1=LEAP then cache; 0=listener DB cache only"),
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
    result = get_area_occupancy_status_by_floor(db, floor_id, live=bool(live))
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "Unknown error"))
    return result



@router.get("/light_status")
def light_status(
    floor_id: int,
    live: int = Query(1, ge=0, le=1, description="1=LEAP then cache; 0=listener DB cache only"),
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

    result = get_area_light_status_by_floor(db, floor_id, live=bool(live))
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

