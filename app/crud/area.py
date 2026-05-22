from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.area import Area
from app.models.processor import Processor
from app.models.events import CurrentAreaEvent
from typing import List, Dict, Any, Optional, Tuple
from math import sqrt
from itertools import groupby
from app.models.coordinate import Coordinate
from app.models.floor import Floor
from app.schemas.area import Point



from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.logger import logger

def get_area_scene_summary_by_area_id(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Scene Summary] Area {area_id} not found")
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Scene Summary] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Scene Summary] Processor {processor.ipv4} not reachable")
        return {"status": "error", "message": f"Processor {processor.ipv4} not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        logger.info(f"[Scene Summary] Fetching scenes for area {area.code}")
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/areascene"}
        })
        scenes_response = recv_json(ssock)

        scene_list = scenes_response.get("Body", {}).get("AreaScenes", [])

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        status_response = recv_json(ssock)

        area_status = status_response.get("Body", {}).get("AreaStatus", {})
        current_scene = area_status.get("CurrentScene")
        active_href = current_scene.get("href") if current_scene else None
        active_scene_id = int(active_href.split("/")[-1]) if active_href else None

        ssock.close()

        area_scenes = [
            {"id": int(scene["href"].split("/")[-1]), "name": scene.get("Name", "")}
            for scene in scene_list
        ]

        return {
            "status": "success",
            "active_scene": active_scene_id,
            "area_scenes": area_scenes
        }

    except Exception as e:
        logger.exception(f"[Scene Summary] Error for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}

def _lutron_zone_code_from_href(href: Optional[str]) -> Optional[int]:
    """Parse Lutron zone code from a zone href (e.g. ``/zone/5/status``)."""
    if not href or not isinstance(href, str):
        return None
    parts = [p for p in href.strip("/").split("/") if p]
    if "zone" not in parts:
        return None
    idx = parts.index("zone")
    if idx + 1 >= len(parts):
        return None
    try:
        return int(parts[idx + 1])
    except (TypeError, ValueError):
        return None


def _fofp_status_from_level(level: int) -> Dict[str, Any]:
    """Wire-format light fields for FOFP markers from a 0-100 level."""
    if level <= 0:
        return {"light_level": 0, "light_status": False}
    return {"light_level": level, "light_status": True}


def fetch_zone_light_levels_for_area(db: Session, area_id: int) -> Dict[int, Dict[str, Any]]:
    """
    Return per-zone light status for an area keyed by ``zones.id`` (DB primary key).

    Uses the same LEAP endpoint as :func:`get_area_zones_with_status`
    (``/area/{code}/associatedzone/status``). Never raises; returns ``{}`` on failure.
    """
    from app.models.zone import Zone

    try:
        area_id_int = int(area_id)
    except (TypeError, ValueError):
        return {}

    area = db.query(Area).filter(Area.id == area_id_int).first()
    if not area:
        return {}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        return {}

    if not is_processor_reachable(processor.ipv4):
        logger.warning(
            "[FOFP Zone Status] Processor %s not reachable for area %s",
            processor.ipv4,
            area_id_int,
        )
        return {}

    out: Dict[int, Dict[str, Any]] = {}
    try:
        ssock = connect_to_processor(
            processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4
        )
        send_json(
            ssock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area.code}/associatedzone/status"},
            },
        )
        status_resp = recv_json(ssock)
        status_zones = status_resp.get("Body", {}).get("ZoneStatuses", []) or []

        for status in status_zones:
            zone_href = (status.get("Zone") or {}).get("href", "")
            lutron_code = _lutron_zone_code_from_href(zone_href)
            if lutron_code is None:
                continue
            try:
                level_raw = status.get("Level", 0)
                level = max(0, min(100, int(round(float(level_raw)))))
            except (TypeError, ValueError):
                level = 0

            zone_row = (
                db.query(Zone)
                .filter(Zone.area_id == area_id_int, Zone.code == str(lutron_code))
                .first()
            )
            if zone_row is None:
                zone_row = (
                    db.query(Zone)
                    .filter(
                        Zone.processor_id == area.processor_id,
                        Zone.code == str(lutron_code),
                    )
                    .first()
                )
            if zone_row is not None:
                out[int(zone_row.id)] = _fofp_status_from_level(level)

        ssock.close()
    except Exception as exc:
        logger.warning(
            "[FOFP Zone Status] Live fetch failed for area %s: %s", area_id_int, exc
        )
    return out


