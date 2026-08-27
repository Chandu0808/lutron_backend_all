"""
System-wide device identity for multi-processor Athena projects.

Processors in the same Lutron project share a device database. LMS keys rows by
(system_key, device_code) / (system_key, loadcontroller_code) so the same
physical device is not stored (and alerted) once per processor.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.models.drivers import Driver
from app.models.processor import Processor
from app.models.sensors_and_modules import SensorAndModule


def derive_system_key_from_project(project_payload: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Hash ProductType + Name from a LEAP /project body.

    MasterDeviceList is deliberately excluded — it changes when processors are
    added and would split one system into multiple keys.
    """
    if not isinstance(project_payload, dict):
        return None, None
    body = project_payload.get("Body") if "Body" in project_payload else project_payload
    if not isinstance(body, dict):
        return None, None
    project = body.get("Project") if isinstance(body.get("Project"), dict) else body
    if not isinstance(project, dict):
        return None, None

    product_type = str(project.get("ProductType") or "").strip()
    name = str(project.get("Name") or "").strip()
    if not name:
        return None, None

    digest = hashlib.sha256(f"{product_type}|{name}".encode("utf-8")).hexdigest()[:32]
    return f"sys:{digest}", name


def processor_fallback_key(serial: Optional[str]) -> Optional[str]:
    """Isolate an unnamed / unreachable processor rather than merging unrelated ones."""
    if serial is None:
        return None
    text = str(serial).strip()
    if not text:
        return None
    return f"proc:{text}"


def apply_project_identity(
    processor: Processor,
    project_payload: Any,
    *,
    fallback_serial: Optional[str] = None,
) -> Optional[str]:
    """Set processor.system_key / project_name from /project (or proc:serial fallback)."""
    key, name = derive_system_key_from_project(project_payload)
    if not key:
        key = processor_fallback_key(fallback_serial or getattr(processor, "serial", None))
    if key:
        processor.system_key = key
    if name:
        processor.project_name = name
    return key


def system_processor_ids(db: Session, processor: Processor) -> List[int]:
    """All processor ids that share this processor's system_key (or just itself)."""
    key = getattr(processor, "system_key", None)
    if not key:
        return [processor.id] if processor.id is not None else []
    rows = (
        db.query(Processor.id)
        .filter(Processor.system_key == key)
        .all()
    )
    ids = [r[0] for r in rows if r and r[0] is not None]
    return ids or ([processor.id] if processor.id is not None else [])


def _earliest_reported(*values: Any) -> Any:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return min(present)


def _device_has_area(row: SensorAndModule) -> bool:
    if getattr(row, "area_id", None) is not None:
        return True
    path = getattr(row, "area_path", None)
    return bool(path and str(path).strip())


def _driver_has_area(row: Driver) -> bool:
    if getattr(row, "area_id", None) is not None:
        return True
    path = getattr(row, "area_path", None)
    return bool(path and str(path).strip())


def _pick_device_survivor(rows: Sequence[SensorAndModule]) -> SensorAndModule:
    # Stable survivor by earliest id so merge can adopt another row's processor_id
    # after losers are deleted (flush-order safe).
    return min(rows, key=lambda r: r.id if r.id is not None else 10**12)


def _pick_driver_survivor(rows: Sequence[Driver]) -> Driver:
    return min(rows, key=lambda r: r.id if r.id is not None else 10**12)


