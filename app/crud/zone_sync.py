from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.area import Area
from app.models.floor import Floor
from app.models.floor_proc_mapping import FloorProcMapping
from app.models.processor import Processor
from app.models.zone import Zone
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


@dataclass
class ZoneSyncResult:
    status: str  # success / partial / error
    floor_id: int
    areas_total: int
    areas_synced: int
    areas_skipped: int
    areas_renamed: int
    zones_created: int
    zones_updated: int
    zones_deleted: int
    errors: list[dict[str, Any]]


def parse_zone_entries(metadata_zones: Any) -> list[dict[str, str]]:
    """
    Parse Body.Zones returned by /area/{code}/associatedzone into normalized dicts.
    Expected entries are dict-like with an href like "/zone/{code}".
    """
    zones = metadata_zones or []
    out: list[dict[str, str]] = []

    if not isinstance(zones, list):
        return out

    for z in zones:
        if not isinstance(z, dict):
            continue
        href = z.get("href") or ""
        if not isinstance(href, str) or "/" not in href:
            continue
        zone_code = href.strip("/").split("/")[-1]
        if not zone_code:
            continue

        name = z.get("Name") or f"Zone {zone_code}"
        ctype = z.get("ControlType") or z.get("Type") or "Unknown"
        out.append({"code": str(zone_code), "name": str(name), "type": str(ctype)})

    return out


def apply_zone_metadata_for_area(
    db: Session,
    area: Area,
    metadata_zones: Iterable[dict[str, str]],
) -> dict[str, int]:
    """
    Upsert/delete zones for a single Area.
    Identity is (processor_id, code). Commits are handled by caller.
    """
    created = updated = deleted = 0
    desired_by_code: dict[str, dict[str, str]] = {}
    for z in metadata_zones or []:
        if not isinstance(z, dict):
            continue
        code = z.get("code")
        if code is None:
            continue
        desired_by_code[str(code)] = z

    existing = (
        db.query(Zone)
        .filter(Zone.processor_id == area.processor_id, Zone.area_id == area.id)
        .all()
    )
    existing_by_code = {str(z.code): z for z in existing}

    # Delete zones that are no longer associated with this area.
    # FOFP (Step 8): mark placements unavailable before delete so historical
    # layout rows survive (zone_id becomes NULL via ON DELETE SET NULL).
    zones_to_remove = [
        z.id for code, z in existing_by_code.items() if code not in desired_by_code
    ]
    if zones_to_remove:
        try:
            from app.crud.fofp_health import mark_positions_for_zones_pending_removal

            mark_positions_for_zones_pending_removal(db, zones_to_remove)
        except Exception:
            pass

    for code, z in existing_by_code.items():
        if code not in desired_by_code:
            db.delete(z)
            deleted += 1

    # Upsert desired zones
    for code, payload in desired_by_code.items():
        existing_zone = (
            db.query(Zone)
            .filter(Zone.processor_id == area.processor_id, Zone.code == str(code))
            .first()
        )
        if existing_zone:
            changed = False
            if existing_zone.area_id != area.id:
                existing_zone.area_id = area.id
                changed = True
            if existing_zone.processor_id != area.processor_id:
                existing_zone.processor_id = area.processor_id
                changed = True
            if payload.get("name") is not None and existing_zone.name != payload.get("name"):
                existing_zone.name = payload["name"]
                changed = True
            if payload.get("type") is not None and existing_zone.type != payload.get("type"):
                existing_zone.type = payload["type"]
                changed = True
            if changed:
                updated += 1
        else:
            db.add(
                Zone(
                    code=str(code),
                    name=str(payload.get("name") or f"Zone {code}"),
                    type=str(payload.get("type") or "Unknown"),
                    area_id=area.id,
                    processor_id=area.processor_id,
                )
            )
            created += 1

    return {"created": created, "updated": updated, "deleted": deleted}


