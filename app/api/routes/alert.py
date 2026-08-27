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
from app.crud.alert_reconciliation import (
    active_device_filter_clauses,
    active_driver_filter_clauses,
    active_processor_filter_clauses,
    reconcile_all_processors_from_leap,
)
from app.utils.alert_area_path import AreaPathResolver, sanitize_stored_path, area_alert_scope

router = APIRouter()


def require_admin_or_superadmin(user: User = Depends(get_current_user)) -> User:
    """Admin or Superadmin may force a live LEAP driver-alert reconcile."""
    if user.role not in ("Admin", "Superadmin"):
        raise HTTPException(
            status_code=403,
            detail="This action is restricted to Admin or Superadmin.",
        )
    return user


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
        path_resolver = AreaPathResolver(db)

        def include_type(alert_type: str) -> bool:
            if not types:
                return True
            # Case-insensitive comparison to handle "Processor Not Responding" vs "processor not responding"
            alert_type_lower = alert_type.lower()
            return any(t.lower() == alert_type_lower for t in types)

        # Processor Alerts
        if include_type("Processor Not Responding") and type_display_map.get("Processor Not Responding", True):
            q_processors = db.query(Processor).filter(
                *active_processor_filter_clauses(),
            )
            if current_user.role == "Operator":
                q_processors = q_processors.join(
                    FloorProcMapping, FloorProcMapping.processor_id == Processor.id
                ).filter(FloorProcMapping.floor_id.in_(allowed_floor_ids))

            for p in q_processors.all():
                location = None
                area = None
                associated_code = None

                if p.associated_area:
                    associated_code = (
                        p.associated_area.split("/")[-1]
                        if "/" in p.associated_area
                        else p.associated_area
                    )
                    area = db.query(Area).filter(
                        Area.code == associated_code, Area.processor_id == p.id
                    ).first()
                    scope = area_alert_scope(area, area_code=associated_code)
                    location = path_resolver.resolve(
                        area, None, area_code=scope["area_code"]
                    )
                    if not location:
                        location = sanitize_stored_path(
                            get_area_full_path_from_processor(
                                p.ipv4, p.mac, p.system, associated_code
                            )
                        )
                else:
                    scope = area_alert_scope(None)

                results.append({
                    "location": location,
                    **scope,
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
            from app.utils.system_identity import (
                heal_duplicate_active_device_alerts,
                partition_active_device_alert_duplicates,
                processor_system_key_map,
            )

            # Soft-heal Athena twins (Shade 3 / SL (5) on two processors) before listing.
            try:
                healed = heal_duplicate_active_device_alerts(db)
                if healed:
                    db.commit()
            except Exception as heal_exc:
                db.rollback()
                print(f"[active_alerts] device duplicate heal skipped: {heal_exc}")

            bad_devices = db.query(SensorAndModule).filter(
                *active_device_filter_clauses(),
            ).all()
            survivors, _losers = partition_active_device_alert_duplicates(
                bad_devices, processor_system_key_map(db)
            )
            for dev in survivors:
                area = path_resolver.get_area(dev.area_id) if dev.area_id else None
                if area is None and getattr(dev, "area_code", None):
                    area = path_resolver.get_area_by_code(
                        dev.area_code, getattr(dev, "processor_id", None)
                    )

                if area and current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                    continue

                scope = area_alert_scope(
                    area, area_id=dev.area_id, area_code=dev.area_code
                )
                location = path_resolver.resolve(
                    area, getattr(dev, "area_path", None), area_code=scope["area_code"]
                )

                results.append({
                    "location": location,
                    **scope,
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
                *active_driver_filter_clauses(),
            ).all()
            for d in drivers:
                area = path_resolver.get_area(d.area_id) if d.area_id else None
                if area is None and getattr(d, "area_code", None):
                    area = path_resolver.get_area_by_code(
                        d.area_code, getattr(d, "processor_id", None)
                    )

                if area and current_user.role == "Operator" and area.floor_id not in allowed_floor_ids:
                    continue

                alert_type = driver_types.get(d.error_code, "Other Warnings")
                if not type_display_map.get(alert_type, True):
                    continue
                if not include_type(alert_type):
                    continue

                scope = area_alert_scope(
                    area, area_id=d.area_id, area_code=d.area_code
                )
                location = path_resolver.resolve(
                    area, getattr(d, "area_path", None), area_code=scope["area_code"]
                )

                model_number = None
                serial_no = None

                results.append({
                    "location": location,
                    **scope,
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
    """
    Return alert types enabled in Settings (display=true).

    The Alerts page filter must list every type the operator opted to monitor,
    not only types that currently have an active row — otherwise the dropdown
    shrinks to whatever is failing right now (e.g. only Device Not Responding).
    """
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
        type_display_map = _get_alert_type_display_map(db)
        ordered_types = [
            "Processor Not Responding",
            "Device Not Responding",
            "Ballast Failure",
            "Lamp Failure",
            "Other Warnings",
        ]
        alert_types = [t for t in ordered_types if type_display_map.get(t, True)]
        return {"status": "success", "alert_types": alert_types}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reconcile_live")
def reconcile_live_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_superadmin),
):
    """
    Force a full LEAP /loadcontroller/status inventory reconcile for all
    handshake processors. Idempotent; clears orphan/stale driver alerts only.
    """
    try:
        summary = reconcile_all_processors_from_leap(db)
        return {"status": "success", "summary": summary}
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