def collapse_duplicate_devices(db: Session, system_key: Optional[str]) -> int:
    """
    Merge sensors_and_modules rows that share (system_key, device_code).

    Flush ordering: delete losers and flush before writing the survivor's
    processor_id (otherwise UPDATE-before-DELETE hits the legacy unique index).
    """
    if not system_key:
        return 0

    rows = (
        db.query(SensorAndModule)
        .filter(SensorAndModule.system_key == system_key)
        .order_by(SensorAndModule.device_code, SensorAndModule.id)
        .all()
    )
    by_code: Dict[Any, List[SensorAndModule]] = {}
    for row in rows:
        by_code.setdefault(row.device_code, []).append(row)

    merged = 0
    for _code, group in by_code.items():
        if len(group) < 2:
            continue
        survivor = _pick_device_survivor(group)
        losers = [r for r in group if r.id != survivor.id]

        earliest = survivor.reported_time
        prefer_area = survivor if _device_has_area(survivor) else None
        for loser in losers:
            earliest = _earliest_reported(earliest, loser.reported_time)
            if prefer_area is None and _device_has_area(loser):
                prefer_area = loser

        merge_processor_id = (
            prefer_area.processor_id if prefer_area is not None else survivor.processor_id
        )
        merge_area_id = prefer_area.area_id if prefer_area is not None else survivor.area_id
        merge_area_code = (
            prefer_area.area_code if prefer_area is not None else survivor.area_code
        )
        merge_area_path = (
            prefer_area.area_path if prefer_area is not None else survivor.area_path
        )
        merge_serial = survivor.serial_number
        merge_name = survivor.device_name
        merge_model = survivor.device_model
        merge_type = survivor.device_type
        merge_availability = survivor.availability
        merge_alert = survivor.alert_status
        for loser in losers:
            if not merge_serial and loser.serial_number:
                merge_serial = loser.serial_number
            if not merge_name and loser.device_name:
                merge_name = loser.device_name
            if not merge_model and loser.device_model:
                merge_model = loser.device_model
            if not merge_type and loser.device_type:
                merge_type = loser.device_type
            if loser.alert_status == "not_ok":
                merge_alert = "not_ok"
                merge_availability = loser.availability or merge_availability

        for loser in losers:
            db.delete(loser)
        db.flush()

        survivor.processor_id = merge_processor_id
        survivor.area_id = merge_area_id
        survivor.area_code = merge_area_code
        survivor.area_path = merge_area_path
        survivor.serial_number = merge_serial
        survivor.device_name = merge_name
        survivor.device_model = merge_model
        survivor.device_type = merge_type
        survivor.availability = merge_availability
        survivor.alert_status = merge_alert
        survivor.reported_time = earliest
        survivor.system_key = system_key
        merged += len(losers)

    return merged


def collapse_duplicate_drivers(db: Session, system_key: Optional[str]) -> int:
    """Merge drivers that share (system_key, loadcontroller_code). Same flush ordering."""
    if not system_key:
        return 0

    rows = (
        db.query(Driver)
        .filter(Driver.system_key == system_key)
        .order_by(Driver.loadcontroller_code, Driver.id)
        .all()
    )
    by_lc: Dict[Any, List[Driver]] = {}
    for row in rows:
        if row.loadcontroller_code is None:
            continue
        by_lc.setdefault(row.loadcontroller_code, []).append(row)

    merged = 0
    for _lc, group in by_lc.items():
        if len(group) < 2:
            continue
        survivor = _pick_driver_survivor(group)
        losers = [r for r in group if r.id != survivor.id]

        earliest = survivor.reported_time
        prefer_area = survivor if _driver_has_area(survivor) else None
        for loser in losers:
            earliest = _earliest_reported(earliest, loser.reported_time)
            if prefer_area is None and _driver_has_area(loser):
                prefer_area = loser

        merge_processor_id = (
            prefer_area.processor_id if prefer_area is not None else survivor.processor_id
        )
        merge_area_id = prefer_area.area_id if prefer_area is not None else survivor.area_id
        merge_area_code = (
            prefer_area.area_code if prefer_area is not None else survivor.area_code
        )
        merge_area_path = (
            prefer_area.area_path if prefer_area is not None else survivor.area_path
        )
        merge_error = survivor.error_code
        merge_desc = survivor.description
        merge_alert = survivor.alert_status
        for loser in losers:
            if loser.alert_status in ("not_ok", "not_okay"):
                merge_alert = loser.alert_status
                merge_error = loser.error_code or merge_error
                merge_desc = loser.description or merge_desc

        for loser in losers:
            db.delete(loser)
        db.flush()

        survivor.processor_id = merge_processor_id
        survivor.area_id = merge_area_id
        survivor.area_code = merge_area_code
        survivor.area_path = merge_area_path
        survivor.error_code = merge_error
        survivor.description = merge_desc
        survivor.alert_status = merge_alert
        survivor.reported_time = earliest
        survivor.system_key = system_key
        merged += len(losers)

    return merged


