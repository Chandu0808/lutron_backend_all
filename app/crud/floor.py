from collections import defaultdict
from itertools import groupby
from fastapi import HTTPException
from typing import List, Any
from sqlalchemy.orm import Session
from app.models.floor import Floor
from app.models.area import Area
from app.models.processor import Processor
from app.models.events import CurrentAreaEvent
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import create_ssl_connection, send_json, recv_json
from app.models.coordinate import Coordinate
from app.schemas.floor import (
    ModifyCoordinatesRequest,
    ModifyCoordinatesResponse,
    Operation,
    Unit,
)

from app.crud.fofp_settings import get_fofp_settings
from app.crud.fofp_overlay import (
    attach_driver_alerts_to_positions,
    attach_live_status_to_positions,
    get_active_driver_alerts_by_zone,
    get_overlay_positions_for_floor,
    get_zone_live_status_for_fofp,
)


def area_coordinates_to_rings(coordinates: Any) -> List[List[dict]]:
    """
    Group coordinates by polygon_index into rings for multi-polygon support.
    Returns [[{x,y},{x,y}...], [{x,y}...], ...] - array of polygon rings.
    Single-polygon areas return [[ring]] for consistent frontend handling.
    """
    if not coordinates:
        return []
    sorted_coords = sorted(
        coordinates,
        key=lambda c: (getattr(c, "polygon_index", 0), getattr(c, "id", 0) or 0),
    )
    rings = []
    for _idx, group in groupby(sorted_coords, key=lambda c: getattr(c, "polygon_index", 0)):
        ring = [
            {"x": c.x, "y": c.y}
            for c in group
            if c.x is not None and c.y is not None
        ]
        if len(ring) >= 3:
            rings.append(ring)
    return rings


def generate_and_save_area_tree(db: Session, floor_id: int):
    """
    Generate area tree for a floor and save it to the area_tree column.
    Returns True if successful, False otherwise.
    """
    from app.crud.area_tree import get_area_tree_by_floor
    
    try:
        tree = get_area_tree_by_floor(db, floor_id)
        floor = db.query(Floor).filter(Floor.id == floor_id).first()
        if floor:
            floor.area_tree = tree
            db.commit()
            return True
    except Exception as e:
        print(f"Error generating area tree for floor {floor_id}: {e}")
        return False


def calculate_floor_boundaries(db: Session, floor_id: int):
    """
    Calculate floor boundaries based on all area coordinates.
    Returns dict with x_left, x_right, y_top, y_bottom values.
    """
    # Get all areas for this floor
    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    
    if not areas:
        return None
    
    # Get all coordinates for all areas on this floor
    coordinates = (
        db.query(Coordinate)
        .join(Area, Area.id == Coordinate.area_id)
        .filter(Area.floor_id == floor_id)
        .all()
    )
    
    if not coordinates:
        return None
    
    # Find min and max values
    x_values = [coord.x for coord in coordinates if coord.x is not None]
    y_values = [coord.y for coord in coordinates if coord.y is not None]
    
    if not x_values or not y_values:
        return None
    
    x_min = min(x_values)
    x_max = max(x_values)
    y_min = min(y_values)
    y_max = max(y_values)
    
    # Calculate differences
    x_diff = x_max - x_min
    y_diff = y_max - y_min
    
    # Apply 3% margin
    x_margin = x_diff * 0.03
    y_margin = y_diff * 0.03
    
    return {
        "x_left": x_min - x_margin,
        "x_right": x_max + x_margin,
        "y_top": y_min - y_margin,
        "y_bottom": y_max + y_margin
    }

def update_floor_boundaries(db: Session, floor_id: int):
    """
    Calculate and update floor boundary coordinates in the database.
    """
    boundaries = calculate_floor_boundaries(db, floor_id)
    if boundaries:
        floor = db.query(Floor).filter(Floor.id == floor_id).first()
        if floor:
            floor.x_left = boundaries["x_left"]
            floor.x_right = boundaries["x_right"]
            floor.y_top = boundaries["y_top"]
            floor.y_bottom = boundaries["y_bottom"]
            db.commit()
            return True
    return False