def get_area_zones_with_status(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Zone Status] Area {area_id} not found")
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Zone Status] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Zone Status] Processor {processor.ipv4} not reachable")
        return {"status": "error", "message": f"Processor {processor.ipv4} not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        # Step 1: Fetch zone metadata
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone"}
        })
        metadata_resp = recv_json(ssock)
        metadata_zones = metadata_resp.get("Body", {}).get("Zones", [])
        zone_meta_map = {}
        for zone in metadata_zones:
            zone_id = int(zone["href"].split("/")[-1])
            zone_meta_map[zone_id] = {
                "name": zone.get("Name", f"Zone {zone_id}"),
                "type": zone.get("ControlType", "Unknown")
            }

        # Step 2: Fetch zone statuses
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone/status"}
        })
        status_resp = recv_json(ssock)
        status_zones = status_resp.get("Body", {}).get("ZoneStatuses", [])

        enriched_zones = []
        for status in status_zones:
            zone_href = status.get("Zone", {}).get("href", "")
            zone_id = int(zone_href.split("/")[-1]) if zone_href else None
            level = status.get("Level", 0)

            meta = zone_meta_map.get(zone_id, {})
            zone_type = meta.get("type", "Unknown").lower()
            zone_name = meta.get("name", f"Zone {zone_id}")

            zone_obj = {
                "id": zone_id,
                "name": zone_name,
                "type": zone_type
            }

            if zone_type == "switched":
                zone_obj["level"] = level
                zone_obj["status"] = "On" if level == 100 else "Off" if level == 0 else "INVALID"

            elif zone_type == "dimmed":
                zone_obj["brightness"] = f"{level}%"

            elif zone_type == "whitetune":
                zone_obj["brightness"] = f"{level}%"
                kelvin = status.get("ColorTuningStatus", {}).get("WhiteTuningLevel", {}).get("Kelvin")
                if kelvin:
                    zone_obj["temperature"] = f"{kelvin}K"

            elif zone_type == "shade":
                zone_obj["level"] = f"{level}%"

            else:
                zone_obj["brightness"] = f"{level}%"  # fallback

            enriched_zones.append(zone_obj)

        ssock.close()
        return {"status": "success", "zones": enriched_zones}

    except Exception as e:
        logger.exception(f"[Zone Status] Error for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}


def get_area_light_status(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Light Status] Processor {processor.ipv4} not reachable")
        return {"status": "error", "message": "Processor not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        response = recv_json(ssock)
        ssock.close()

        level = response.get("Body", {}).get("AreaStatus", {}).get("Level", 0)
        return {"status": "success", "light_status": "On" if level > 0 else "Off"}

    except Exception as e:
        logger.exception(f"[Light Status] Error for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}


def get_area_occupancy_status(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Occupancy] Processor {processor.ipv4} not reachable")
        return {"status": "error", "message": "Processor not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        response = recv_json(ssock)
        ssock.close()

        occ_status = response.get("Body", {}).get("AreaStatus", {}).get("OccupancyStatus", "Unknown")
        return {"status": "success", "occupancy_status": occ_status}

    except Exception as e:
        logger.exception(f"[Occupancy] Error for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}

def get_area_energy_status(db: Session, area_id: int):
    """
    Returns the energy consumption and savings for the given area.
    """
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return {"status": "error", "message": "Area not found"}

    event = (
        db.query(CurrentAreaEvent)
        .filter(CurrentAreaEvent.area_id == area_id)
        .first()
    )

    if event and event.instantaneous_power is not None and event.instantaneous_max_power is not None:
        consumption = event.instantaneous_power
        savings = event.instantaneous_max_power - event.instantaneous_power
    else:
        consumption = "Unknown"
        savings = "Unknown"

    return {
        "status": "success",
        "consumption": consumption,
        "savings": savings
    }

#set scene update logic
def activate_scene_for_area(area_id: int, scene_code: int, db: Session):
    logger.info(f"[activate_scene_for_area] Area: {area_id}, Scene: {scene_code}")
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Scene Activate] Area {area_id} not found")
        return {"status": "error", "message": f"Area {area_id} not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Scene Activate] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        request = {
            "CommuniqueType": "CreateRequest",
            "Header": {
                "URL": f"/area/{area.code}/commandprocessor"
            },
            "Body": {
                "Command": {
                    "CommandType": "GoToScene",
                    "GoToSceneParameters": {
                        "CurrentScene": {
                            "href": f"/areascene/{scene_code}"
                        }
                    }
                }
            }
        }

        logger.info(f"[Scene Activate] Sending scene activation for /areascene/{scene_code} in /area/{area.code}")
        send_json(ssock, request)
        response = recv_json(ssock)
        ssock.close()

        logger.info(f"[Scene Activate] Response: {response}")
        return {
            "status": "success",
            "area_code": area.code,
            "scene_href": f"/areascene/{scene_code}",
            "response": response
        }

    except Exception as e:
        logger.exception(f"[Scene Activate] Error activating scene: {e}")
        return {"status": "error", "message": str(e)}
    
    #zone update logic

def update_zones_by_area(db: Session, area_id: int, zones: list):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Zone Update] Area {area_id} not found")
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Zone Update] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        for zone in zones:
            zone_id = zone.get("zone_id")
            zone_type = zone.get("zone_type", "").lower()
            fade = zone.get("fade_time")
            delay = zone.get("delay_time")

            if zone_type == "switched":
                payload = {
                    "Command": {
                        "CommandType": "GoToSwitchedLevel",
                        "SwitchedLevelParameters": {
                            "SwitchedLevel": zone.get("switched_state", "Off")  # "On"/"Off"
                        }
                    }
                }

            elif zone_type == "dimmed":
                payload = {
                    "Command": {
                        "CommandType": "GoToDimmedLevel",
                        "DimmedLevelParameters": {
                            "Level": zone.get("level", 0)
                        }
                    }
                }
                if fade is not None:
                    payload["Command"]["DimmedLevelParameters"]["FadeTime"] = str(fade)
                if delay is not None:
                    payload["Command"]["DimmedLevelParameters"]["DelayTime"] = str(delay)

            elif zone_type == "whitetune":
                payload = {
                    "Command": {
                        "CommandType": "GoToWhiteTuningLevel",
                        "WhiteTuningLevelParameters": {
                            "Level": zone.get("level", 0),
                            "WhiteTuningLevel": {
                                "Kelvin": zone.get("kelvin", 3000)
                            }
                        }
                    }
                }
                if fade is not None:
                    payload["Command"]["WhiteTuningLevelParameters"]["FadeTime"] = str(fade)
                if delay is not None:
                    payload["Command"]["WhiteTuningLevelParameters"]["DelayTime"] = str(delay)

            elif zone_type == "shade":
                payload = {
                    "Command": {
                        "CommandType": "GoToShadeLevel",
                        "ShadeLevelParameters": {
                            "Level": zone.get("level", 0)
                        }
                    }
                }

            else:
                logger.warning(f"[Zone Update] Unknown zone type {zone_type} for zone {zone_id}")
                continue

            request = {
                "CommuniqueType": "CreateRequest",
                "Header": {"Url": f"/zone/{zone_id}/commandprocessor"},
                "Body": payload
            }

            logger.info(f"[Zone Update] Sending command to zone {zone_id} ({zone_type})")
            send_json(ssock, request)
            response = recv_json(ssock)
            logger.debug(f"[Zone Update] Response: {response}")

        ssock.close()
        return {"status": "success", "message": "Zones updated successfully"}

    except Exception as e:
        logger.exception(f"[Zone Update] Error updating zones for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}

def set_all_zones_on_off(db: Session, area_id: int, action: str):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Zone On/Off] Area {area_id} not found")
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Zone On/Off] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        # Step 1: Get all associated zones with metadata
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone"}
        })
        resp = recv_json(ssock)
        zone_list = resp.get("Body", {}).get("Zones", [])

        for zone in zone_list:
            zone_href = zone.get("href")
            zone_id = int(zone_href.split("/")[-1])
            zone_type = zone.get("ControlType", "Unknown")

            if zone_type == "Switched":
                payload = {
                    "Command": {
                        "CommandType": "GoToSwitchedLevel",
                        "SwitchedLevelParameters": {
                            "SwitchedLevel": action  # "On" or "Off"
                        }
                    }
                }

            elif zone_type == "Dimmed":
                payload = {
                    "Command": {
                        "CommandType": "GoToDimmedLevel",
                        "DimmedLevelParameters": {
                            "Level": 100 if action == "On" else 0
                        }
                    }
                }

            elif zone_type == "WhiteTune":
                payload = {
                    "Command": {
                        "CommandType": "GoToWhiteTuningLevel",
                        "WhiteTuningLevelParameters": {
                            "Level": 100 if action == "On" else 0,
                            "WhiteTuningLevel": {
                                "Kelvin": 3500  # Default temperature
                            }
                        }
                    }
                }

            else:
                logger.warning(f"[Zone On/Off] Unsupported zone type: {zone_type} (zone_id={zone_id})")
                continue

            request = {
                "CommuniqueType": "CreateRequest",
                "Header": {"Url": f"/zone/{zone_id}/commandprocessor"},
                "Body": payload
            }

            logger.info(f"[Zone On/Off] Sending '{action}' to zone {zone_id} ({zone_type})")
            send_json(ssock, request)
            response = recv_json(ssock)
            logger.debug(f"[Zone On/Off] Response: {response}")

        ssock.close()
        return {"status": "success", "message": f"All zones turned {action}"}

    except Exception as e:
        logger.exception(f"[Zone On/Off] Error for area {area_id}: {e}")
        return {"status": "error", "message": str(e)}
    
