from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional, Any
from pydantic import BaseModel
from app.database.session import get_db
from app.models.zone import Zone
from app.models.area import Area
from app.models.floor import Floor
from app.models.processor import Processor
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.utils.load_schedule_csv import parse_load_schedule_csv

router = APIRouter()


# Pydantic schema for output zone details
class ZoneOut(BaseModel):
    id: int
    code: str
    name: str
    type: Optional[str]
    area_id: int

    class Config:
        from_attributes = True


# Wrapper schema for API response
class ZoneListResponse(BaseModel):
    status: str
    zones: List[ZoneOut]


class UploadZonewiseLoadResponse(BaseModel):
    status: str
    message: str
    areas_processed: int
    zones_updated: int
    dimmed_zones_without_trim: int
    errors: List[str]


class ZoneTuningOut(BaseModel):
    zone_id: int
    zone_code: Optional[str]
    zone_name: str
    loadcontroller_code: Optional[int]
    high_end_trim: Optional[float]
    energy_trim: Optional[float]
    low_end_trim: Optional[float]


class AreaTuningSettingsResponse(BaseModel):
    status: str
    area_id: int
    area_name: Optional[str]
    zones: List[ZoneTuningOut]


class ZoneMissingLoadOut(BaseModel):
    zone_id: int
    zone_code: Optional[str]
    zone_name: str
    max_power: Optional[float]
    high_end_trim: Optional[float]


class AreaMissingLoadOut(BaseModel):
    area_id: int
    area_name: Optional[str]
    processor_id: int
    zones: List[ZoneMissingLoadOut]


class FloorMissingLoadOut(BaseModel):
    floor_id: Optional[int]
    floor_name: Optional[str]
    areas: List[AreaMissingLoadOut]


class ZonesMissingLoadDataResponse(BaseModel):
    floors: List[FloorMissingLoadOut]