def create_floor(db: Session, name: str, image_path: str):
    floor = Floor(name=name, image_path=image_path)
    db.add(floor)
    db.commit()
    db.refresh(floor)
    return floor

def get_area_light_status_by_floor(db: Session, floor_id: int):
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        return {"status": "error", "message": "Floor not found"}

    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    if not areas:
        return {"status": "error", "message": "No areas on this floor"}

    # Group areas by processor
    processor_area_map = defaultdict(list)
    for area in areas:
        processor_area_map[area.processor_id].append(area)

    # Initialize results with all areas having null status
    # Use a dict to track which areas have been processed
    results_dict = {
        area.id: {
            "id": area.id,
            "name": area.name,
            "code": area.code,
            "floor_id": area.floor_id,
            "processor_id": area.processor_id,
            "co-ordinates": area_coordinates_to_rings(area.coordinates),
            "light_status": None,
            "light_level": 0,
        }
        for area in areas
    }

    for processor_id, processor_areas in processor_area_map.items():
        processor = db.query(Processor).filter(Processor.id == processor_id).first()
        if not processor:
            # Processor not found - areas remain with null status
            continue

        if not is_processor_reachable(processor.ipv4):
            print(f"Processor not reachable: {processor.ipv4}")
            # Areas remain with null status - don't add them again
            continue

        try:
            with create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                send_json(ssock, {
                    "CommuniqueType": "ReadRequest",
                    "Header": {"Url": "/area/status"}
                })
                response = recv_json(ssock)
                status_map = {
                    item["href"]: item
                    for item in response.get("Body", {}).get("AreaStatuses", [])
                }

                for area in processor_areas:
                    area_href = f"/area/{area.code}/status"
                    level = status_map.get(area_href, {}).get("Level")
                    try:
                        light_level = max(0, min(100, int(round(float(level)))))
                    except (TypeError, ValueError):
                        light_level = 0

                    if level == 0:
                        zone_status = "off"
                    elif level:
                        zone_status = "on"
                    else:
                        zone_status = None  # Changed from "unknown" to None

                    # Update the area status in results_dict
                    results_dict[area.id]["light_status"] = zone_status
                    results_dict[area.id]["light_level"] = light_level

        except Exception as e:
            print(f"Processor {processor.ipv4} error: {e}")
            # Areas remain with null status - no need to update

    # Convert dict values to list
    results = list(results_dict.values())

    response = {
        "status": "success",
        "floor_plan": floor.image_path,
        "boundary_values": {
            "x_left": round(floor.x_left) if floor.x_left else 0,
            "x_right": round(floor.x_right) if floor.x_right else 0,
            "y_top": round(floor.y_top) if floor.y_top else 0,
            "y_bottom": round(floor.y_bottom) if floor.y_bottom else 0
        },
        "x_left": floor.x_left,
        "x_right": floor.x_right,
        "y_top": floor.y_top,
        "y_bottom": floor.y_bottom,
        "areas": results
    }

    # Additive read-only FOFP overlay fields (Step 6).
    #
    # Strict safety contract:
    # - This block must never alter any existing field above.
    # - Any failure inside this block silently degrades to "FOFP disabled".
    # - Empty positions when disabled, missing config, or any error path.
    #
    # The helpers themselves are non-raising; the outer guard is a belt-and-
    # suspenders safety net so a regression here can never break the legacy
    # light_status contract.
    try:
        fofp_cfg = get_fofp_settings(db)
        fofp_enabled = bool(fofp_cfg.enabled)
        fofp_positions: list = []

        if fofp_enabled:
            fofp_positions = get_overlay_positions_for_floor(db, floor_id)

            # Step 7: zone-wise light_level per marker from current_zone_status only.
            if fofp_positions:
                try:
                    status_by_zone = get_zone_live_status_for_fofp(db, fofp_positions)
                    fofp_positions = attach_live_status_to_positions(
                        fofp_positions, status_by_zone
                    )
                except Exception:
                    fofp_positions = attach_live_status_to_positions(
                        fofp_positions, {}
                    )

            if fofp_positions:
                try:
                    zone_ids = [
                        p["zone_id"]
                        for p in fofp_positions
                        if isinstance(p, dict) and p.get("zone_id") is not None
                    ]
                    alerts_by_zone = get_active_driver_alerts_by_zone(db, zone_ids)
                    fofp_positions = attach_driver_alerts_to_positions(
                        fofp_positions, alerts_by_zone
                    )
                except Exception:
                    fofp_positions = attach_driver_alerts_to_positions(fofp_positions, {})

        response["fofp_enabled"] = fofp_enabled
        response["fofp_config"] = fofp_cfg.as_response_dict()
        response["fofp_positions"] = fofp_positions
    except Exception:
        response["fofp_enabled"] = False
        response["fofp_config"] = {"shape": "circle", "marker_size": 5}
        response["fofp_positions"] = []

    return response