def sync_zones_for_floor(db: Session, floor_id: int) -> dict[str, Any]:
    floor = db.query(Floor).filter(Floor.id == floor_id).first()
    if not floor:
        return {
            "status": "error",
            "floor_id": floor_id,
            "areas_total": 0,
            "areas_synced": 0,
            "areas_skipped": 0,
            "areas_renamed": 0,
            "zones_created": 0,
            "zones_updated": 0,
            "zones_deleted": 0,
            "errors": [{"area_id": None, "reason": "Floor not found"}],
        }

    mappings = db.query(FloorProcMapping).filter(FloorProcMapping.floor_id == floor_id).all()
    processor_ids = sorted({m.processor_id for m in mappings if getattr(m, "processor_id", None) is not None})

    result = ZoneSyncResult(
        status="success",
        floor_id=floor_id,
        areas_total=0,
        areas_synced=0,
        areas_skipped=0,
        areas_renamed=0,
        zones_created=0,
        zones_updated=0,
        zones_deleted=0,
        errors=[],
    )

    for processor_id in processor_ids:
        processor = db.query(Processor).filter(Processor.id == processor_id).first()
        if not processor:
            result.status = "partial"
            result.errors.append({"area_id": None, "reason": f"Processor not found: {processor_id}"})
            continue

        areas = (
            db.query(Area)
            .filter(Area.floor_id == floor_id, Area.processor_id == processor_id)
            .all()
        )
        result.areas_total += len(areas)

        ssock = create_ssl_connection(
            processor.ipv4,
            processor.mac,
            processor.system,
            processor_ipv4=processor.ipv4,
        )
        if not ssock:
            result.status = "partial"
            for a in areas:
                result.areas_skipped += 1
                result.errors.append({"area_id": a.id, "reason": f"Failed to connect to processor {processor_id}"})
            continue

        try:
            for area in areas:
                try:
                    if not area.code:
                        result.areas_skipped += 1
                        result.status = "partial"
                        result.errors.append({"area_id": area.id, "reason": "Area.code is missing"})
                        continue

                    # Refresh Area name from processor (best-effort)
                    try:
                        send_json(
                            ssock,
                            {"CommuniqueType": "ReadRequest", "Header": {"Url": f"/area/{area.code}"}},
                        )
                        area_resp = recv_json(ssock) or {}
                        area_body = (area_resp.get("Body") or {}).get("Area")
                        if isinstance(area_body, dict):
                            new_name = area_body.get("Name")
                            if isinstance(new_name, str):
                                new_name = new_name.strip()
                            if isinstance(new_name, str) and new_name and new_name != (area.name or ""):
                                area.name = new_name
                                result.areas_renamed += 1
                    except Exception:
                        # Don't fail zone sync if area name refresh fails
                        pass

                    send_json(
                        ssock,
                        {"CommuniqueType": "ReadRequest", "Header": {"Url": f"/area/{area.code}/associatedzone"}},
                    )
                    resp = recv_json(ssock) or {}
                    zones_raw = (resp.get("Body") or {}).get("Zones") or []
                    parsed = parse_zone_entries(zones_raw)

                    counts = apply_zone_metadata_for_area(db=db, area=area, metadata_zones=parsed)
                    db.commit()  # commit after each area for partial success

                    result.areas_synced += 1
                    result.zones_created += counts["created"]
                    result.zones_updated += counts["updated"]
                    result.zones_deleted += counts["deleted"]
                except Exception as e:
                    db.rollback()
                    result.status = "partial"
                    result.areas_skipped += 1
                    result.errors.append({"area_id": area.id, "reason": str(e)})
        finally:
            try:
                ssock.close()
            except Exception:
                pass

    if result.status != "success" and result.areas_synced == 0:
        result.status = "error"

    # FOFP incremental maintenance (Step 8) — fail-closed; never alters sync result.
    try:
        from app.crud.fofp_sync import run_fofp_post_sync_maintenance

        run_fofp_post_sync_maintenance(db, floor_id)
    except Exception:
        pass

    return {
        "status": result.status,
        "floor_id": result.floor_id,
        "areas_total": result.areas_total,
        "areas_synced": result.areas_synced,
        "areas_skipped": result.areas_skipped,
        "areas_renamed": result.areas_renamed,
        "zones_created": result.zones_created,
        "zones_updated": result.zones_updated,
        "zones_deleted": result.zones_deleted,
        "errors": result.errors,
    }