def get_shade_zones_by_area(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Shade Zones] Area {area_id} not found")
        raise Exception("Area not found")

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Shade Zones] Processor not found for area {area_id}")
        raise Exception("Processor not found")

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Shade Zones] Processor {processor.ipv4} not reachable")
        raise Exception(f"Processor {processor.ipv4} not reachable")

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        # Fetch zone metadata
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone"}
        })
        metadata_resp = recv_json(ssock)
        metadata_zones = metadata_resp.get("Body", {}).get("Zones", [])
        zone_meta_map = {
            int(zone["href"].split("/")[-1]): {
                "name": zone.get("Name", f"Zone {zone.get('href')}"),
                "type": zone.get("ControlType", "Unknown")
            }
            for zone in metadata_zones
        }

        # Fetch zone statuses
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone/status"}
        })
        status_resp = recv_json(ssock)
        status_zones = status_resp.get("Body", {}).get("ZoneStatuses", [])

        shade_zones = []
        for status in status_zones:
            zone_href = status.get("Zone", {}).get("href")
            if not zone_href:
                continue
            zone_id = int(zone_href.split("/")[-1])
            meta = zone_meta_map.get(zone_id, {})
            zone_type = meta.get("type", "").lower()

            if zone_type == "shade":
                level = status.get("Level", 0)
                shade_zones.append({
                    "id": zone_id,
                    "name": meta.get("name", f"Zone {zone_id}"),
                    "type": zone_type,
                    "level": f"{level}%"
                })

        ssock.close()
        return {"status": "success", "zones": shade_zones}

    except Exception as e:
        logger.exception(f"[Shade Zones] Error for area {area_id}: {e}")
        raise