def get_area_occupancy_status_by_floor(db: Session, floor_id: int):
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        return {"status": "error", "message": "Floor not found"}

    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    if not areas:
        return {"status": "error", "message": "No areas on this floor"}

    processor_area_map = defaultdict(list)
    for area in areas:
        processor_area_map[area.processor_id].append(area)

    # Initialize results with all areas having null status
    # Use a dict to track which areas have been processed
    results_dict = {
        area.id: {
            "id": area.id,
            "name": area.name,
            "code": area.code,
            "floor_id": area.floor_id,
            "processor_id": area.processor_id,
            "co-ordinates": area_coordinates_to_rings(area.coordinates),
            "occupancy_status": None
        }
        for area in areas
    }

    for processor_id, processor_areas in processor_area_map.items():
        processor = db.query(Processor).filter(Processor.id == processor_id).first()
        if not processor:
            # Processor not found - areas remain with null status
            continue

        if not is_processor_reachable(processor.ipv4):
            print(f"Processor not reachable: {processor.ipv4}")
            # Areas remain with null status - don't add them again
            continue

        try:
            with create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                send_json(ssock, {
                    "CommuniqueType": "ReadRequest",
                    "Header": {"Url": "/area/status"}
                })
                response = recv_json(ssock)
                status_map = {
                    item["href"]: item
                    for item in response.get("Body", {}).get("AreaStatuses", [])
                }

                for area in processor_areas:
                    area_href = f"/area/{area.code}/status"
                    occupancy = status_map.get(area_href, {}).get("OccupancyStatus")
                    
                    # If OccupancyStatus is None or "Unknown", set to None
                    if occupancy is None or occupancy == "Unknown":
                        occupancy = None

                    # Update the area status in results_dict
                    results_dict[area.id]["occupancy_status"] = occupancy

        except Exception as e:
            print(f"Error retrieving occupancy from processor {processor.ipv4}: {e}")
            # Areas remain with null status - no need to update

    # Convert dict values to list
    results = list(results_dict.values())

    return {
        "status": "success",
        "floor_plan": floor.image_path,
        "boundary_values": {
            "x_left": round(floor.x_left) if floor.x_left else 0,
            "x_right": round(floor.x_right) if floor.x_right else 0,
            "y_top": round(floor.y_top) if floor.y_top else 0,
            "y_bottom": round(floor.y_bottom) if floor.y_bottom else 0
        },
        "x_left": floor.x_left,
        "x_right": floor.x_right,
        "y_top": floor.y_top,
        "y_bottom": floor.y_bottom,
        "areas": results
    }

