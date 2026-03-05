from fastapi import APIRouter, Depends, HTTPException, Query, Body,Request
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.area import Area
from app.models.user_model import User
from app.models.processor import Processor
from pydantic import BaseModel
from app.models.coordinate import Coordinate
from math import sqrt
from app.schemas.area import ReferenceLengthInput
from fastapi.responses import JSONResponse,StreamingResponse
from app.crud.area import update_area_sizes_from_reference
from app.crud.area_size_and_load import get_size_and_load_tree_all_floors
from app.utils.activity_logger import log_activity
from app.crud.area_csv import generate_area_csv
from app.schemas.area_csv import AreaCSVRequest
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.utils.lutron_helpers import is_processor_reachable
from app.crud.area import (
    get_area_scene_summary_by_area_id,
    get_area_zones_with_status,
    get_area_light_status,
    get_area_occupancy_status,
    activate_scene_for_area,
    get_shade_zones_by_area,
    get_area_energy_status,
    sync_area_names_for_floor,
)
from app.utils.activity_report_logger import activity_report_log
import json


router = APIRouter()


@router.get("/full_area_status")
def get_scene_zone_status_summary(
    area_id: int = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found in DB")

    scene_result = get_area_scene_summary_by_area_id(db, area_id)
    if scene_result["status"] != "success":
        raise HTTPException(status_code=404, detail=f"Scene Error: {scene_result['message']}")

    zone_result = get_area_zones_with_status(db, area_id)
    if zone_result["status"] != "success":
        raise HTTPException(status_code=404, detail=f"Zone Error: {zone_result['message']}")

    light_result = get_area_light_status(db, area_id)
    if light_result["status"] != "success":
        raise HTTPException(status_code=404, detail=f"Light Error: {light_result['message']}")

    occupancy_result = get_area_occupancy_status(db, area_id)
    if occupancy_result["status"] != "success":
        raise HTTPException(status_code=404, detail=f"Occupancy Error: {occupancy_result['message']}")

    energy_result = get_area_energy_status(db, area_id)
    if energy_result["status"] != "success":
        consumption = "Unknown"
        savings = "Unknown"
    else:
        c = energy_result["consumption"]
        s = energy_result["savings"]
        consumption = round(c, 2) if isinstance(c, (int, float)) else c
        savings = round(s, 2) if isinstance(s, (int, float)) else s

    return {
        "status": "success",
        "floor_id": area.floor_id,
        "area_id": area.id,
        "area_name": area.name,
        "area_code": area.code,
        "light_status": light_result["light_status"],
        "occupancy_status": occupancy_result["occupancy_status"],
        "active_scene": scene_result["active_scene"],
        "area_scenes": scene_result["area_scenes"],
        "zones": zone_result["zones"],
        "consumption": consumption,
        "savings": savings
    }


class SyncAreaNamesByFloorInput(BaseModel):
    floor_id: int
    processor_id: int


@router.post("/sync-names-by-floor")
def sync_area_names_by_floor(
    payload: SyncAreaNamesByFloorInput = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sync area names from Lutron for all areas on the given floor and processor."""
    require_operator_permission_for_scope(
        required_level=3,
        floor_ids=[payload.floor_id],
        db=db,
        current_user=user,
    )
    result = sync_area_names_for_floor(db, payload.floor_id, payload.processor_id)
    if result.get("status") == "error" and result.get("message") in (
        "Floor not found",
        "Processor not found",
    ):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


class SceneActivationInput(BaseModel):
    area_id: int
    scene_code: int

def get_scene_name_safe(processor: Processor, scene_code: int) -> str:
    """
    Try to fetch scene name from processor using LEAP.
    Falls back to 'Scene {code}' if unavailable.
    """
    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        request = {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/areascene/{scene_code}"}
        }
        send_json(ssock, request)
        response = recv_json(ssock)
        ssock.close()

        area_scene = response.get("Body", {}).get("AreaScene", {})
        if "Name" in area_scene:
            return area_scene["Name"]
        return f"Scene {scene_code}"
    except Exception:
        return f"Scene {scene_code}"


@router.post("/scene_activate")
def activate_scene(
    payload: SceneActivationInput = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Activates a scene for a given area and records user-triggered log.
    - Admin/Superadmin: always allowed
    - Operator: must have at least monitor_control (2) on the area's floor
    """
    # 1) Resolve area
    area = db.query(Area).filter(Area.id == payload.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    # 2) Permission check
    try:
        require_operator_permission_for_scope(
            required_level=2,
            floor_ids=[area.floor_id],
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {
                "status": "failed",
                "message": f"Not authorized to activate scenes in floor {area.floor_id}"
            }
        raise

    # 3) Fetch processor for scene name lookup
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    scene_name = get_scene_name_safe(processor, payload.scene_code)

    # 4) Perform the action
    try:
        result = activate_scene_for_area(payload.area_id, payload.scene_code, db)

        # 5) Log with area + scene name
        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=area.id,
            activity_type="User",
            sub_activity_type="AreaSceneChanged",
            activity_description=f"Scene '{scene_name}' activated"
        )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



class AreaIdRequest(BaseModel):
    area_id: int

@router.post("/scene_list")
def get_area_list(payload: AreaIdRequest, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    return get_area_scene_summary_by_area_id(db, payload.area_id)

class AreaRequest(BaseModel):
    area_id: int

# POST API to get zone status of an area
@router.post("/zone_status")
def get_area_zone_status(input: AreaRequest, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    try:
        result = get_area_zones_with_status(db, input.area_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching zone status: {str(e)}")

@router.get("/shade_groups")
def shade_groups(area_id: int, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    """
    Get only shade-type zones for a given area_id.
    """
    try:
        return get_shade_zones_by_area(db, area_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    






def calculate_polygon_area(coords):
    # Shoelace formula to calculate area of polygon
    n = len(coords)
    if n < 3:
        return 0  # Not a polygon

    area = 0.0
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        area += (x1 * y2) - (x2 * y1)
    return abs(area) / 2.0







@router.get("/coordinates/{area_id}", response_class=JSONResponse)
def get_coordinates_by_area_id(
    area_id: int,
    db: Session = Depends(get_db),user: User = Depends(get_current_user)
):
    from app.crud.floor import area_coordinates_to_rings

    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return JSONResponse(
            content={"status": "error", "message": "Area not found."},
            status_code=404
        )

    rings = area_coordinates_to_rings(area.coordinates)
    coord_objects = [[{"x": int(p["x"]), "y": int(p["y"])} for p in ring] for ring in rings]

    return JSONResponse(content={
        "area_id": area.id,
        "area_name": area.name,
        "coordinates": coord_objects
    })











@router.post("/reference_length", response_class=JSONResponse)
def set_reference_length(
    data: ReferenceLengthInput,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    result = update_area_sizes_from_reference(
        db=db,
        first_point=data.first_point,
        second_point=data.second_point,
        floor_id=data.floor_id,
        length_in_meters=data.length_in_meters,
        length_in_feet=data.length_in_feet,
    )

    if result["status"] != "success":
        return JSONResponse(content=result, status_code=400)

    return JSONResponse(content=result)





@router.get("/size_and_load")
def size_and_load_all_floors(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Returns size and load metrics for all floors.
    - Admin/Superadmin: full access
    - Operator: only floors with at least edit-level (1) permission
    """

    result = get_size_and_load_tree_all_floors(db)

    # Admin/Superadmin → return everything
    if user.role in ["Admin", "Superadmin"]:
        return result

    # Operator → filter floors based on edit-level access
    permitted_floors = []
    for floor_data in result["floors"]:
        floor_id = floor_data.get("floor_id")
        if not floor_id:
            continue

        try:
            require_operator_permission_for_scope(
                required_level=1,  # edit-level
                floor_ids=[floor_id],
                enforce_on_empty_scope=True,
                db=db,
                current_user=user
            )
            permitted_floors.append(floor_data)
        except HTTPException as e:
            if e.status_code != 403:  # re-raise non-permission errors
                raise

    # Recalculate totals based on permitted floors
    total_sqft = sum(f["area_sqft"] for f in permitted_floors)
    total_sqm = sum(f["area_sqm"] for f in permitted_floors)
    total_load = sum(f["area_load"] for f in permitted_floors)

    return {
        "status": "success",
        "floors": permitted_floors,
        "total": {
            "total_area_sqft": total_sqft,
            "total_area_sqm": total_sqm,
            "total_area_load": total_load
        }
    }





@router.post("/area_size_download")
def download_area_csv(
    request: AreaCSVRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    csv_stream = generate_area_csv(request, db)
    return StreamingResponse(csv_stream, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=area_data.csv"
    })

