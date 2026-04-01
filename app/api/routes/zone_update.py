from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum

from app.database.session import get_db
from app.models.area import Area
from app.models.zone import Zone
from app.models.processor import Processor
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.utils.activity_logger import log_activity
from app.crud.area import update_zones_by_area, set_all_zones_on_off
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.activity_report_logger import activity_report_log
from app.utils.json_connection import create_ssl_connection, send_json, recv_json

router = APIRouter()

# -------------------- Schemas -------------------- #
class ZoneCommand(BaseModel):
    zone_id: int
    zone_type: Literal["Switched", "switched", "Dimmed", "dimmed", "WhiteTune", "whitetune", "Shade", "shade"]
    switched_state: Optional[Literal["On", "Off"]] = None
    level: Optional[int] = None
    kelvin: Optional[int] = None
    fade_time: Optional[str] = None
    delay_time: Optional[str] = None

class ZoneUpdateRequest(BaseModel):
    area_id: int
    zones: List[ZoneCommand]

class ZoneAction(str, Enum):
    on = "On"
    off = "Off"

class ZoneOnOffRequest(BaseModel):
    area_id: int
    action: ZoneAction


class ZoneTuningUpdateRequest(BaseModel):
    """
    zone_id matches Zone.id (DB primary key).
    Provide at least one of HighEndTrim / EnergyTrim / LowEndTrim (LEAP-style names also accepted).
    """
    model_config = ConfigDict(populate_by_name=True)

    zone_id: int
    high_end_trim: Optional[float] = Field(default=None, alias="HighEndTrim")
    energy_trim: Optional[float] = Field(default=None, alias="EnergyTrim")
    low_end_trim: Optional[float] = Field(default=None, alias="LowEndTrim")


class ZoneTuningUpdateResponse(BaseModel):
    status: str
    zone_id: int
    loadcontroller_code: int
    high_end_trim: float
    energy_trim: float
    low_end_trim: float


def _extract_tuning_href(loadcontroller_body: dict) -> Optional[str]:
    if not isinstance(loadcontroller_body, dict):
        return None

    ts = loadcontroller_body.get("TuningSettings")
    if isinstance(ts, dict) and isinstance(ts.get("href"), str):
        return ts["href"]

    dimmed_props = loadcontroller_body.get("DimmedLoadControllerProperties")
    if isinstance(dimmed_props, dict):
        ts2 = dimmed_props.get("TuningSettings")
        if isinstance(ts2, dict) and isinstance(ts2.get("href"), str):
            return ts2["href"]

    return None