def calculate_polygon_area(coords: List[Point]) -> float:
    n = len(coords)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords[i].x * coords[j].y
        area -= coords[j].x * coords[i].y
    return abs(area) / 2.0

def update_area_sizes_from_reference(
    db: Session,
    first_point: Point,
    second_point: Point,
    floor_id: int,
    length_in_meters: float = None,
    length_in_feet: float = None,
):
    dx = second_point.x - first_point.x
    dy = second_point.y - first_point.y
    pixel_distance = sqrt(dx ** 2 + dy ** 2)

    if pixel_distance == 0:
        return {"status": "error", "message": "Selected points are identical."}

    if length_in_meters:
        scale_m = pixel_distance / length_in_meters
        scale_ft = pixel_distance / (length_in_meters * 3.28084)
    elif length_in_feet:
        scale_ft = pixel_distance / length_in_feet
        scale_m = pixel_distance / (length_in_feet * 0.3048)
    else:
        return {"status": "error", "message": "Either length_in_meters or length_in_feet must be provided."}

    updated_areas = []

    # get all areas for this floor
    areas = db.query(Area).filter(Area.floor_id == floor_id).all()

    for area in areas:
        coords_raw = db.query(Coordinate).filter(Coordinate.area_id == area.id).all()
        if not coords_raw:
            continue

        # Group by polygon_index to support multi-polygon areas
        sorted_coords = sorted(
            coords_raw,
            key=lambda c: (getattr(c, "polygon_index", 0), getattr(c, "id", 0) or 0),
        )
        pixel_area = 0.0
        for _idx, group in groupby(sorted_coords, key=lambda c: getattr(c, "polygon_index", 0)):
            ring = [Point(x=coord.x, y=coord.y) for coord in group if coord.x is not None and coord.y is not None]
            if len(ring) >= 3:
                pixel_area += calculate_polygon_area(ring)

        if pixel_area <= 0:
            continue

        area.area_sqm = round(pixel_area / (scale_m ** 2), 2)
        area.area_sqft = round(pixel_area / (scale_ft ** 2), 2)

        updated_areas.append({
            "area_id": area.id,
            "area_sqm": area.area_sqm,
            "area_sqft": area.area_sqft
        })

    db.commit()

    return {
        "status": "success",
        "updated_areas": updated_areas
    }


