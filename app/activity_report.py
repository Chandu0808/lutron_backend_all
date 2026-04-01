from datetime import datetime
from sqlalchemy.orm import Session
from app.models.zone import Zone
from app.models.area import Area
from app.models.activity_report import ActivityReport
from app.models.events import ProcessorZoneEvent, CurrentAreaEvent


from datetime import datetime
from sqlalchemy.orm import Session
from app.models.zone import Zone
from app.models.activity_report import ActivityReport
from app.models.events import ProcessorZoneEvent
from app.models.area import Area
from app.models.processor import Processor


from datetime import datetime
from sqlalchemy.orm import Session
from app.models.zone import Zone
from app.models.activity_report import ActivityReport
from app.models.events import ProcessorZoneEvent
from app.models.area import Area


def get_area_full_path(db: Session, area: Area) -> str:
    """
    Build full hierarchical path for an area.
    Example: "Ground Floor / Living Room / Sub Area"
    """
    parts = []
    current = area
    while current:
        parts.append(current.name)
        current = current.parent if hasattr(current, "parent") else None
    parts.reverse()

    # prepend floor name if available
    if hasattr(area, "floor") and area.floor:
        parts.insert(0, area.floor.name)

    return " / ".join(parts)

def log_activity_report_for_zone(db: Session, zone_event: ProcessorZoneEvent) -> None:
    """
    Create ActivityReport entry/entries when a new ProcessorZoneEvent is inserted.
    Handles switched, dimmer, whitetune, and shade with proper enrichment.
    Skips logging if zone type cannot be classified.
    """
    zone = None
    if zone_event.zone_code:
        q = db.query(Zone).filter(Zone.code == str(zone_event.zone_code))
        if getattr(zone_event, "processor_id", None) is not None:
            q = q.filter(Zone.processor_id == zone_event.processor_id)
        zone = q.first()

    desc = None
    activity_type = None
    area_name = None
    area_id = None

    if zone:
        zone_type = (zone.type or "").strip().lower()
        zone_label = zone.name

        # Switched zones → On/Off
        if zone_type == "switched":
            if zone_event.switched_level is not None:
                state = str(zone_event.switched_level).strip().lower()
                if state in ("on", "off"):
                    desc = f"Switched state changed to {state.capitalize()} in {zone_label}"
                else:
                    desc = f"Switched state changed in {zone_label}"
            else:
                desc = f"Switched state changed in {zone_label}"
            activity_type = "Lights"

        # Dimmer zones → level
        elif zone_type in ("dimmer", "dimmed"):
            if zone_event.level is not None:
                desc = f"Light intensity changed to {zone_event.level}% in {zone_label}"
            else:
                desc = f"Light intensity changed in {zone_label}"
            activity_type = "Lights"

        # Whitetune zones → level + kelvin
        elif zone_type in ("whitetune", "white tune", "white_tuning"):
            if zone_event.level is not None:
                desc = f"Light intensity changed to {zone_event.level}%"
            else:
                desc = "Light intensity changed"
            if zone_event.white_tuning_kelvin is not None:
                desc += f" and color temperature set to {zone_event.white_tuning_kelvin}K"
            desc += f" in {zone_label}"
            activity_type = "Lights"

        # Shade zones
        elif zone_type == "shade":
            if zone_event.level is not None:
                desc = f"Shade level changed to {zone_event.level}% in {zone_label}"
            else:
                desc = f"Shade level changed in {zone_label}"
            activity_type = "Shades"

        # Skip unknown types
        else:
            return

        area_name = get_area_full_path(db, zone.area) if zone.area else None
        area_id = zone.area_id

    else:
        # No zone info found → print for debugging
        return

    base_fields = {
        "date": datetime.now().date(),
        "time": datetime.now().strftime("%H:%M"),
        "area_id": area_id,
        "area_name": area_name,
        "activity_desc": desc,
    }

    try:
        # Main activity
        db.add(ActivityReport(activity_type=activity_type, **base_fields))

        # If triggered by a button, log as Device Control too
        if zone_event.button_activity:
            db.add(ActivityReport(activity_type="Device Control", **base_fields))

        db.commit()
    except Exception:
        db.rollback()


def log_activity_report_for_area(db: Session, current_area_event: CurrentAreaEvent, change_type: str) -> None:
    """
    Log Area activity (occupancy or scene changes) from CurrentAreaEvent.
    Only log when the value actually changes (caller should check before calling).
    """
    area = db.query(Area).filter(Area.id == current_area_event.area_id).first()
    area_name = get_area_full_path(db, area) if area else None

    if change_type == "Occupancy":
        activity_type = "Occupancy"
        desc = f"Occupancy status changed to {current_area_event.occupancy_status}"

    elif change_type == "Scene":
        activity_type = "Scene"
        desc = "Scene changed"

        # Try to fetch scene name directly from processor
        if current_area_event.current_scene_href:
            try:
                scene_id = int(current_area_event.current_scene_href.strip("/").split("/")[-1])
                processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
                if processor:
                    from app.utils.json_connection import connect_to_processor, send_json, recv_json
                    ssock = connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4)

                    send_json(ssock, {
                        "CommuniqueType": "ReadRequest",
                        "Header": {"Url": f"/areascene/{scene_id}"}
                    })
                    response = recv_json(ssock)
                    ssock.close()

                    scene_obj = response.get("Body", {}).get("AreaScene", {})
                    scene_name = scene_obj.get("Name")
                    if scene_name:
                        desc = f"Scene changed to {scene_name}"
            except Exception as e:
                pass

    else:
        activity_type = "Area"
        desc = "Area activity detected"

    report = ActivityReport(
        date=datetime.now().date(),
        time=datetime.now().strftime("%H:%M"),
        area_id=current_area_event.area_id,
        area_name=area_name,
        activity_type=activity_type,
        activity_desc=desc,
    )

    try:
        db.add(report)
        db.commit()
    except Exception:
        db.rollback()

