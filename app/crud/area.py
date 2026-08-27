from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.area import Area
from app.models.processor import Processor
from app.models.events import CurrentAreaEvent, CurrentZoneEvent
from app.models.area_scene import AreaScene
from typing import List, Dict, Any, Optional, Tuple
from math import sqrt
from itertools import groupby
from app.models.coordinate import Coordinate
from app.models.floor import Floor
from app.models.zone import Zone
from app.schemas.area import Point



from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.logger import logger


_VALID_OCCUPANCY = frozenset({"Occupied", "Unoccupied"})


def _enrich_zone_from_level(zone_id, zone_name, zone_type, level, kelvin=None):
    """Build the same zone wire object used by live LEAP enrichment."""
    zone_type_l = (zone_type or "Unknown").lower()
    try:
        level_int = max(0, min(100, int(round(float(level))))) if level is not None else 0
    except (TypeError, ValueError):
        level_int = 0

    zone_obj = {
        "id": zone_id,
        "name": zone_name or f"Zone {zone_id}",
        "type": zone_type_l,
    }

    if zone_type_l == "switched":
        zone_obj["level"] = level_int
        zone_obj["status"] = "On" if level_int == 100 else "Off" if level_int == 0 else "INVALID"
    elif zone_type_l == "dimmed":
        zone_obj["brightness"] = f"{level_int}%"
    elif zone_type_l == "whitetune":
        zone_obj["brightness"] = f"{level_int}%"
        if kelvin is not None:
            zone_obj["temperature"] = f"{kelvin}K"
    elif zone_type_l == "shade":
        zone_obj["level"] = f"{level_int}%"
    else:
        zone_obj["brightness"] = f"{level_int}%"

    return zone_obj


def _zones_from_db_cache(db: Session, area_id: int, shade_only: bool = False):
    """
    Rebuild zone list from zones table + current_zone_status when LEAP fails.
    Zone id matches live LEAP href id (zone code) when possible.
    """
    zones = db.query(Zone).filter(Zone.area_id == area_id).all()
    if not zones:
        return []

    status_rows = (
        db.query(CurrentZoneEvent)
        .filter(CurrentZoneEvent.area_id == area_id)
        .all()
    )
    by_zone_id = {r.zone_id: r for r in status_rows if r.zone_id is not None}
    by_zone_code = {}
    for r in status_rows:
        if r.zone_code is not None:
            try:
                by_zone_code[int(r.zone_code)] = r
            except (TypeError, ValueError):
                continue

    enriched = []
    for z in zones:
        ztype = (z.type or "").lower()
        if shade_only and ztype != "shade":
            continue

        row = by_zone_id.get(z.id)
        if row is None:
            try:
                row = by_zone_code.get(int(z.code))
            except (TypeError, ValueError):
                row = None

        level = row.level if row else 0
        kelvin = row.white_tuning_kelvin if row else None

        try:
            leap_id = int(z.code)
        except (TypeError, ValueError):
            leap_id = z.id

        enriched.append(
            _enrich_zone_from_level(leap_id, z.name, z.type, level, kelvin)
        )

    return enriched


def _area_light_from_cache(db: Session, area_id: int):
    """Return On/Off from cached non-shade zone levels, or None if unavailable."""
    rows = (
        db.query(CurrentZoneEvent.level, Zone.type)
        .outerjoin(Zone, Zone.id == CurrentZoneEvent.zone_id)
        .filter(CurrentZoneEvent.area_id == area_id)
        .all()
    )
    valid = []
    for level, zone_type in rows:
        if zone_type and str(zone_type).lower() == "shade":
            continue
        try:
            if level is None:
                continue
            valid.append(max(0, min(100, int(round(float(level))))))
        except (TypeError, ValueError):
            continue
    if not valid:
        return None
    return "On" if max(valid) > 0 else "Off"


def _area_occupancy_from_cache(db: Session, area_id: int):
    event = (
        db.query(CurrentAreaEvent)
        .filter(CurrentAreaEvent.area_id == area_id)
        .first()
    )
    if not event:
        return None
    occ = event.occupancy_status
    if occ not in _VALID_OCCUPANCY:
        return None
    return occ