@router.get("/zones_missing_load_data", response_model=ZonesMissingLoadDataResponse)
def zones_missing_load_data(
    processor_id: Optional[int] = Query(None, description="Filter by processor ID"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Zones where max_power is null or high_end_trim is null, organized by floor → area → zone
    for easy identification of zones that need load data.
    """
    q = (
        db.query(Zone, Area, Floor)
        .join(Area, Zone.area_id == Area.id)
        .outerjoin(Floor, Area.floor_id == Floor.id)
        .filter(or_(Zone.max_power.is_(None), Zone.high_end_trim.is_(None)))
    )
    if processor_id is not None:
        q = q.filter(Area.processor_id == processor_id)
    rows = q.order_by(Floor.name.asc().nulls_last(), Area.name, Zone.name).all()

    # Build nested: floor_id -> area_id -> [zones]
    by_floor: dict[tuple[Optional[int], Optional[str]], dict[tuple[int, Optional[str], int], list[dict[str, Any]]]] = {}
    for zone, area, floor in rows:
        fkey = (floor.id if floor else None, floor.name if floor else None)
        akey = (area.id, area.name, area.processor_id)
        if fkey not in by_floor:
            by_floor[fkey] = {}
        if akey not in by_floor[fkey]:
            by_floor[fkey][akey] = []
        by_floor[fkey][akey].append({
            "zone_id": zone.id,
            "zone_code": zone.code,
            "zone_name": zone.name,
            "max_power": zone.max_power,
            "high_end_trim": zone.high_end_trim,
        })

    floors_out: List[FloorMissingLoadOut] = []
    for (fid, fname), areas_dict in sorted(by_floor.items(), key=lambda x: (x[0][0] is None, str(x[0][1]) or "")):
        areas_out: List[AreaMissingLoadOut] = []
        for (aid, aname, pid), zones_list in sorted(areas_dict.items(), key=lambda x: (x[0][1] or "")):
            areas_out.append(AreaMissingLoadOut(
                area_id=aid,
                area_name=aname,
                processor_id=pid,
                zones=[ZoneMissingLoadOut(**z) for z in zones_list],
            ))
        floors_out.append(FloorMissingLoadOut(
            floor_id=fid,
            floor_name=fname,
            areas=areas_out,
        ))
    return ZonesMissingLoadDataResponse(floors=floors_out)


@router.get("/zone_list", response_model=ZoneListResponse)
def list_zones_by_area(area_id: int = Query(..., description="Area ID to filter zones by"),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Returns all zones that belong to a specific area_id.
    No processor interaction.
    """
    zones = db.query(Zone).filter(Zone.area_id == area_id).all()
    return {"status": "success", "zones": zones}


@router.get("/tunning_settings", response_model=AreaTuningSettingsResponse)
def get_area_tunning_settings(
    area_id: int = Query(..., description="Area ID to fetch zone-wise tuning settings"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Returns zone-wise tuning settings from DB: loadcontroller_code, high_end_trim, energy_trim, low_end_trim.
    """
    area = db.query(Area).filter(Area.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    zones = (
        db.query(Zone)
        .filter(Zone.area_id == area_id)
        .filter(Zone.type.ilike("%dimmed%"))
        .order_by(Zone.name.asc())
        .all()
    )
    return {
        "status": "success",
        "area_id": area.id,
        "area_name": area.name,
        "zones": [
            {
                "zone_id": zone.id,
                "zone_code": zone.code,
                "zone_name": zone.name,
                "loadcontroller_code": zone.loadcontroller_code,
                "high_end_trim": zone.high_end_trim,
                "energy_trim": zone.energy_trim,
                "low_end_trim": zone.low_end_trim,
            }
            for zone in zones
        ],
    }


@router.post("/upload_zonewise_load_csv", response_model=UploadZonewiseLoadResponse)
def upload_zonewise_load_csv(
    file: UploadFile = File(..., description="Load Schedule CSV (zone-wise load)"),
    processor_id: int = Query(..., description="Processor ID for area matching"),
    high_end_trim_default: float = Query(100.0, ge=0.0, le=100.0, description="Default HighendTrim when not present in CSV (mandatory, 0-100)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upload a Load Schedule CSV to set zone max_power and high_end_trim.
    CSV and processor_id are required. high_end_trim is read from the CSV HighendTrim column
    (zone-wise); when missing or empty for a zone, high_end_trim_default is used.
    Response includes dimmed_zones_without_trim: count of dimmed zones with no high_end_trim after upload.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    processor = db.query(Processor).filter(Processor.id == processor_id).first()
    if not processor:
        raise HTTPException(status_code=400, detail="Processor not found")

    errors: List[str] = []
    try:
        content = file.file.read().decode("utf-8-sig").strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

    parsed = parse_load_schedule_csv(content)
    areas_processed = 0
    zones_updated = 0

    for area_name, zone_rows in parsed:
        area = db.query(Area).filter(Area.name == area_name, Area.processor_id == processor_id).first()
        if not area:
            errors.append(f"Area not found: {area_name}")
            continue
        areas_processed += 1
        for zone_name, max_power, csv_trim in zone_rows:
            zone = db.query(Zone).filter(Zone.area_id == area.id, Zone.name == zone_name).first()
            if not zone:
                errors.append(f"Zone not found: {area_name} / {zone_name}")
                continue
            zone.max_power = max_power
            trim_val = csv_trim if csv_trim is not None else high_end_trim_default
            zone.high_end_trim = trim_val
            zones_updated += 1

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    dimmed_without_trim = (
        db.query(Zone)
        .join(Area, Zone.area_id == Area.id)
        .filter(Area.processor_id == processor_id)
        .filter(Zone.type.ilike("%dimmed%"))
        .filter(Zone.high_end_trim.is_(None))
        .count()
    )

    return {
        "status": "success",
        "message": f"Processed {areas_processed} areas, updated {zones_updated} zones.",
        "areas_processed": areas_processed,
        "zones_updated": zones_updated,
        "dimmed_zones_without_trim": dimmed_without_trim,
        "errors": errors,
    }