def update_zone_tuning_factors_on_processor(
    db: Session,
    zone: Zone,
    tuning_update_fields: dict,
) -> dict:
    """
    Update processor tuningsettings for the zone's load controller; verify by read-back.
    Persists high_end_trim, energy_trim, low_end_trim on Zone.
    """
    if not zone or zone.loadcontroller_code is None:
        raise HTTPException(status_code=400, detail="Zone has no loadcontroller_code associated")

    area = db.query(Area).filter(Area.id == zone.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found for zone")

    processor = db.query(Processor).filter(Processor.id == area.processor_id).first()
    if not processor:
        raise HTTPException(status_code=404, detail="Processor not found for zone")

    ssock = create_ssl_connection(
        processor.ipv4,
        processor.mac,
        processor.system,
        processor_ipv4=processor.ipv4,
        port=8081,
        timeout=8,
    )
    if not ssock:
        raise HTTPException(status_code=500, detail="Failed to connect to processor")

    try:
        loadcontroller_code = int(zone.loadcontroller_code)

        send_json(
            ssock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/loadcontroller/{loadcontroller_code}"},
            },
        )
        lc_resp = recv_json(ssock)
        lc_body = (lc_resp or {}).get("Body", {}).get("LoadController")
        if not isinstance(lc_body, dict):
            raise HTTPException(status_code=500, detail="Invalid LoadController response from processor")

        tuning_href = _extract_tuning_href(lc_body)
        if not tuning_href:
            raise HTTPException(status_code=400, detail="Tuningsettings not available for this loadcontroller")

        send_json(
            ssock,
            {
                "CommuniqueType": "UpdateRequest",
                "Header": {"Url": tuning_href},
                "Body": {"TuningSettings": tuning_update_fields},
            },
        )
        _ = recv_json(ssock)

        send_json(
            ssock,
            {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": tuning_href},
            },
        )
        verify_resp = recv_json(ssock)
        ts_body = (verify_resp or {}).get("Body", {}).get("TuningSettings")
        if not isinstance(ts_body, dict):
            raise HTTPException(status_code=500, detail="Invalid tuningsettings response from processor")

        try:
            zone.high_end_trim = float(ts_body.get("HighEndTrim"))
            zone.energy_trim = float(ts_body.get("EnergyTrim"))
            zone.low_end_trim = float(ts_body.get("LowEndTrim"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=500, detail="Processor tuningsettings values missing/invalid")

        db.commit()
        db.refresh(zone)

        return {
            "high_end_trim": zone.high_end_trim,
            "energy_trim": zone.energy_trim,
            "low_end_trim": zone.low_end_trim,
        }
    finally:
        try:
            ssock.close()
        except Exception:
            pass


@router.post("/zone_update")
async def zone_update(
    payload: ZoneUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # 1) Resolve area
        area = db.query(Area).filter(Area.id == payload.area_id).first()
        if not area:
            raise HTTPException(status_code=404, detail="Area not found")

        # 2) Permission check
        try:
            require_operator_permission_for_scope(
                required_level=2,  # monitor + control
                floor_ids=[area.floor_id],
                enforce_on_empty_scope=True,
                db=db,
                current_user=current_user
            )
        except HTTPException as e:
            if e.status_code == 403:
                return {
                    "status": "failed",
                    "message": f"Not authorized to update zones in floor {area.floor_id}"
                }
            raise

        # 3) Log GUI actions per-zone (before applying updates)
        for zone_cmd in payload.zones:
            # Lookup by Zone.code instead of Zone.id
            zone = (
                db.query(Zone)
                .filter(
                    Zone.code == str(zone_cmd.zone_id),
                    Zone.processor_id == area.processor_id,
                )
                .first()
            )
            if not zone:
                zone_name = f"Zone {zone_cmd.zone_id} (not in DB)"
                zone_type = (zone_cmd.zone_type or "").lower()
            else:
                zone_name = zone.name
                zone_type = (zone.type or "").lower()

            # Shade
            if zone_type == "shade" and zone_cmd.level is not None:
                log_activity(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="GUI Triggered",
                    activity_description=f"Shade level changed in {zone_name}"
                )
                activity_report_log(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="User",
                    sub_activity_type="ZoneShadeLevelChanged",
                    activity_description=f"Shade level changed to {zone_cmd.level} in {zone_name}"
                )

            # Dimmer / Whitetune
            elif zone_type in ("dimmer", "dimmed", "whitetune") and zone_cmd.level is not None:
                log_activity(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="GUI Triggered",
                    activity_description=f"Light brightness level changed in {zone_name}"
                )
                activity_report_log(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="User",
                    sub_activity_type="ZoneLightStatusChanged",
                    activity_description=f"Light brightness level changed to {zone_cmd.level} percent in {zone_name}"
                )

            # Switch
            if zone_cmd.switched_state is not None:
                log_activity(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="GUI Triggered",
                    activity_description=f"Switch state changed in {zone_name}"
                )
                activity_report_log(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="User",
                    sub_activity_type="ZoneLightStatusChanged",
                    activity_description=f"Switched level changed to {zone_cmd.switched_state} in {zone_name}"
                )

            # Kelvin
            if getattr(zone_cmd, "kelvin", None) is not None:
                log_activity(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="GUI Triggered",
                    activity_description=f"Color temperature changed in {zone_name}"
                )
                activity_report_log(
                    db=db, user_id=current_user.id, area_id=area.id,
                    activity_type="User",
                    sub_activity_type="ZoneLightStatusChanged",
                    activity_description=f"Temperature changed to {zone_cmd.kelvin}K in {zone_name}"
                )

        # 4) Apply updates
        return update_zones_by_area(db, payload.area_id, [z.dict() for z in payload.zones])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/zone_tuning_update", response_model=ZoneTuningUpdateResponse)
async def zone_tuning_update(
    payload: ZoneTuningUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update processor tuningsettings for a single zone (by Zone.id).
    Partial update: send only the trim fields you want to change; processor read-back fills all three in DB.
    """
    if payload.high_end_trim is None and payload.energy_trim is None and payload.low_end_trim is None:
        raise HTTPException(status_code=400, detail="Provide at least one tuning field to update")

    zone = db.query(Zone).filter(Zone.id == payload.zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    area = db.query(Area).filter(Area.id == zone.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")

    try:
        require_operator_permission_for_scope(
            required_level=2,
            floor_ids=[area.floor_id],
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user,
        )
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized to update tuning in floor {area.floor_id}",
            ) from e
        raise

    tuning_update_fields: dict = {}
    if payload.high_end_trim is not None:
        tuning_update_fields["HighEndTrim"] = payload.high_end_trim
    if payload.energy_trim is not None:
        tuning_update_fields["EnergyTrim"] = payload.energy_trim
    if payload.low_end_trim is not None:
        tuning_update_fields["LowEndTrim"] = payload.low_end_trim

    verified = update_zone_tuning_factors_on_processor(
        db=db,
        zone=zone,
        tuning_update_fields=tuning_update_fields,
    )

    return ZoneTuningUpdateResponse(
        status="success",
        zone_id=zone.id,
        loadcontroller_code=int(zone.loadcontroller_code),
        high_end_trim=verified["high_end_trim"],
        energy_trim=verified["energy_trim"],
        low_end_trim=verified["low_end_trim"],
    )


@router.post("/zone_on-off")
async def zone_on_off(
    payload: ZoneOnOffRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    try:
        area = db.query(Area).filter(Area.id == payload.area_id).first()
        if not area:
            raise HTTPException(status_code=404, detail="Area not found")

        # Log ON/OFF for whole area
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=area.id,
            activity_type="User",
            sub_activity_type="AreaLightStatusChanged",
            activity_description=f"All zones turned {payload.action.value} in area {area.name}"
        )

        return set_all_zones_on_off(db, area_id=payload.area_id, action=payload.action)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
