"""
Dashboard home helpers: new functions only (no changes to existing code).
Used by app/crud/dashboard_home.py for alerts top-5 and next schedule.
"""
from datetime import datetime, time, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.processor import Processor
from app.models.drivers import Driver
from app.models.area import Area
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.sensors_and_modules import SensorAndModule
from app.models.alert_type_display_settings import AlertTypeDisplaySetting
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.crud.schedule import fetch_combined_schedules


def _format_datetime_to_ist(dt: Optional[datetime]) -> Optional[str]:
    """Convert UTC datetime to IST (UTC+5:30) and format as string."""
    if dt is None:
        return None
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ist_tz).strftime("%d-%m-%Y %H.%M")


_DEFAULT_ALERT_TYPE_DISPLAY = {
    "Processor Not Responding": True,
    "Device Not Responding": True,
    "Ballast Failure": True,
    "Lamp Failure": True,
    "Other Warnings": True,
}


def _get_alert_type_display_map(db: Session) -> Dict[str, bool]:
    """Global alert visibility per alert type."""
    type_map: Dict[str, bool] = dict(_DEFAULT_ALERT_TYPE_DISPLAY)
    rows = db.query(AlertTypeDisplaySetting).all()
    for r in rows:
        type_map[r.alert_type] = bool(r.display)
    return type_map


def _get_area_full_path_from_processor(
    ip: str, mac: str, system: str, area_code: str
) -> Optional[str]:
    """Resolve full area path from processor via LEAP traversal."""
    if not area_code:
        return None
    sock = None
    try:
        sock = connect_to_processor(ip=ip, mac=mac, system=system, processor_ipv4=ip)
        if not sock:
            return None
        path_parts = []
        current_href = f"/area/{area_code}"
        while current_href:
            send_json(sock, {"CommuniqueType": "ReadRequest", "Header": {"Url": current_href}})
            resp = recv_json(sock)
            area = resp.get("Body", {}).get("Area")
            if not area:
                break
            name = area.get("Name")
            if name:
                path_parts.insert(0, name)
            parent_href = area.get("Parent", {}).get("href")
            current_href = parent_href if parent_href else None
        return "/".join(path_parts)
    except Exception:
        return None
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass


def get_active_alerts_list_for_dashboard(
    db: Session, current_user: Any
) -> List[Dict[str, Any]]:
    """
    Build list of active alert dicts for dashboard (same structure as GET /alert/active_alerts).
    Returns all alerts; dashboard crud slices to top 5.
    """
    results = []
    allowed_floor_ids = []
    if getattr(current_user, "role", None) == "Operator":
        allowed_floor_ids = [
            p.floor_id for p in getattr(current_user, "user_permissions", [])
            if getattr(p, "floor_id", None) is not None
        ]

    type_display_map = _get_alert_type_display_map(db)

    # Processor Alerts
    if type_display_map.get("Processor Not Responding", True):
        q_processors = db.query(Processor).filter(
            Processor.ping_status == "not_ok",
            Processor.display.is_(True),
        )
        if getattr(current_user, "role", None) == "Operator":
            q_processors = q_processors.join(
                FloorProcMapping, FloorProcMapping.processor_id == Processor.id
            ).filter(FloorProcMapping.floor_id.in_(allowed_floor_ids))
        for p in q_processors.all():
            location = None
            if p.associated_area:
                area_code = p.associated_area.split("/")[-1] if "/" in p.associated_area else p.associated_area
                area = db.query(Area).filter(Area.code == area_code, Area.processor_id == p.id).first()
                if area:
                    location_parts = []
                    if area.floor and area.floor.name:
                        location_parts.append(area.floor.name)
                    if area.name:
                        location_parts.append(area.name)
                    location = "/".join(location_parts) if location_parts else None
                else:
                    location = _get_area_full_path_from_processor(p.ipv4, p.mac, p.system, area_code)
            results.append({
                "location": location,
                "alert_type": "processor not responding",
                "device_name": p.system,
                "serial_no": p.serial,
                "model_number": p.model_number,
                "description": "not pingable",
                "time": _format_datetime_to_ist(p.created_at),
                "reported_time": _format_datetime_to_ist(p.reported_time),
                "solved_time": _format_datetime_to_ist(p.solved_time),
                "last_updated_time": _format_datetime_to_ist(p.created_at),
            })

    # Device Alerts
    if type_display_map.get("Device Not Responding", True):
        bad_devices = db.query(SensorAndModule).filter(
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.display.is_(True),
        ).all()
        for dev in bad_devices:
            location = None
            area = None
            if dev.area_id:
                area = db.query(Area).filter(Area.id == dev.area_id).first()
            if area and getattr(current_user, "role", None) == "Operator" and area.floor_id not in allowed_floor_ids:
                continue
            if area:
                location_parts = []
                if area.floor and area.floor.name:
                    location_parts.append(area.floor.name)
                if area.name:
                    location_parts.append(area.name)
                location = "/".join(location_parts) if location_parts else None
            elif getattr(dev, "area_code", None) and getattr(dev, "processor_id", None):
                proc = db.query(Processor).filter(Processor.id == dev.processor_id).first()
                if proc:
                    location = _get_area_full_path_from_processor(
                        proc.ipv4, proc.mac, proc.system, str(dev.area_code)
                    )
            results.append({
                "location": location,
                "alert_type": "Device Not Responding",
                "device_name": dev.device_name,
                "serial_no": dev.serial_number,
                "model_number": dev.device_model,
                "description": "",
                "time": _format_datetime_to_ist(dev.created_at),
                "reported_time": _format_datetime_to_ist(dev.reported_time),
                "solved_time": _format_datetime_to_ist(dev.solved_time),
                "last_updated_time": _format_datetime_to_ist(dev.created_at),
            })

    # Driver Alerts
    driver_types = {"E2": "Ballast Failure", "FC": "Lamp Failure"}
    drivers = db.query(Driver).filter(
        Driver.alert_status.in_(["not_ok", "not_okay"]),
        Driver.area_id.isnot(None),
        Driver.display.is_(True),
    ).all()
    for d in drivers:
        # Exclude driver rows with NULL/empty error_code from being shown as
        # "Other Warnings" on the dashboard.
        if d.error_code is None:
            continue
        if isinstance(d.error_code, str) and d.error_code.strip() == "":
            continue

        location = None
        area = None
        if d.area_id:
            area = db.query(Area).filter(Area.id == d.area_id).first()
        if area and getattr(current_user, "role", None) == "Operator" and area.floor_id not in allowed_floor_ids:
            continue
        alert_type = driver_types.get(d.error_code, "Other Warnings")
        if not type_display_map.get(alert_type, True):
            continue
        if area:
            location_parts = []
            if area.floor and area.floor.name:
                location_parts.append(area.floor.name)
            if area.name:
                location_parts.append(area.name)
            location = "/".join(location_parts) if location_parts else None
        elif getattr(d, "area_code", None) and getattr(d, "processor_id", None):
            proc = db.query(Processor).filter(Processor.id == d.processor_id).first()
            if proc:
                location = _get_area_full_path_from_processor(
                    proc.ipv4, proc.mac, proc.system, str(d.area_code)
                )
        results.append({
            "location": location,
            "alert_type": alert_type,
            "device_name": d.device_name,
            "serial_no": getattr(d, "serial_number", None),
            "model_number": getattr(d, "device_model", None),
            "description": d.description or "",
            "time": _format_datetime_to_ist(d.created_at),
            "reported_time": _format_datetime_to_ist(d.reported_time),
            "solved_time": _format_datetime_to_ist(d.solved_time),
            "last_updated_time": _format_datetime_to_ist(d.created_at),
        })

    return results


