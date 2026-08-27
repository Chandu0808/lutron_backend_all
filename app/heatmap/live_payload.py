"""
Build WebSocket payloads from listener DB cache (live=False CRUD paths).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.crud.area import assemble_full_area_status
from app.crud.floor import (
    get_area_light_status_by_floor,
    get_area_occupancy_status_by_floor,
)


def build_floor_light_message(db: Session, floor_id: int) -> Optional[Dict[str, Any]]:
    result = get_area_light_status_by_floor(db, floor_id, live=False)
    if result.get("status") != "success":
        return None
    return {"type": "floor_light", "payload": result}


def build_floor_occupancy_message(db: Session, floor_id: int) -> Optional[Dict[str, Any]]:
    result = get_area_occupancy_status_by_floor(db, floor_id, live=False)
    if result.get("status") != "success":
        return None
    return {"type": "floor_occupancy", "payload": result}


def build_area_status_message(db: Session, area_id: int) -> Optional[Dict[str, Any]]:
    result = assemble_full_area_status(db, area_id, live=False)
    if result.get("status") != "success":
        return None
    return {"type": "area_status", "payload": result}


def _normalize_light_status(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"on", "true"}:
        return "on"
    if text in {"off", "false"}:
        return "off"
    return None


def _zone_level(zone: Dict[str, Any]) -> Optional[int]:
    zone_type = str(zone.get("type") or zone.get("zone_type") or "").strip().lower()
    if zone_type == "shade":
        return None
    if zone_type == "switched":
        state = str(
            zone.get("switched_state") or zone.get("status") or zone.get("on_off") or ""
        ).strip().lower()
        if state == "on":
            return 100
        if state == "off":
            return 0
        return None
    raw = zone.get("brightness", zone.get("level"))
    if raw is None:
        return None
    try:
        return max(0, min(100, int(round(float(str(raw).replace("%", ""))))))
    except (TypeError, ValueError):
        return None


def _light_level_from_area_status(payload: Dict[str, Any]) -> Optional[int]:
    levels = [
        level
        for level in (_zone_level(zone) for zone in (payload.get("zones") or []))
        if level is not None
    ]
    if levels:
        return max(levels)
    light = _normalize_light_status(payload.get("light_status"))
    if light == "off":
        return 0
    if light == "on":
        return 100
    return None


def build_floor_light_message_for_area(db: Session, area_id: int) -> Optional[Dict[str, Any]]:
    result = assemble_full_area_status(db, area_id, live=False)
    if result.get("status") != "success":
        return None
    payload = {
        "status": "success",
        "areas": [
            {
                "id": result["area_id"],
                "light_status": _normalize_light_status(result.get("light_status")),
                "light_level": _light_level_from_area_status(result),
                "processor_reachable": True,
            }
        ],
    }
    return {"type": "floor_light", "payload": payload}


def build_floor_occupancy_message_for_area(db: Session, area_id: int) -> Optional[Dict[str, Any]]:
    result = assemble_full_area_status(db, area_id, live=False)
    if result.get("status") != "success":
        return None
    payload = {
        "status": "success",
        "areas": [
            {
                "id": result["area_id"],
                "occupancy_status": result.get("occupancy_status"),
                "processor_reachable": True,
            }
        ],
    }
    return {"type": "floor_occupancy", "payload": payload}
