import os
import tempfile
import csv
from io import StringIO
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.crud import alert, email_settings as email_crud
from app.models.processor import Processor
from app.models.drivers import Driver
from app.models.user_model import User
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.area import Area
from app.models.sensors_and_modules import SensorAndModule
from app.crud.widget_title import get_title_of_widget
from app.models.alert_type_display_settings import AlertTypeDisplaySetting
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope

router = APIRouter()


# ------------------- Helper ------------------- #
def format_datetime_to_ist(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert UTC datetime to IST (UTC+5:30) and format as string.
    NOTE: This function only converts for display purposes - database storage remains unchanged.
    
    Args:
        dt: Datetime object from database (UTC, timezone-aware or naive)
    
    Returns:
        Formatted string in IST timezone (DD-MM-YYYY HH.MM) or None if dt is None
    """
    if dt is None:
        return None
    
    # IST timezone (UTC+5:30)
    ist_timezone = timezone(timedelta(hours=5, minutes=30))
    
    # Handle both timezone-aware and naive datetimes
    # TIMESTAMP(timezone=True) returns timezone-aware, DateTime returns naive
    if dt.tzinfo is None:
        # Naive datetime - assume it's UTC (as stored in database)
        dt = dt.replace(tzinfo=timezone.utc)
    
    # Convert to IST (astimezone handles conversion correctly)
    ist_dt = dt.astimezone(ist_timezone)
    
    # Format as "DD-MM-YYYY HH.MM" (same format as before, just timezone converted)
    return ist_dt.strftime("%d-%m-%Y %H.%M")

def format_alert_type_for_csv(alert_type: str) -> str:
    """Format alert type for CSV output with proper capitalization."""
    alert_type_lower = alert_type.lower()
    if alert_type_lower == "processor not responding":
        return "Processor Not Responding"
    elif alert_type_lower == "device not responding":
        return "Device Not Responding"
    return alert_type


_DEFAULT_ALERT_TYPE_DISPLAY = {
    "Processor Not Responding": True,
    "Device Not Responding": True,
    "Ballast Failure": True,
    "Lamp Failure": True,
    "Other Warnings": True,
}


def _get_alert_type_display_map(db: Session):
    """
    Global alert visibility per alert type.
    Used to ensure disabling keeps working for alerts that arrive after the change.
    """
    type_map = dict(_DEFAULT_ALERT_TYPE_DISPLAY)
    rows = db.query(AlertTypeDisplaySetting).all()
    for r in rows:
        type_map[r.alert_type] = bool(r.display)
    return type_map


def get_area_full_path_from_processor(ip: str, mac: str, system: str, area_code: str) -> Optional[str]:
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


# ------------------- Device Discovery ------------------- #
@router.post("/discover_devices")
def discover_devices(
    processor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Discover all devices (sensors + modules) from the given processor and upsert into DB."""
    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found")

    devices = alert.discover_and_upsert_all_devices(
        db,
        ip=processor.ipv4,
        mac=processor.mac,
        system=processor.system,
    )
    return {"status": "success", "count": len(devices), "devices": devices}


# ------------------- Active Alerts ------------------- #
@router.get("/active_alerts")
def get_active_alerts(
    types: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch all active alerts (processors, devices, drivers)."""
    try:
        require_operator_permission_for_scope(
            required_level=1,
            area_ids=None,
            floor_ids=None,
            enforce_on_empty_scope=False,
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {"status": "failed", "message": "You don’t have permission to view alerts."}
        raise

    try:
        results = []
        allowed_floor_ids = []
        if current_user.role == "Operator":
            allowed_floor_ids = [perm.floor_id for perm in current_user.user_permissions]
        type_display_map = _get_alert_type_display_map(db)

        def include_type(alert_type: str) -> bool:
            if not types:
                return True
            # Case-insensitive comparison to handle "Processor Not Responding" vs "processor not responding"
            alert_type_lower = alert_type.lower()
            return any(t.lower() == alert_type_lower for t in types)

        # Processor Alerts
        if include_type("Processor Not Responding") and type_display_map.get("Processor Not Responding", True):
            q_processors = db.query(Processor).filter(
                Processor.ping_status == "not_ok",
                Processor.display.is_(True),
            )
            if current_user.role == "Operator":
                q_processors = q_processors.join(
                    FloorProcMapping, FloorProcMapping.processor_id == Processor.id
                ).filter(FloorProcMapping.floor_id.in_(allowed_floor_ids))

            for p in q_processors.all():
                location = None
                
                # Check if processor has associated_area
                if p.associated_area:
                    # Extract area code from href like "/area/1022"
                    area_code = p.associated_area.split("/")[-1] if "/" in p.associated_area else p.associated_area
                    
                    # Try to find area in database with processor_id context
                    area = db.query(Area).filter(Area.code == area_code, Area.processor_id == p.id).first()
                    
                    if area:
                        # Build location from database
                        location_parts = []
                        if area.floor and area.floor.name:
                            location_parts.append(area.floor.name)
                        if area.name:
                            location_parts.append(area.name)
                        location = "/".join(location_parts) if location_parts else None
                    else:
                        # Area not found in DB, fetch from processor
                        location = get_area_full_path_from_processor(p.ipv4, p.mac, p.system, area_code)
                
                results.append({
                    "location": location,
                    "alert_type": "processor not responding",
                    "device_name": p.system,
                    "serial_no": p.serial,
                    "model_number": p.model_number,
                    "description": "not pingable",
                    "time": format_datetime_to_ist(p.created_at),
                    "reported_time": format_datetime_to_ist(p.reported_time),
                    "solved_time": format_datetime_to_ist(p.solved_time),
                    "last_updated_time": format_datetime_to_ist(p.created_at),
                })

        # Device Alerts
        if include_type("Device Not Responding") and type_display_map.get("Device Not Responding", True):
            bad_devices = db.query(SensorAndModule).filter(
                SensorAndModule.alert_status == "not_ok",
                SensorAndModule.display.is_(True),
            ).all()
            for dev in bad_devices:
                location = None
                area = None
                
                # Try to get area from database if area_id exists
                if dev.area_id:
                    area = db.query(Area).filter(Area.id == dev.area_id).first()
                
                # Check operator permissions
                if area and current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                    continue
                
                # If area found in DB, build location from database
                if area:
                    location_parts = []
                    if area.floor and area.floor.name:
                        location_parts.append(area.floor.name)
                    if area.name:
                        location_parts.append(area.name)
                    location = "/".join(location_parts) if location_parts else None
                
                # If area not found in DB but we have area_code and processor_id, fetch from processor
                elif dev.area_code and dev.processor_id:
                    proc = db.query(Processor).filter(Processor.id == dev.processor_id).first()
                    if proc:
                        location = get_area_full_path_from_processor(
                            proc.ipv4, 
                            proc.mac, 
                            proc.system, 
                            str(dev.area_code)
                        )
                
                results.append({
                    "location": location,
                    "alert_type": "Device Not Responding",
                    "device_name": dev.device_name,
                    "serial_no": dev.serial_number,
                    "model_number": dev.device_model,
                    "description": "",
                    "time": format_datetime_to_ist(dev.created_at),
                    "reported_time": format_datetime_to_ist(dev.reported_time),
                    "solved_time": format_datetime_to_ist(dev.solved_time),
                    "last_updated_time": format_datetime_to_ist(dev.created_at),
                })

        # Driver Alerts
        driver_types = {"E2": "Ballast Failure", "FC": "Lamp Failure"}
        if include_type("Ballast Failure") or include_type("Lamp Failure") or include_type("Other Warnings"):
            drivers = db.query(Driver).filter(
                Driver.alert_status.in_(["not_ok", "not_okay"]),
                Driver.area_id.isnot(None),
                Driver.display.is_(True),
            ).all()
            for d in drivers:
                # Exclude driver rows with NULL/empty error_code from being classified
                # as "Other Warnings" (read-side only; recording logic unchanged).
                if d.error_code is None:
                    continue
                if isinstance(d.error_code, str) and d.error_code.strip() == "":
                    continue

                location = None
                area = None
                
                # Try to get area from database if area_id exists
                if d.area_id:
                    area = db.query(Area).filter(Area.id == d.area_id).first()
                
                # Check operator permissions
                if area and current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                    continue
                
                alert_type = driver_types.get(d.error_code, "Other Warnings")
                if not type_display_map.get(alert_type, True):
                    continue
                if not include_type(alert_type):
                    continue
                
                # If area found in DB, build location from database
                if area:
                    location_parts = []
                    if area.floor and area.floor.name:
                        location_parts.append(area.floor.name)
                    if area.name:
                        location_parts.append(area.name)
                    location = "/".join(location_parts) if location_parts else None
                
                # If area not found in DB but we have area_code and processor_id, fetch from processor
                elif d.area_code and d.processor_id:
                    proc = db.query(Processor).filter(Processor.id == d.processor_id).first()
                    if proc:
                        location = get_area_full_path_from_processor(proc.ipv4, proc.mac, proc.system, str(d.area_code))
                
                # Try to get model number from SensorAndModule table using device_code
                model_number = None
                serial_no = None
               
                
                results.append({
                    "location": location,
                    "alert_type": alert_type,
                    "device_name": d.device_name,
                    "serial_no": serial_no,
                    "model_number": model_number,
                    "description": d.description or "",
                    "time": format_datetime_to_ist(d.created_at),
                    "reported_time": format_datetime_to_ist(d.reported_time),
                    "solved_time": format_datetime_to_ist(d.solved_time),
                    "last_updated_time": format_datetime_to_ist(d.created_at),
                })

        return {"status": "success", "alerts": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------- Alert Types ------------------- #
@router.get("/alerts_types")
def get_alert_types(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all alert types currently active in the system."""
    try:
        require_operator_permission_for_scope(
            required_level=1,
            area_ids=None,
            floor_ids=None,
            enforce_on_empty_scope=False,
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            return {"status": "failed", "message": "You don’t have permission to view alert types."}
        raise

    try:
        alert_types = set()
        allowed_floor_ids = []
        if current_user.role == "Operator":
            allowed_floor_ids = [perm.floor_id for perm in current_user.user_permissions]
        type_display_map = _get_alert_type_display_map(db)

        # Processor
        q_proc = db.query(Processor).filter(
            Processor.ping_status == "not_ok",
            Processor.display.is_(True),
        )
        if current_user.role == "Operator":
            q_proc = q_proc.join(
                FloorProcMapping, FloorProcMapping.processor_id == Processor.id
            ).filter(FloorProcMapping.floor_id.in_(allowed_floor_ids))
        if q_proc.first() and type_display_map.get("Processor Not Responding", True):
            alert_types.add("Processor Not Responding")

        # Devices
        q_devices = db.query(SensorAndModule).filter(
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.display.is_(True),
        ).all()
        for dev in q_devices:
            # Use area_id if available
            area = None
            if dev.area_id:
                area = db.query(Area).filter(Area.id == dev.area_id).first()
            
            # Check operator permissions if area exists
            if area and current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                continue
            
            # Include devices even if area_id is null (they have area_code or processor_id)
            if type_display_map.get("Device Not Responding", True):
                alert_types.add("Device Not Responding")
            break

        # Drivers
        drivers = db.query(Driver).filter(
            Driver.alert_status.in_(["not_ok", "not_okay"]),
            Driver.area_id.isnot(None),
            Driver.display.is_(True),
        ).all()
        for d in drivers:
            # Exclude driver rows with NULL/empty error_code from dropdown types.
            if d.error_code is None:
                continue
            if isinstance(d.error_code, str) and d.error_code.strip() == "":
                continue

            # Use area_id if available
            area = None
            if d.area_id:
                area = db.query(Area).filter(Area.id == d.area_id).first()
            
            if not area:
                continue
            if current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                continue
            if d.error_code == "E2":
                if type_display_map.get("Ballast Failure", True):
                    alert_types.add("Ballast Failure")
            elif d.error_code == "FC":
                if type_display_map.get("Lamp Failure", True):
                    alert_types.add("Lamp Failure")
            else:
                if type_display_map.get("Other Warnings", True):
                    alert_types.add("Other Warnings")

        return {"status": "success", "alert_types": list(alert_types)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------- Download Alerts as CSV ------------------- #
@router.get("/active_alerts/download")
def download_active_alerts_csv(
    types: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download active alerts as CSV file."""
    try:
        data = get_active_alerts(types=types, db=db, current_user=current_user)
        if data.get("status") != "success":
            raise HTTPException(status_code=400, detail="Failed to fetch alerts")

        alerts = data.get("alerts", [])
        output = StringIO()
        writer = csv.writer(output)

        widget_key = "active_alerts"
        widget_title = get_title_of_widget(db, widget_key) or "System Alerts"

        writer.writerow(["Title", widget_title])
        writer.writerow([f"{len(alerts)} active alerts requiring attention"])
        writer.writerow([])
        writer.writerow(["Location", "Alert Type", "Device Name", "Serial No", "Model Number", "Description", "Date/Time"])

        for alert in alerts:
            writer.writerow([
                alert["location"] or "",
                format_alert_type_for_csv(alert["alert_type"]),
                alert["device_name"] or "",
                alert["serial_no"] or "",
                alert.get("model_number") or "",
                alert["description"] or "",
                alert.get("reported_time") or alert.get("time") or "",
            ])

        output.seek(0)
        filename = "active_alerts.csv"
        return StreamingResponse(
            output,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ------------------- Send Alerts by Email ------------------- #
@router.post("/active_alerts/send_by_email")
def send_active_alerts_email(
    to_email: str = Query(...),
    types: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send active alerts as CSV attachment by email."""
    try:
        data = get_active_alerts(types=types, db=db, current_user=current_user)
        if data.get("status") != "success":
            raise HTTPException(status_code=400, detail="Failed to fetch alerts")

        alerts = data.get("alerts", [])
        fd, temp_path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)

        widget_key = "active_alerts"
        widget_title = get_title_of_widget(db, widget_key) or "System Alerts"

        with open(temp_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Title", widget_title])
            writer.writerow([f"{len(alerts)} active alerts requiring attention"])
            writer.writerow([])
            writer.writerow(["Location", "Alert Type", "Device Name", "Serial No", "Model Number", "Description", "Date/Time"])

            for alert in alerts:
                writer.writerow([
                    alert["location"] or "",
                    format_alert_type_for_csv(alert["alert_type"]),
                    alert["device_name"] or "",
                    alert["serial_no"] or "",
                    alert.get("model_number") or "",
                    alert["description"] or "",
                    alert.get("reported_time") or alert.get("time") or "",
                ])

        success = email_crud.send_email(
            db=db,
            to_email=to_email,
            subject=f"{widget_title} Report",
            body=f"Please find attached the {widget_title} report with {len(alerts)} active alerts.",
            is_html=False,
            attachment_path=temp_path,
        )

        os.remove(temp_path)
        if not success:
            raise HTTPException(status_code=500, detail="CSV generated but email sending failed.")
        return {"status": "success", "message": "Email sent successfully with CSV report."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