def _active_scene_from_cache(db: Session, area_id: int) -> Optional[int]:
    event = (
        db.query(CurrentAreaEvent)
        .filter(CurrentAreaEvent.area_id == area_id)
        .first()
    )
    if not event or event.current_scene_code is None:
        return None
    try:
        return int(event.current_scene_code)
    except (TypeError, ValueError):
        return None


def _scenes_from_db_cache(db: Session, area_id: int) -> List[Dict[str, Any]]:
    rows = (
        db.query(AreaScene)
        .filter(AreaScene.area_id == area_id)
        .order_by(AreaScene.scene_code.asc())
        .all()
    )
    return [{"id": int(r.scene_code), "name": r.name or ""} for r in rows]


def _upsert_area_scenes_cache(db: Session, area_id: int, area_scenes: List[Dict[str, Any]]) -> None:
    """Replace cached scene list for this area_id after a successful LEAP read (including [])."""
    try:
        db.query(AreaScene).filter(AreaScene.area_id == area_id).delete(synchronize_session=False)
        for scene in area_scenes or []:
            db.add(
                AreaScene(
                    area_id=area_id,
                    scene_code=int(scene["id"]),
                    name=(scene.get("name") or "")[:200],
                )
            )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[Scene Cache] Failed to upsert scenes for area {area_id}: {e}")


def _parse_area_scenes_from_leap(scene_list) -> List[Dict[str, Any]]:
    area_scenes = []
    for scene in scene_list or []:
        if not isinstance(scene, dict):
            continue
        href = scene.get("href") or ""
        try:
            scene_id = int(str(href).rstrip("/").split("/")[-1])
        except (TypeError, ValueError):
            continue
        area_scenes.append({"id": scene_id, "name": scene.get("Name") or ""})
    return area_scenes


def _leap_read_area_scenes(ssock, area_code) -> Optional[List[Dict[str, Any]]]:
    """
    Read /area/{code}/areascene. Returns a parsed list (possibly empty) on a
    valid LEAP dict, or None when the socket/response failed (caller may retry).
    """
    if not ssock or not area_code:
        return None
    send_json(ssock, {
        "CommuniqueType": "ReadRequest",
        "Header": {"Url": f"/area/{area_code}/areascene"},
    })
    scenes_response = recv_json(ssock)
    if not isinstance(scenes_response, dict):
        return None
    scene_list = (scenes_response.get("Body") or {}).get("AreaScenes", []) or []
    return _parse_area_scenes_from_leap(scene_list)


def _parse_active_scene_id(area_status: dict) -> Optional[int]:
    current_scene = area_status.get("CurrentScene") if isinstance(area_status, dict) else None
    if not isinstance(current_scene, dict):
        return None
    active_href = current_scene.get("href")
    if not active_href:
        return None
    try:
        return int(str(active_href).rstrip("/").split("/")[-1])
    except (TypeError, ValueError):
        return None


def get_area_scene_summary_by_area_id(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        logger.error(f"[Scene Summary] Area {area_id} not found")
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        logger.error(f"[Scene Summary] Processor not found for area {area_id}")
        return {"status": "error", "message": "Processor not found"}

    cached_scenes = _scenes_from_db_cache(db, area_id)
    cached_active = _active_scene_from_cache(db, area_id)

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Scene Summary] Processor {processor.ipv4} not reachable; using cache")
        if cached_scenes:
            return {
                "status": "success",
                "active_scene": cached_active,
                "area_scenes": cached_scenes,
            }
        return {"status": "error", "message": f"Processor {processor.ipv4} not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
        if ssock is None:
            if cached_scenes:
                return {
                    "status": "success",
                    "active_scene": cached_active,
                    "area_scenes": cached_scenes,
                }
            return {"status": "error", "message": "Processor connection unavailable"}

        logger.info(f"[Scene Summary] Fetching scenes for area {area.code}")
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/areascene"}
        })
        scenes_response = recv_json(ssock)

        if not isinstance(scenes_response, dict):
            ssock.close()
            if cached_scenes:
                return {
                    "status": "success",
                    "active_scene": cached_active,
                    "area_scenes": cached_scenes,
                }
            return {"status": "error", "message": "Empty LEAP scenes response"}

        scene_list = (scenes_response.get("Body") or {}).get("AreaScenes", []) or []

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        status_response = recv_json(ssock)
        ssock.close()

        area_status = {}
        if isinstance(status_response, dict):
            area_status = (status_response.get("Body") or {}).get("AreaStatus", {}) or {}

        active_scene_id = _parse_active_scene_id(area_status)
        if active_scene_id is None:
            active_scene_id = cached_active

        area_scenes = _parse_area_scenes_from_leap(scene_list)
        # Successful LEAP for this area_id: [] is valid; do not keep stale cache.
        _upsert_area_scenes_cache(db, area_id, area_scenes)

        return {
            "status": "success",
            "active_scene": active_scene_id,
            "area_scenes": area_scenes
        }

    except Exception as e:
        logger.exception(f"[Scene Summary] Error for area {area_id}: {e}")
        if cached_scenes:
            return {
                "status": "success",
                "active_scene": cached_active,
                "area_scenes": cached_scenes,
            }
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