def backfill_row_system_keys_from_processors(db: Session) -> int:
    """Copy processor.system_key onto device/driver rows that still lack one."""
    updated = 0
    processors = {
        p.id: p.system_key
        for p in db.query(Processor).all()
        if p.system_key
    }
    if not processors:
        return 0

    for row in db.query(SensorAndModule).filter(SensorAndModule.system_key.is_(None)).all():
        key = processors.get(row.processor_id)
        if key:
            row.system_key = key
            updated += 1

    for row in db.query(Driver).filter(Driver.system_key.is_(None)).all():
        key = processors.get(row.processor_id)
        if key:
            row.system_key = key
            updated += 1

    return updated


def collapse_all_systems(db: Session) -> Dict[str, int]:
    """Run device/driver collapse for every distinct system_key present."""
    keys = set()
    for (key,) in db.query(SensorAndModule.system_key).filter(
        SensorAndModule.system_key.isnot(None)
    ).distinct():
        keys.add(key)
    for (key,) in db.query(Driver.system_key).filter(Driver.system_key.isnot(None)).distinct():
        keys.add(key)
    for (key,) in db.query(Processor.system_key).filter(Processor.system_key.isnot(None)).distinct():
        keys.add(key)

    devices = 0
    drivers = 0
    for key in keys:
        devices += collapse_duplicate_devices(db, key)
        drivers += collapse_duplicate_drivers(db, key)
    return {"devices_merged": devices, "drivers_merged": drivers, "systems": len(keys)}


def processor_system_key_map(db: Session) -> Dict[int, str]:
    return {
        int(p.id): p.system_key
        for p in db.query(Processor).all()
        if p.id is not None and p.system_key
    }


def _normalize_device_code(code: Any) -> Any:
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return code


def _device_code_serial(row: SensorAndModule) -> Optional[Tuple[Any, str]]:
    code_norm = _normalize_device_code(row.device_code)
    serial = (getattr(row, "serial_number", None) or "").strip().upper()
    if code_norm is None or not serial:
        return None
    return (code_norm, serial)


def _build_code_serial_system_map(
    rows: Sequence[SensorAndModule],
    processor_keys: Dict[int, str],
) -> Dict[Tuple[Any, str], str]:
    """
    Map (device_code, serial) → system_key from any keyed row in the batch.

    Lets unkeyed orphan twins (processor with no system_key) join the same
    identity bucket as their keyed sibling without merging on serial alone.
    """
    out: Dict[Tuple[Any, str], str] = {}
    for row in rows:
        sk = getattr(row, "system_key", None) or processor_keys.get(row.processor_id)
        if not sk:
            continue
        pair = _device_code_serial(row)
        if pair is not None:
            out.setdefault(pair, sk)
    return out


def _device_alert_identity_key(
    row: SensorAndModule,
    processor_keys: Dict[int, str],
    code_serial_to_sys: Optional[Dict[Tuple[Any, str], str]] = None,
) -> Tuple[Any, ...]:
    """
    Identity for active-alert dedupe / heal.

    Prefer (system_key, device_code). Fall back to processor.system_key when the
    row is still unkeyed. If still unkeyed, adopt a sibling's system_key when the
    same (device_code, serial) appears on a keyed row in the batch. Never use
    serial alone.
    """
    code_norm = _normalize_device_code(row.device_code)
    pair = _device_code_serial(row)

    sk = getattr(row, "system_key", None) or processor_keys.get(row.processor_id)
    if not sk and pair is not None and code_serial_to_sys:
        sk = code_serial_to_sys.get(pair)

    if sk and code_norm is not None:
        return ("sys", sk, code_norm)

    if pair is not None:
        # Cross-processor orphans before either row is keyed: same LEAP code + serial.
        return ("code_serial", pair[0], pair[1])

    if code_norm is not None and row.processor_id is not None:
        return ("proc", int(row.processor_id), code_norm)
    return ("id", row.id)