def sync_area_names_for_floor(
    db: Session, floor_id: int, processor_id: int
) -> Dict[str, Any]:
    """
    Sync area names from Lutron LEAP for all areas on the given floor and processor.
    Fetches current name via ReadRequest /area/{code} and updates Area.name in DB.
    Returns areas_updated, list of {area_id, area_code, old_name, new_name}, and any errors.
    """
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        return {
            "status": "error",
            "message": "Floor not found",
            "floor_id": floor_id,
            "processor_id": processor_id,
            "areas_updated": 0,
            "areas": [],
            "errors": ["Floor not found"],
        }

    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        return {
            "status": "error",
            "message": "Processor not found",
            "floor_id": floor_id,
            "processor_id": processor_id,
            "areas_updated": 0,
            "areas": [],
            "errors": ["Processor not found"],
        }

    areas = (
        db.query(Area)
        .filter(Area.floor_id == floor_id, Area.processor_id == processor_id)
        .all()
    )
    if not areas:
        return {
            "status": "success",
            "message": "No areas on this floor for this processor",
            "floor_id": floor_id,
            "processor_id": processor_id,
            "areas_updated": 0,
            "areas": [],
            "errors": [],
        }

    if not is_processor_reachable(processor.ipv4):
        return {
            "status": "error",
            "message": f"Processor {processor.ipv4} not reachable",
            "floor_id": floor_id,
            "processor_id": processor_id,
            "areas_updated": 0,
            "areas": [],
            "errors": ["Processor not reachable"],
        }

    updated_list: List[Dict[str, Any]] = []
    errors: List[str] = []
    ssock = None

    try:
        ssock = connect_to_processor(
            processor.ipv4,
            processor.mac,
            processor.system,
            processor_ipv4=processor.ipv4,
        )
        if not ssock:
            return {
                "status": "error",
                "message": "Failed to connect to processor",
                "floor_id": floor_id,
                "processor_id": processor_id,
                "areas_updated": 0,
                "areas": [],
                "errors": ["Connection failed"],
            }

        for area in areas:
            area_code = str(area.code) if area.code is not None else ""
            if not area_code:
                errors.append(f"Area id={area.id} has no code")
                continue
            old_name = area.name
            try:
                send_json(
                    ssock,
                    {
                        "CommuniqueType": "ReadRequest",
                        "Header": {"Url": f"/area/{area_code}"},
                    },
                )
                resp = recv_json(ssock)
                body_area = (resp or {}).get("Body", {}).get("Area")
                new_name: Optional[str] = body_area.get("Name") if body_area else None
                if new_name is not None and new_name != old_name:
                    area.name = new_name
                    updated_list.append({
                        "area_id": area.id,
                        "area_code": area_code,
                        "old_name": old_name,
                        "new_name": new_name,
                    })
                elif new_name is None:
                    errors.append(f"Area id={area.id} (code={area_code}): no name in LEAP response")
            except Exception as e:
                errors.append(f"Area id={area.id} (code={area_code}): {e}")

        db.commit()
    except Exception as e:
        logger.exception(f"[Sync area names] Error: {e}")
        errors.append(str(e))
        db.rollback()
        updated_list = []
    finally:
        if ssock:
            try:
                ssock.close()
            except Exception:
                pass

    return {
        "status": "success" if not (errors and not updated_list) else "partial",
        "floor_id": floor_id,
        "processor_id": processor_id,
        "areas_updated": len(updated_list),
        "areas": updated_list,
        "errors": errors,
    }


