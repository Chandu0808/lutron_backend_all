from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.area import Area
from app.models.processor import Processor
from app.models.area_group import AreaGroup, AreaGroupMapping
from app.utils.lutron_helpers import (
    is_processor_reachable,
    get_occupancy_mapping
)
from app.utils.json_connection import connect_to_processor, send_json, recv_json


def update_area_occupancy_setting(db: Session, area_id: int, mode: str):
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    if not is_processor_reachable(processor.ipv4):
        raise HTTPException(status_code=500, detail="Processor not reachable")

    sock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

    try:
        mapping = get_occupancy_mapping(sock, area.code)

        selected = mapping.get(mode)
        if not selected:
            raise HTTPException(status_code=400, detail="Occupancy mode not found in mapping")

        button_id = selected["button_id"]
        send_json(sock, {
            "CommuniqueType": "CreateRequest",
            "Header": {"Url": f"/button/{button_id}/commandprocessor"},
            "Body": {"Command": {"CommandType": "PressAndRelease"}}
        })
        resp = recv_json(sock)

        if "ExceptionResponse" in resp.get("CommuniqueType", ""):
            raise HTTPException(status_code=500, detail=f"Button press failed: {resp}")

        for m, info in mapping.items():
            if m == mode:
                continue
            led_id = info.get("led_id")
            if not led_id:
                continue
            send_json(sock, {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": f"/led/{led_id}/status"},
                "Body": {"LEDStatus": {"State": "Off"}}
            })
            _ = recv_json(sock)
    finally:
        sock.close()

    return {"status": "success", "message": f"Occupancy mode set to {mode}"}


def get_area_occupancy_setting(db: Session, area_id: int):
    """
    Returns the currently active occupancy mode for a single area.
    Checks each mode's LED and returns the one that is ON.
    """
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    if not is_processor_reachable(processor.ipv4):
        raise HTTPException(status_code=500, detail="Processor not reachable")

    sock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)
    try:
        mapping = get_occupancy_mapping(sock, area.code)

        for mode in ["Auto", "Disabled", "Vacancy"]:
            led_id = mapping.get(mode, {}).get("led_id")
            if not led_id:
                continue

            send_json(sock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/led/{led_id}/status"}
            })
            resp = recv_json(sock)
            led_state = resp.get("Body", {}).get("LEDStatus", {}).get("State")
            if led_state == "On":
                return mode

    finally:
        sock.close()

    return "Unknown"


def update_group_occupancy_setting(db: Session, group_id: int, mode: str):
    """
    Updates occupancy mode for all areas in a given area group.
    """
    group = db.query(AreaGroup).filter(AreaGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Area group not found")

    area_mappings = db.query(AreaGroupMapping).filter(AreaGroupMapping.group_id == group.id).all()
    if not area_mappings:
        raise HTTPException(status_code=404, detail="No areas found in this group")

    updated = []
    failed = []

    for mapping in area_mappings:
        try:
            update_area_occupancy_setting(db, mapping.area_id, mode)
            updated.append(mapping.area_id)
        except Exception:
            failed.append(mapping.area_id)
            continue

    return {
        "status": "partial_success" if failed else "success",
        "updated_area_ids": updated,
        "failed_area_ids": failed
    }


def get_area_group_occupancy_setting(db: Session, group_id: int):
    """
    Returns the group occupancy mode:
    - A common mode if all areas have same mode
    - "Mixed" if they differ
    - "Unknown" if none could be determined
    """
    group = db.query(AreaGroup).filter(AreaGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Area group not found")

    area_mappings = db.query(AreaGroupMapping).filter(AreaGroupMapping.group_id == group_id).all()
    if not area_mappings:
        raise HTTPException(status_code=404, detail="No areas in this group")

    modes = []

    for mapping in area_mappings:
        try:
            mode = get_area_occupancy_setting(db, mapping.area_id)
            modes.append(mode)
        except Exception:
            modes.append("Unknown")

    unique_modes = set(modes)

    if len(unique_modes) == 1:
        return list(unique_modes)[0]
    return "Mixed"