def get_area_energy_status_by_floor(db: Session, floor_id: int):
    # Step 1: Validate floor existence
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        return {"status": "error", "message": "Floor not found"}

    # Step 2: Fetch all areas for the floor
    areas = db.query(Area).filter(Area.floor_id == floor_id).all()
    if not areas:
        return {"status": "error", "message": "No areas found on this floor"}

    area_ids = [area.id for area in areas]

    # Step 3: Fetch current energy data for those areas
    energy_events = (
        db.query(CurrentAreaEvent)
        .filter(CurrentAreaEvent.area_id.in_(area_ids))
        .all()
    )

    energy_map = {
        event.area_id: {
            "instantaneous_power": event.instantaneous_power,
            "instantaneous_max_power": event.instantaneous_max_power
        }
        for event in energy_events
    }

    # Step 4: Build result list
    results = []
    for area in areas:
        power_data = energy_map.get(area.id, {})
        inst_power = power_data.get("instantaneous_power")
        inst_max = power_data.get("instantaneous_max_power")

        load_pct = None
        if inst_power is not None and inst_max and inst_max != 0:
            load_pct = round((inst_power / inst_max) * 100, 2)

        results.append({
            "id": area.id,
            "name": area.name,
            "code": area.code,
            "floor_id": area.floor_id,
            "processor_id": area.processor_id,
            "co-ordinates": area_coordinates_to_rings(area.coordinates),
            "instantaneous_power": inst_power,
            "instantaneous_max_power": inst_max,
            "load_percentage": load_pct
        })

    return {
        "status": "success",
        "floor_plan": floor.image_path,
        "boundary_values": {
            "x_left": round(floor.x_left) if floor.x_left else 0,
            "x_right": round(floor.x_right) if floor.x_right else 0,
            "y_top": round(floor.y_top) if floor.y_top else 0,
            "y_bottom": round(floor.y_bottom) if floor.y_bottom else 0
        },
        "x_left": floor.x_left,
        "x_right": floor.x_right,
        "y_top": floor.y_top,
        "y_bottom": floor.y_bottom,
        "areas": results
    }




def modify_coordinates_in_db(db: Session, payload: ModifyCoordinatesRequest) -> ModifyCoordinatesResponse:
    floor = db.query(Floor).filter(Floor.id == payload.floor_id).first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    coords = (
        db.query(Coordinate)
        .join(Area, Area.id == Coordinate.area_id)
        .filter(Area.floor_id == payload.floor_id)
        .all()
    )

    if not coords:
        return ModifyCoordinatesResponse(
            status="success",
            floor_id=payload.floor_id,
            operation=payload.operation,
            affected_coordinates=0,
            details={}
        )

    affected = 0
    details: dict = {}

    if payload.operation == Operation.move_x:
        if payload.move_by == Unit.pixels:
            for c in coords:
                c.x = (c.x or 0.0) + payload.move_value
                affected += 1
            details = {"mode": "move_x", "by": "pixels", "delta": payload.move_value}
        else:  # percentage -> shift by % of current X-span
            xs = [(c.x or 0.0) for c in coords]
            span = (max(xs) - min(xs)) if xs else 0.0
            delta = (abs(payload.move_value) / 100.0) * span
            if payload.move_value < 0:
                delta = -delta
            for c in coords:
                c.x = (c.x or 0.0) + delta
                affected += 1
            details = {"mode": "move_x", "by": "percentage", "span": span, "delta": delta}

    elif payload.operation == Operation.move_y:
        if payload.move_by == Unit.pixels:
            for c in coords:
                c.y = (c.y or 0.0) + payload.move_value
                affected += 1
            details = {"mode": "move_y", "by": "pixels", "delta": payload.move_value}
        else:  # percentage -> shift by % of current Y-span
            ys = [(c.y or 0.0) for c in coords]
            span = (max(ys) - min(ys)) if ys else 0.0
            delta = (abs(payload.move_value) / 100.0) * span
            if payload.move_value < 0:
                delta = -delta
            for c in coords:
                c.y = (c.y or 0.0) + delta
                affected += 1
            details = {"mode": "move_y", "by": "percentage", "span": span, "delta": delta}

    elif payload.operation == Operation.scale_x:
        min_x = min((c.x or 0.0) for c in coords)
        sf = payload.scale_factor
        for c in coords:
            base = (c.x or 0.0) - min_x
            c.x = min_x + base * sf
            affected += 1
        details = {"mode": "scale_x", "anchor_min_x": min_x, "scale_factor": sf}

    elif payload.operation == Operation.scale_y:
        ys = [(c.y or 0.0) for c in coords]
        center_y = (max(ys) + min(ys)) / 2.0
        sf = payload.scale_factor
        for c in coords:
            c.y = center_y + ((c.y or 0.0) - center_y) * sf
            affected += 1
        details = {"mode": "scale_y", "anchor_center_y": center_y, "scale_factor": sf}

    db.commit()
    
    # Update floor boundaries after coordinates are modified
    update_floor_boundaries(db, payload.floor_id)

    return ModifyCoordinatesResponse(
        status="success",
        floor_id=payload.floor_id,
        operation=payload.operation,
        affected_coordinates=affected,
        details=details
    )
