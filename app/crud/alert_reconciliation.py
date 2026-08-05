"""
Live alert reconciliation — keep dashboard/API/FOFP aligned with LEAP current state.

Active = failing now, not yet solved, visible, and (for drivers) has a real error_code.
Ghosts are cleared by comparing DB not_ok drivers to a full /loadcontroller/status inventory.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.drivers import Driver
from app.models.processor import Processor
from app.models.sensors_and_modules import SensorAndModule
from app.utils.json_connection import connect_to_processor, recv_json, send_json

logger = logging.getLogger("alert_reconciliation")

_DRIVER_BAD = ("not_ok", "not_okay")


def active_processor_filter_clauses():
    """Processor rows that should appear as active alerts."""
    return (
        Processor.ping_status == "not_ok",
        Processor.display.is_(True),
        Processor.solved_time.is_(None),
    )


def active_device_filter_clauses():
    """Device (sensor/module) rows that should appear as active alerts."""
    return (
        SensorAndModule.alert_status == "not_ok",
        SensorAndModule.display.is_(True),
        SensorAndModule.solved_time.is_(None),
    )


def active_driver_filter_clauses():
    """
    Driver rows that should appear as active alerts.

    Requires non-empty error_code so NULL/blank codes are not shown as Other Warnings.
    """
    return (
        Driver.alert_status.in_(_DRIVER_BAD),
        Driver.area_id.isnot(None),
        Driver.display.is_(True),
        Driver.solved_time.is_(None),
        Driver.error_code.isnot(None),
        Driver.error_code != "",
    )


def clear_driver_alert(driver: Driver) -> None:
    """Mark a driver alert resolved (keep history via solved_time)."""
    from app.crud.alert import update_alert_timestamps

    update_alert_timestamps(driver, "ok")
    driver.error_code = None
    driver.description = None


def _status_has_error(status: dict) -> Tuple[bool, Optional[str], Optional[str]]:
    """Return (is_error, code, description) for one LoadControllerStatuses entry."""
    if not isinstance(status, dict):
        return False, None, None
    error_info = status.get("ErrorStatus") or {}
    if not isinstance(error_info, dict):
        error_info = {}
    code = error_info.get("ErrorCode")
    desc = error_info.get("Description")
    if code == "Unknown" or desc == "Unknown":
        return False, None, None
    empty_code = not code or (isinstance(code, str) and code.strip() == "")
    empty_desc = not desc or (isinstance(desc, str) and desc.strip() == "")
    if empty_code and empty_desc:
        return False, None, None
    return True, code, desc


def _lc_code_from_status(status: dict) -> Optional[int]:
    href = status.get("href") if isinstance(status, dict) else None
    if not href:
        return None
    try:
        return int(href.strip("/").split("/")[-2])
    except (ValueError, IndexError, AttributeError, TypeError):
        return None


def dedupe_active_drivers_for_lc(
    db: Session, processor_id: int, loadcontroller_code: int, keep: Optional[Driver] = None
) -> int:
    """
    Ensure at most one not_ok driver row per (processor_id, loadcontroller_code).
    Keeps `keep` or the newest row; clears the rest.
    """
    rows = (
        db.query(Driver)
        .filter(
            Driver.processor_id == processor_id,
            Driver.loadcontroller_code == loadcontroller_code,
            Driver.alert_status.in_(_DRIVER_BAD),
            Driver.solved_time.is_(None),
        )
        .order_by(Driver.id.desc())
        .all()
    )
    if not rows:
        return 0
    if keep is None:
        keep = rows[0]
    cleared = 0
    for row in rows:
        if row.id == keep.id:
            continue
        clear_driver_alert(row)
        cleared += 1
    return cleared


# Back-compat alias
_dedupe_active_drivers_for_lc = dedupe_active_drivers_for_lc


def reconcile_drivers_from_status_batch(
    db: Session,
    processor_id: int,
    statuses: Iterable[dict],
    *,
    full_snapshot: bool = False,
) -> Dict[str, int]:
    """
    Apply a LEAP LoadControllerStatuses batch.

    When full_snapshot=True, any DB not_ok driver for this processor whose LC is
    absent from the batch (or present without ErrorStatus) is cleared.
    Partial deltas must pass full_snapshot=False so missing LCs are not wiped.
    """
    status_list = [s for s in (statuses or []) if isinstance(s, dict)]
    live_error_lcs: Set[int] = set()
    seen_lcs: Set[int] = set()

    for status in status_list:
        lc = _lc_code_from_status(status)
        if lc is None:
            continue
        seen_lcs.add(lc)
        is_err, _code, _desc = _status_has_error(status)
        if is_err:
            live_error_lcs.add(lc)
            # Prefer a single active row for this LC
            keep = (
                db.query(Driver)
                .filter(
                    Driver.processor_id == processor_id,
                    Driver.loadcontroller_code == lc,
                )
                .order_by(Driver.id.desc())
                .first()
            )
            if keep is not None:
                dedupe_active_drivers_for_lc(db, processor_id, lc, keep=keep)

    cleared_healthy = 0
    cleared_orphan = 0

    if full_snapshot:
        bad_drivers = (
            db.query(Driver)
            .filter(
                Driver.processor_id == processor_id,
                Driver.alert_status.in_(_DRIVER_BAD),
                Driver.solved_time.is_(None),
            )
            .all()
        )
        for driver in bad_drivers:
            lc = driver.loadcontroller_code
            if lc is None:
                clear_driver_alert(driver)
                cleared_orphan += 1
                continue
            if lc not in seen_lcs:
                # LC gone from full inventory → orphan
                clear_driver_alert(driver)
                cleared_orphan += 1
            elif lc not in live_error_lcs:
                # Present but healthy in this snapshot
                clear_driver_alert(driver)
                cleared_healthy += 1

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "live_errors": len(live_error_lcs),
        "cleared_healthy": cleared_healthy,
        "cleared_orphan": cleared_orphan,
        "seen_loadcontrollers": len(seen_lcs),
    }


def reconcile_all_processors_from_leap(db: Session) -> Dict[str, Any]:
    """
    ReadRequest /loadcontroller/status for each handshake processor and
    full-snapshot reconcile DB driver alerts.
    """
    processors = db.query(Processor).filter_by(handshake_status=True).all()
    summary: Dict[str, Any] = {
        "processors": 0,
        "live_errors": 0,
        "cleared_healthy": 0,
        "cleared_orphan": 0,
        "errors": [],
    }

    for proc in processors:
        sock = None
        try:
            sock = connect_to_processor(
                ip=proc.ipv4, mac=proc.mac, system=proc.system, processor_ipv4=proc.ipv4
            )
            if not sock:
                summary["errors"].append({"processor_id": proc.id, "error": "connect_failed"})
                continue
            send_json(
                sock,
                {"CommuniqueType": "ReadRequest", "Header": {"Url": "/loadcontroller/status"}},
            )
            resp = recv_json(sock) or {}
            body = resp.get("Body") or {}
            statuses = body.get("LoadControllerStatuses") or []
            if not isinstance(statuses, list):
                statuses = []
            result = reconcile_drivers_from_status_batch(
                db, proc.id, statuses, full_snapshot=True
            )
            summary["processors"] += 1
            summary["live_errors"] += result.get("live_errors", 0)
            summary["cleared_healthy"] += result.get("cleared_healthy", 0)
            summary["cleared_orphan"] += result.get("cleared_orphan", 0)
        except Exception as exc:
            logger.warning("reconcile processor %s failed: %s", proc.id, exc)
            summary["errors"].append({"processor_id": proc.id, "error": str(exc)})
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    return summary


def clear_devices_not_in_inventory(
    db: Session, processor_id: int, seen_device_codes: Set[Any]
) -> int:
    """
    After a full device discovery for one processor, clear not_ok devices that
    were not present in this inventory (ghost Unavailable rows).
    """
    if not seen_device_codes:
        return 0
    # Normalize to comparable strings
    seen = {str(c) for c in seen_device_codes if c is not None}
    bad = (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.processor_id == processor_id,
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.solved_time.is_(None),
        )
        .all()
    )
    cleared = 0
    from app.crud.alert import update_alert_timestamps

    for dev in bad:
        code = str(dev.device_code) if dev.device_code is not None else None
        if code is None or code not in seen:
            update_alert_timestamps(dev, "ok")
            cleared += 1
    return cleared


def dedupe_active_devices_by_serial(db: Session, processor_id: int) -> int:
    """
    Keep at most one active (not_ok, unsolved) device row per serial_number
    on a processor. Prefer newest id.
    """
    from collections import defaultdict
    from app.crud.alert import update_alert_timestamps

    bad = (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.processor_id == processor_id,
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.solved_time.is_(None),
            SensorAndModule.serial_number.isnot(None),
            SensorAndModule.serial_number != "",
        )
        .order_by(SensorAndModule.id.desc())
        .all()
    )
    by_serial: Dict[str, List[SensorAndModule]] = defaultdict(list)
    for row in bad:
        by_serial[str(row.serial_number).upper()].append(row)
    cleared = 0
    for rows in by_serial.values():
        if len(rows) < 2:
            continue
        for extra in rows[1:]:
            update_alert_timestamps(extra, "ok")
            cleared += 1
    return cleared