def _parse_time_of_day(time_dict: Optional[Dict]) -> Optional[time]:
    """Parse time_of_day dict; supports Hour/Minute/Second and hour/minute/second."""
    if not time_dict or not isinstance(time_dict, dict):
        return None
    h = time_dict.get("hour") if "hour" in time_dict else time_dict.get("Hour", 0)
    m = time_dict.get("minute") if "minute" in time_dict else time_dict.get("Minute", 0)
    s = time_dict.get("second") if "second" in time_dict else time_dict.get("Second", 0)
    try:
        return time(hour=int(h), minute=int(m), second=int(s or 0))
    except (TypeError, ValueError):
        return None


def get_next_schedule_occurrence(
    db: Session, current_user: Any = None
) -> Optional[Dict[str, str]]:
    """
    Get the next upcoming schedule run from combined schedules (uses existing fetch_combined_schedules).
    Returns {"name": str, "time": "12:00 pm", "date": "Aug 31"} or None.
    """
    result = fetch_combined_schedules(db)
    if result.get("status") != "success":
        return None

    internal = result.get("internal_schedules", [])
    preconfigured = result.get("preconfigured_schedules", [])
    tz_ist = timedelta(hours=5, minutes=30)
    now = datetime.utcnow() + tz_ist
    candidates = []
    day_name_to_weekday = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
        "Friday": 4, "Saturday": 5, "Sunday": 6,
    }

    def add_candidate(next_dt: datetime, name: str):
        if next_dt and next_dt > now:
            candidates.append((next_dt, name))

    for s in internal:
        if s.get("EnableState") != "Enabled":
            continue
        name = s.get("name") or "Schedule"
        tod = _parse_time_of_day(s.get("time_of_day"))
        if not tod:
            continue
        if s.get("schedule_type") == "DayOfWeek" and s.get("days"):
            active_days = [
                day_name_to_weekday[d]
                for d, active in s["days"].items()
                if active and d in day_name_to_weekday
            ]
            for day_offset in range(8):
                d = now.date() + timedelta(days=day_offset)
                if d.weekday() in active_days:
                    run = datetime(d.year, d.month, d.day, tod.hour, tod.minute, tod.second)
                    if run > now:
                        add_candidate(run, name)
                        break
        elif s.get("schedule_type") == "SpecificDates" and s.get("specific_dates"):
            for entry in s["specific_dates"]:
                try:
                    y = entry.get("year") or entry.get("Year")
                    m = entry.get("month") or entry.get("Month")
                    day = entry.get("day") or entry.get("Day")
                    run = datetime(y, m, day, tod.hour, tod.minute, tod.second)
                    add_candidate(run, name)
                except (TypeError, ValueError, KeyError):
                    continue

    for s in preconfigured:
        if s.get("EnableState") != "Enabled":
            continue
        name = s.get("name") or "Schedule"
        tod = _parse_time_of_day(s.get("time_of_day"))
        if not tod:
            continue
        days = s.get("days") or {}
        active_days = [
            day_name_to_weekday[d]
            for d, active in days.items()
            if active and d in day_name_to_weekday
        ]
        if active_days:
            for day_offset in range(8):
                d = now.date() + timedelta(days=day_offset)
                if d.weekday() in active_days:
                    run = datetime(d.year, d.month, d.day, tod.hour, tod.minute, tod.second)
                    if run > now:
                        add_candidate(run, name)
                        break

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    next_dt, name = candidates[0]
    hour12 = next_dt.hour % 12 or 12
    am_pm = "am" if next_dt.hour < 12 else "pm"
    time_str = f"{hour12}:{next_dt.minute:02d} {am_pm}"
    date_str = next_dt.strftime("%b %d")
    return {"name": name, "time": time_str, "date": date_str}