def _pick_alert_device_survivor(rows: Sequence[SensorAndModule]) -> SensorAndModule:
    def sort_key(r: SensorAndModule):
        has_area = 0 if _device_has_area(r) else 1
        reported = r.reported_time
        # Earliest reported_time keeps true alert age; missing sorts last.
        if reported is None:
            reported_ord = datetime.max
        else:
            # Normalize tz-aware to naive for comparison with datetime.max
            reported_ord = reported.replace(tzinfo=None) if getattr(reported, "tzinfo", None) else reported
        rid = r.id if r.id is not None else 10**12
        return (has_area, reported_ord, rid)

    return min(rows, key=sort_key)


def partition_active_device_alert_duplicates(
    rows: Sequence[SensorAndModule],
    processor_keys: Optional[Dict[int, str]] = None,
) -> Tuple[List[SensorAndModule], List[SensorAndModule]]:
    """
    Split active device alert rows into survivors and duplicate losers.

    Used by GET /alert/active_alerts and dashboard top alerts so Shade 3 / SL (5)
    Athena twins do not appear twice. Key is (system_key, device_code) when known;
    unkeyed orphans with the same device_code+serial as a keyed sibling join that
    system bucket.
    """
    from collections import defaultdict

    keys = processor_keys or {}
    code_serial_to_sys = _build_code_serial_system_map(rows, keys)
    groups: Dict[Tuple[Any, ...], List[SensorAndModule]] = defaultdict(list)
    for row in rows:
        groups[_device_alert_identity_key(row, keys, code_serial_to_sys)].append(row)

    survivors: List[SensorAndModule] = []
    losers: List[SensorAndModule] = []
    for group in groups.values():
        if len(group) == 1:
            survivors.append(group[0])
            continue
        survivor = _pick_alert_device_survivor(group)
        survivors.append(survivor)
        losers.extend(r for r in group if r.id != survivor.id)
    return survivors, losers


def heal_duplicate_active_device_alerts(db: Session) -> int:
    """
    Clear alert_status on duplicate active device rows (Athena twins).

    Soft-heal (mark ok) so the alerts list drops twins immediately even when
    hard DELETE collapse has not yet removed the orphan row.
    """
    from app.crud.alert import update_alert_timestamps

    bad = (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.alert_status == "not_ok",
            SensorAndModule.solved_time.is_(None),
            SensorAndModule.display.is_(True),
        )
        .all()
    )
    processor_keys = processor_system_key_map(db)
    _survivors, losers = partition_active_device_alert_duplicates(bad, processor_keys)
    for loser in losers:
        update_alert_timestamps(loser, "ok")
    return len(losers)


def find_device_for_upsert(
    db: Session,
    *,
    device_code: int,
    processor_id: int,
    system_key: Optional[str],
) -> Optional[SensorAndModule]:
    """Prefer system-scoped identity; fall back to legacy processor scope."""
    if system_key:
        row = (
            db.query(SensorAndModule)
            .filter(
                SensorAndModule.system_key == system_key,
                SensorAndModule.device_code == device_code,
            )
            .order_by(SensorAndModule.id.asc())
            .first()
        )
        if row is not None:
            return row
    return (
        db.query(SensorAndModule)
        .filter(
            SensorAndModule.processor_id == processor_id,
            SensorAndModule.device_code == device_code,
        )
        .first()
    )


def find_driver_for_upsert(
    db: Session,
    *,
    loadcontroller_code: int,
    processor_id: int,
    system_key: Optional[str],
) -> Optional[Driver]:
    if system_key:
        row = (
            db.query(Driver)
            .filter(
                Driver.system_key == system_key,
                Driver.loadcontroller_code == loadcontroller_code,
            )
            .order_by(Driver.id.desc())
            .first()
        )
        if row is not None:
            return row
    return (
        db.query(Driver)
        .filter(
            Driver.processor_id == processor_id,
            Driver.loadcontroller_code == loadcontroller_code,
        )
        .order_by(Driver.id.desc())
        .first()
    )