def parse_area_rename_leap_response(
    resp: Optional[Dict[str, Any]],
) -> Tuple[bool, Optional[str], str]:
    """
    Interpret LEAP response after UpdateRequest to /area/{code}.

    Returns:
        (ok, processor_name_or_none, error_message)
        error_message is empty when ok is True.
    """
    if resp is None:
        return False, None, "No response from processor"

    ctype = str(resp.get("CommuniqueType") or "")
    if "ExceptionResponse" in ctype:
        return False, None, f"Processor error: {resp}"

    if "UpdateResponse" not in ctype:
        return False, None, f"Unexpected CommuniqueType: {ctype or 'missing'}"

    header = resp.get("Header") or {}
    status_code = str(header.get("StatusCode") or "")
    if status_code and status_code != "200 OK":
        return False, None, f"Processor status: {status_code}"

    body_area = (resp.get("Body") or {}).get("Area")
    proc_name: Optional[str] = None
    if isinstance(body_area, dict):
        raw = body_area.get("Name")
        if raw is not None:
            proc_name = str(raw)

    return True, proc_name, ""


def update_area_name_on_processor_and_db(
    db: Session, area_id: int, new_name: str
) -> Dict[str, Any]:
    """
    Rename an area on the Lutron processor (LEAP UpdateRequest), then persist Area.name.

    Loads area by area_id; uses area.code for LEAP and area.processor_id for the connection.
    """
    name_stripped = (new_name or "").strip()
    if not name_stripped:
        raise HTTPException(status_code=400, detail="Name must not be empty")

    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    processor_id = area.processor_id
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    if not is_processor_reachable(processor.ipv4):
        raise HTTPException(
            status_code=503,
            detail=f"Processor {processor.ipv4} not reachable",
        )

    area_code = str(area.code) if area.code is not None else ""
    if not area_code:
        raise HTTPException(status_code=400, detail="Area has no LEAP code")

    ssock = None
    try:
        ssock = connect_to_processor(
            processor.ipv4,
            processor.mac,
            processor.system,
            processor_ipv4=processor.ipv4,
        )
        if not ssock:
            raise HTTPException(
                status_code=503,
                detail="Failed to connect to processor",
            )

        send_json(
            ssock,
            {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": f"/area/{area_code}"},
                "Body": {"Area": {"Name": name_stripped}},
            },
        )
        resp = recv_json(ssock)
        ok, proc_name, err = parse_area_rename_leap_response(resp)
        if not ok:
            raise HTTPException(status_code=502, detail=err)

        final_name = proc_name if proc_name is not None else name_stripped
        area.name = final_name
        db.commit()
        db.refresh(area)

        return {
            "status": "success",
            "area_id": area.id,
            "processor_id": processor_id,
            "area_code": area_code,
            "name": final_name,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"[Area rename] Error for area_id={area_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if ssock is not None:
            try:
                ssock.close()
            except Exception:
                pass