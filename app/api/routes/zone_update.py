from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List, Literal
from pydantic import BaseModel
from enum import Enum

from app.database.session import get_db
from app.models.area import Area
from app.models.zone import Zone
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.utils.activity_logger import log_activity
from app.crud.area import update_zones_by_area, set_all_zones_on_off
from app.dependencies.permissions import require_operator_permission_for_scope
from app.utils.activity_report_logger import activity_report_log

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
            zone = db.query(Zone).filter(Zone.code == str(zone_cmd.zone_id)).first()
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