def _parse_live_zone_levels_from_status_resp(status_resp) -> Dict[int, Dict[str, Any]]:
    """Map Lutron zone code -> {level, kelvin} from LEAP /associatedzone/status."""
    out: Dict[int, Dict[str, Any]] = {}
    if not isinstance(status_resp, dict):
        return out
    for status in (status_resp.get("Body") or {}).get("ZoneStatuses", []) or []:
        zone_href = ((status.get("Zone") or {}).get("href") or "")
        zone_code = _lutron_zone_code_from_href(zone_href)
        if zone_code is None:
            try:
                zone_code = int(str(zone_href).rstrip("/").split("/")[-1])
            except (TypeError, ValueError):
                continue
        kelvin = None
        cts = status.get("ColorTuningStatus") or {}
        wtl = cts.get("WhiteTuningLevel") if isinstance(cts, dict) else None
        if isinstance(wtl, dict):
            kelvin = wtl.get("Kelvin")
        out[zone_code] = {"level": status.get("Level", 0), "kelvin": kelvin}
    return out


def _overlay_live_zone_levels_on_zones(
    zones: List[Dict[str, Any]],
    live_by_code: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Apply live LEAP levels onto DB-backed zone rows (same ids, names, types)."""
    if not live_by_code or not zones:
        return zones
    result = []
    for z in zones:
        zid = z.get("id")
        try:
            code = int(zid) if zid is not None else None
        except (TypeError, ValueError):
            code = None
        live = live_by_code.get(code) if code is not None else None
        if live is None:
            result.append(z)
            continue
        result.append(
            _enrich_zone_from_level(
                zid,
                z.get("name"),
                z.get("type"),
                live.get("level"),
                live.get("kelvin"),
            )
        )
    return result


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
        logger.warning(f"[Zone Status] Processor {processor.ipv4} not reachable; using cache")
        return {"status": "success", "zones": _zones_from_db_cache(db, area_id)}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        # Step 1: Fetch zone metadata
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone"}
        })
        metadata_resp = recv_json(ssock)
        if not isinstance(metadata_resp, dict):
            ssock.close()
            logger.warning(f"[Zone Status] Empty LEAP metadata for area {area_id}; using cache")
            return {"status": "success", "zones": _zones_from_db_cache(db, area_id)}

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
        if not isinstance(status_resp, dict):
            ssock.close()
            logger.warning(f"[Zone Status] Empty LEAP status for area {area_id}; using cache")
            return {"status": "success", "zones": _zones_from_db_cache(db, area_id)}

        status_zones = status_resp.get("Body", {}).get("ZoneStatuses", [])

        enriched_zones = []
        for status in status_zones:
            zone_href = status.get("Zone", {}).get("href", "")
            zone_id = int(zone_href.split("/")[-1]) if zone_href else None
            level = status.get("Level", 0)

            meta = zone_meta_map.get(zone_id, {})
            zone_type = meta.get("type", "Unknown")
            zone_name = meta.get("name", f"Zone {zone_id}")
            kelvin = status.get("ColorTuningStatus", {}).get("WhiteTuningLevel", {}).get("Kelvin")

            enriched_zones.append(
                _enrich_zone_from_level(zone_id, zone_name, zone_type, level, kelvin)
            )

        ssock.close()
        return {"status": "success", "zones": enriched_zones}

    except Exception as e:
        logger.exception(f"[Zone Status] Error for area {area_id}: {e}; using cache")
        return {"status": "success", "zones": _zones_from_db_cache(db, area_id)}


def get_area_light_status(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return {"status": "error", "message": "Area not found"}
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        return {"status": "error", "message": "Processor not found"}

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Light Status] Processor {processor.ipv4} not reachable; using cache")
        cached = _area_light_from_cache(db, area_id)
        if cached is not None:
            return {"status": "success", "light_status": cached}
        return {"status": "error", "message": "Processor not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        response = recv_json(ssock)
        ssock.close()

        if not isinstance(response, dict):
            cached = _area_light_from_cache(db, area_id)
            if cached is not None:
                return {"status": "success", "light_status": cached}
            return {"status": "error", "message": "Empty LEAP response"}

        level = response.get("Body", {}).get("AreaStatus", {}).get("Level", 0)
        return {"status": "success", "light_status": "On" if level > 0 else "Off"}

    except Exception as e:
        logger.exception(f"[Light Status] Error for area {area_id}: {e}; using cache")
        cached = _area_light_from_cache(db, area_id)
        if cached is not None:
            return {"status": "success", "light_status": cached}
        return {"status": "error", "message": str(e)}


def get_area_occupancy_status(db: Session, area_id: int):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return {"status": "error", "message": "Area not found"}
    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        return {"status": "error", "message": "Processor not found"}

    if not is_processor_reachable(processor.ipv4):
        logger.warning(f"[Occupancy] Processor {processor.ipv4} not reachable; using cache")
        cached = _area_occupancy_from_cache(db, area_id)
        if cached is not None:
            return {"status": "success", "occupancy_status": cached}
        return {"status": "error", "message": "Processor not reachable"}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/status"}
        })
        response = recv_json(ssock)
        ssock.close()

        if not isinstance(response, dict):
            cached = _area_occupancy_from_cache(db, area_id)
            if cached is not None:
                return {"status": "success", "occupancy_status": cached}
            return {"status": "error", "message": "Empty LEAP response"}

        occ_status = (response.get("Body") or {}).get("AreaStatus", {})
        if not isinstance(occ_status, dict):
            occ_status = {}
        raw_occ = occ_status.get("OccupancyStatus", "Unknown")
        # Keep last good Occupied/Unoccupied — never overwrite UI with Unknown from flaky LEAP.
        if raw_occ not in _VALID_OCCUPANCY:
            cached = _area_occupancy_from_cache(db, area_id)
            if cached is not None:
                return {"status": "success", "occupancy_status": cached}
            return {"status": "success", "occupancy_status": "Unknown"}
        return {"status": "success", "occupancy_status": raw_occ}

    except Exception as e:
        logger.exception(f"[Occupancy] Error for area {area_id}: {e}; using cache")
        cached = _area_occupancy_from_cache(db, area_id)
        if cached is not None:
            return {"status": "success", "occupancy_status": cached}
        return {"status": "error", "message": str(e)}


def assemble_full_area_status(db: Session, area_id: int, live: bool = True) -> Dict[str, Any]:
    """
    Heatmap sidebar payload (scenes + zones + light/occ/energy).

    live=True (default): zone list (names/types) from DB sync cache with live levels
    overlaid from LEAP; area_scenes refreshed from LEAP /areascene when the processor
    is reachable (then cached), otherwise DB cache; active_scene, light, occupancy
    from LEAP when reachable. active_scene is live CurrentScene only (null if missing).

    live=False: listener/DB cache only — no LEAP SSL. Energy always from DB.

    Response shape matches the existing /area/full_area_status wire format.
    """
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        return {"status": "error", "message": "Area not found"}

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()

    energy_result = get_area_energy_status(db, area_id)
    if energy_result.get("status") == "success":
        c = energy_result.get("consumption")
        s = energy_result.get("savings")
        consumption = round(c, 2) if isinstance(c, (int, float)) else c
        savings = round(s, 2) if isinstance(s, (int, float)) else s
    else:
        consumption = "Unknown"
        savings = "Unknown"

    area_scenes = _scenes_from_db_cache(db, area_id)
    # LMS-style: highlight only from live LEAP CurrentScene (null = no button selected).
    # Do not seed from DB cache on live path — stale current_scene_code (e.g. OFF) caused
    # false highlights while zones/light were On. Cache polls (live=False) do use cache.
    active_scene = _active_scene_from_cache(db, area_id) if not live else None
    zones = _zones_from_db_cache(db, area_id)
    light_status = _area_light_from_cache(db, area_id) or "Unknown"
    occupancy_status = _area_occupancy_from_cache(db, area_id) or "Unknown"

    def _payload():
        return {
            "status": "success",
            "floor_id": area.floor_id,
            "area_id": area.id,
            "area_name": area.name,
            "area_code": area.code,
            "light_status": light_status,
            "occupancy_status": occupancy_status,
            "active_scene": active_scene,
            "area_scenes": area_scenes,
            "zones": zones,
            "consumption": consumption,
            "savings": savings,
        }

    if not live:
        return _payload()

    if not processor or not is_processor_reachable(processor.ipv4):
        logger.warning(
            f"[Full Area Status] Processor unreachable for area {area_id}; serving cache"
        )
        return _payload()

    ssock = None
    try:
        ssock = connect_to_processor(
            processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4
        )
        if ssock is None:
            return _payload()

        # --- Area status: light + occupancy + active scene (one read) ---
        try:
            send_json(ssock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area.code}/status"}
            })
            status_response = recv_json(ssock)
            if isinstance(status_response, dict):
                area_status = (status_response.get("Body") or {}).get("AreaStatus", {}) or {}
                # Always take live CurrentScene (including None) — never keep a prior cached id.
                active_scene = _parse_active_scene_id(area_status)

                level = area_status.get("Level", 0)
                try:
                    level_n = float(level) if level is not None else 0
                except (TypeError, ValueError):
                    level_n = 0
                light_status = "On" if level_n > 0 else "Off"

                raw_occ = area_status.get("OccupancyStatus")
                if raw_occ in _VALID_OCCUPANCY:
                    occupancy_status = raw_occ
                # else keep cached Occupied/Unoccupied / Unknown
        except Exception as e:
            logger.warning(f"[Full Area Status] Area status read failed area {area_id}: {e}")

        # --- Scene list: live refresh (same idea as zone levels) so sidebar is not empty
        # when area_scenes cache was never filled (e.g. new deployment before floor sync).
        try:
            leap_scenes = _leap_read_area_scenes(ssock, area.code)
            if leap_scenes is not None:
                _upsert_area_scenes_cache(db, area_id, leap_scenes)
                area_scenes = leap_scenes
        except Exception as e:
            logger.warning(f"[Full Area Status] Scene list read failed area {area_id}: {e}")

        # --- Zone levels: live overlay on DB zone list (names/types from sync) ---
        try:
            send_json(ssock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area.code}/associatedzone/status"}
            })
            status_resp = recv_json(ssock)
            live_by_code = _parse_live_zone_levels_from_status_resp(status_resp)
            if live_by_code:
                zones = _overlay_live_zone_levels_on_zones(zones, live_by_code)
        except Exception as e:
            logger.warning(f"[Full Area Status] Zone status read failed area {area_id}: {e}")

    except Exception as e:
        logger.exception(f"[Full Area Status] LEAP assembly failed area {area_id}: {e}")
    finally:
        if ssock is not None:
            try:
                ssock.close()
            except Exception:
                pass

    return _payload()


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
        logger.warning(f"[Shade Zones] Processor {processor.ipv4} not reachable; using cache")
        return {"status": "success", "zones": _zones_from_db_cache(db, area_id, shade_only=True)}

    try:
        ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

        # Fetch zone metadata
        send_json(ssock, {
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": f"/area/{area.code}/associatedzone"}
        })
        metadata_resp = recv_json(ssock)
        if not isinstance(metadata_resp, dict):
            ssock.close()
            logger.warning(f"[Shade Zones] Empty LEAP metadata for area {area_id}; using cache")
            return {"status": "success", "zones": _zones_from_db_cache(db, area_id, shade_only=True)}

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
        if not isinstance(status_resp, dict):
            ssock.close()
            logger.warning(f"[Shade Zones] Empty LEAP status for area {area_id}; using cache")
            return {"status": "success", "zones": _zones_from_db_cache(db, area_id, shade_only=True)}

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
        logger.exception(f"[Shade Zones] Error for area {area_id}: {e}; using cache")
        return {"status": "success", "zones": _zones_from_db_cache(db, area_id, shade_only=True)}




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