"""
30-minute alert reconciliation (drivers + sensors_and_modules).

Connection-safe vs Lutron max 10 LEAP clients:
- One short-lived socket per processor for the whole pass
- Drivers: ReadRequest /loadcontroller/status — clear if ErrorCode "" or LC missing
- Sensors: ReadRequest /device/status/availability — clear if no longer Unavailable
- Unmapped (no area_path): DB map first; optional single LC Read on same sock;
  never open unbounded connections; emit monitoring metric for remaining unmapped
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from app.crud.alert import update_alert_timestamps
from app.database.session import SessionLocal
from app.models.drivers import Driver
from app.models.processor import Processor
from app.models.sensors_and_modules import SensorAndModule
from app.utils.alert_area_path import (
    map_area_from_codes,
    map_loadcontroller_from_db,
    should_attempt_area_map,
)
from app.utils.json_connection import connect_to_processor, recv_json, send_json

logger = logging.getLogger("alert_reconcile")


def _lc_code_from_status_href(href: Any) -> Optional[int]:
    if not href or not isinstance(href, str):
        return None
    try:
        return int(href.strip("/").split("/")[-2])
    except (ValueError, IndexError, TypeError):
        return None


def _device_code_from_status(dev: dict) -> Optional[int]:
    raw = dev.get("Device")
    href = raw.get("href") if isinstance(raw, dict) else raw
    if not href or not isinstance(href, str):
        return None
    try:
        return int(href.strip("/").split("/")[-1])
    except (ValueError, IndexError, TypeError):
        return None


def _error_cleared(error_info: Any) -> bool:
    if not isinstance(error_info, dict):
        error_info = {}
    code = error_info.get("ErrorCode")
    desc = error_info.get("Description")
    return (not code or (isinstance(code, str) and code.strip() == "")) and (
        not desc or (isinstance(desc, str) and desc.strip() == "")
    )


def _emit_unmapped_metric(count: int) -> None:
    try:
        from app.monitoring import instrumentation

        instrumentation.metric(
            "alerts.unmapped_count",
            float(count),
            component_code="scheduler",
            detail={"source": "alert_reconcile"},
        )
    except Exception:
        pass


def _leap_map_one_lc(sock, db, alert: Driver) -> bool:
    """
    One ReadRequest /loadcontroller/{id} on an existing short sock.
    Prefer DB map; LEAP only fills zone/device when zone row missing.
    """
    lc = alert.loadcontroller_code
    if lc is None or alert.processor_id is None:
        return False

    area_id, area_code, zone_code, area_path = map_loadcontroller_from_db(
        db, alert.processor_id, int(lc)
    )
    if area_path:
        alert.area_id = area_id or alert.area_id
        if area_code is not None:
            alert.area_code = area_code
        if zone_code is not None:
            alert.zone_code = zone_code
        alert.area_path = area_path
        alert.area_map_failures = 0
        return True

    # LEAP fallback on THIS short connection only (not subscribe socket)
    try:
        send_json(
            sock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/loadcontroller/{int(lc)}"},
            },
        )
        resp = recv_json(sock)
        lc_body = ((resp or {}).get("Body") or {}).get("LoadController") or {}
        if not isinstance(lc_body, dict):
            return False

        name = lc_body.get("Name")
        if isinstance(name, str) and name and not alert.device_name:
            alert.device_name = name

        assoc_zone = lc_body.get("AssociatedZone")
        zone_href = assoc_zone.get("href") if isinstance(assoc_zone, dict) else assoc_zone
        if isinstance(zone_href, str) and zone_href.strip("/"):
            try:
                zc = int(zone_href.strip("/").split("/")[-1])
            except (ValueError, IndexError):
                zc = None
            if zc is not None:
                alert.zone_code = zc
                from app.models.zone import Zone
                from app.models.area import Area
                from app.utils.alert_area_path import build_area_path

                zone = (
                    db.query(Zone)
                    .filter(Zone.processor_id == alert.processor_id, Zone.code == str(zc))
                    .first()
                )
                if zone and zone.area_id:
                    area = db.query(Area).filter(Area.id == zone.area_id).first()
                    path = build_area_path(area, db=db)
                    if path:
                        alert.area_id = area.id
                        try:
                            alert.area_code = int(area.code) if area.code is not None else alert.area_code
                        except (TypeError, ValueError):
                            pass
                        alert.area_path = path
                        alert.area_map_failures = 0
                        return True

        assoc_dev = lc_body.get("AssociatedDevice")
        dev_href = assoc_dev.get("href") if isinstance(assoc_dev, dict) else assoc_dev
        if isinstance(dev_href, str) and dev_href.strip("/"):
            try:
                alert.device_code = int(dev_href.strip("/").split("/")[-1])
            except (ValueError, IndexError):
                pass
    except Exception as e:
        logger.warning("LEAP map failed for LC %s: %s", lc, e)

    return False


def _reconcile_processor_drivers(db, proc: Processor, sock) -> Tuple[int, int, int]:
    """Returns (cleared, mapped, still_unmapped)."""
    cleared = mapped = 0

    send_json(
        sock,
        {"CommuniqueType": "ReadRequest", "Header": {"Url": "/loadcontroller/status"}},
    )
    resp = recv_json(sock)
    body = (resp or {}).get("Body") or {}
    statuses = body.get("LoadControllerStatuses") or []
    if not isinstance(statuses, list):
        statuses = []

    present: Dict[int, dict] = {}
    for st in statuses:
        if not isinstance(st, dict):
            continue
        code = _lc_code_from_status_href(st.get("href"))
        if code is not None:
            present[code] = st

    active = (
        db.query(Driver)
        .filter(
            Driver.processor_id == proc.id,
            Driver.alert_status.in_(["not_ok", "not_okay"]),
        )
        .all()
    )

    for alert in active:
        lc = alert.loadcontroller_code
        if lc is None:
            continue

        if int(lc) not in present:
            update_alert_timestamps(alert, "okay")
            alert.error_code = None
            alert.description = None
            cleared += 1
            continue

        st = present[int(lc)]
        if _error_cleared(st.get("ErrorStatus")):
            update_alert_timestamps(alert, "okay")
            alert.error_code = None
            alert.description = None
            cleared += 1
            continue

        if should_attempt_area_map(alert.area_path, alert.area_map_failures):
            if _leap_map_one_lc(sock, db, alert):
                mapped += 1
            else:
                alert.area_map_failures = int(alert.area_map_failures or 0) + 1

    still = (
        db.query(Driver)
        .filter(
            Driver.processor_id == proc.id,
            Driver.alert_status.in_(["not_ok", "not_okay"]),
            Driver.area_path.is_(None),
        )
        .count()
    )
    return cleared, mapped, still


def _reconcile_processor_sensors(db, proc: Processor, sock) -> Tuple[int, int, int]:
    """Returns (cleared, mapped, still_unmapped)."""
    cleared = mapped = 0

    send_json(
        sock,
        {"CommuniqueType": "ReadRequest", "Header": {"Url": "/device/status/availability"}},
    )
    resp = recv_json(sock)
    body = (resp or {}).get("Body") or {}
    statuses = body.get("DeviceAvailabilityStatuses") or []
    if not isinstance(statuses, list):
        statuses = []

    by_code: Dict[int, str] = {}
    for st in statuses:
        if not isinstance(st, dict):
            continue
        dc = _device_code_from_status(st)
        if dc is None:
            continue
        by_code[dc] = st.get("Availability") or "Unknown"

    active = (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.processor_id == proc.id,
            SensorAndModule.alert_status == "not_ok",
        )
        .all()
    )

    for dev in active:
        avail = by_code.get(dev.device_code)
        if avail is None:
            update_alert_timestamps(dev, "ok")
            dev.availability = "Available"
            cleared += 1
            continue
        if avail != "Unavailable":
            update_alert_timestamps(dev, "ok")
            dev.availability = avail
            cleared += 1
            continue

        dev.availability = "Unavailable"
        if should_attempt_area_map(dev.area_path, dev.area_map_failures):
            area_id, path = map_area_from_codes(db, dev.processor_id, dev.area_code)
            if path:
                if area_id:
                    dev.area_id = area_id
                dev.area_path = path
                dev.area_map_failures = 0
                mapped += 1
            else:
                dev.area_map_failures = int(dev.area_map_failures or 0) + 1

    still = (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.processor_id == proc.id,
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.area_path.is_(None),
        )
        .count()
    )
    return cleared, mapped, still


def reconcile_alerts() -> Dict[str, Any]:
    """
    Entry point for APScheduler. One short LEAP connection per processor.
    """
    db = SessionLocal()
    summary = {
        "status": "success",
        "processors": 0,
        "drivers_cleared": 0,
        "drivers_mapped": 0,
        "sensors_cleared": 0,
        "sensors_mapped": 0,
        "unmapped_total": 0,
        "errors": [],
    }
    try:
        processors = db.query(Processor).filter_by(handshake_status=True).all()
        summary["processors"] = len(processors)
        unmapped_total = 0

        for proc in processors:
            sock = None
            try:
                sock = connect_to_processor(
                    ip=proc.ipv4,
                    mac=proc.mac,
                    system=proc.system,
                    processor_ipv4=proc.ipv4,
                    timeout=30,
                )
                if not sock:
                    summary["errors"].append(f"processor {proc.id}: connect failed")
                    continue
                try:
                    sock.settimeout(60)
                except Exception:
                    pass

                d_cleared, d_mapped, d_still = _reconcile_processor_drivers(db, proc, sock)
                s_cleared, s_mapped, s_still = _reconcile_processor_sensors(db, proc, sock)
                summary["drivers_cleared"] += d_cleared
                summary["drivers_mapped"] += d_mapped
                summary["sensors_cleared"] += s_cleared
                summary["sensors_mapped"] += s_mapped
                unmapped_total += d_still + s_still
                db.commit()
            except Exception as e:
                db.rollback()
                summary["errors"].append(f"processor {proc.id}: {e}")
                logger.exception("alert_reconcile failed for processor %s", proc.id)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        summary["unmapped_total"] = unmapped_total
        _emit_unmapped_metric(unmapped_total)
        if unmapped_total > 0:
            logger.warning(
                "alert_reconcile: %s active alerts still missing area_path (hidden in UI)",
                unmapped_total,
            )
        return summary
    except Exception as e:
        summary["status"] = "error"
        summary["errors"].append(str(e))
        logger.exception("alert_reconcile fatal")
        return summary
    finally:
        db.close()
