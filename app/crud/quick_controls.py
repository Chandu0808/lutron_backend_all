from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException
from app.models.quick_controls import QuickControl, QuickControlArea, QuickControlAreaAction, CreationMode
from app.schemas.quick_controls import QuickControlCreate, QuickControlUpdate
from app.models.schedule import Schedule
from app.crud.area import activate_scene_for_area, update_zones_by_area
from app.crud.update_occupancy import update_area_occupancy_setting
from app.utils.logger import logger


def create_quick_control_entry(db: Session, payload: QuickControlCreate) -> QuickControl:
    existing = db.query(QuickControl).filter_by(name=payload.name).first()
    if existing:
        return None

    quick_control = QuickControl(name=payload.name, creation_mode=CreationMode.quick_control)
    db.add(quick_control)
    db.flush()

    for area in payload.areas:
        qc_area = QuickControlArea(
            quick_control_id=quick_control.id,
            area_id=area.area_id
        )
        db.add(qc_area)
        db.flush()

        zone_status_count = sum(1 for action in area.actions if action.type == "zone_status")
        if zone_status_count > 1:
            raise HTTPException(
                status_code=400,
                detail=f"Only one 'zone_status' is allowed per area (Area ID: {area.area_id})"
            )

        for action in area.actions:
            qc_action = QuickControlAreaAction(
                quick_control_area_id=qc_area.id,
                type=action.type,
                scene_code=action.scene_code if action.type == "set_scene" else None,
                scene_name=action.scene_name if action.type == "set_scene" else None,
                zone_id=action.zone_id if action.type == "zone_status" else None,
                zone_name=action.zone_name if action.type == "zone_status" else None,
                zone_type=action.zone_type if action.type == "zone_status" else None,
                zone_status=action.zone_status if action.type == "zone_status" else None,
                zone_brightness=action.zone_brightness if action.type == "zone_status" else None,
                zone_temperature=action.zone_temperature if action.type == "zone_status" else None,
                occupancy_setting=action.occupancy_setting if action.type == "occupancy" else None,
                shade_group_id=action.shade_group_id if action.type == "shade_group_status" else None,
                shade_level=action.shade_level if action.type == "shade_group_status" else None,
                shade_group_name=action.shade_group_name if action.type == "shade_group_status" else None,
                area_status=action.area_status if action.type == "area_status" else None
            )
            db.add(qc_action)

    db.commit()
    db.refresh(quick_control)
    return quick_control


def update_quick_control_entry(db: Session, control_id: int, payload: QuickControlUpdate):
    quick_control = db.query(QuickControl).filter_by(id=control_id).first()
    if not quick_control:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    quick_control.name = payload.name

    area_ids = db.query(QuickControlArea.id).filter_by(quick_control_id=control_id).all()
    area_ids = [a.id for a in area_ids]
    if area_ids:
        db.query(QuickControlAreaAction).filter(
            QuickControlAreaAction.quick_control_area_id.in_(area_ids)
        ).delete(synchronize_session=False)

    db.query(QuickControlArea).filter_by(quick_control_id=control_id).delete()
    db.flush()

    for area in payload.areas:
        qc_area = QuickControlArea(
            quick_control_id=control_id,
            area_id=area.area_id
        )
        db.add(qc_area)
        db.flush()

        zone_status_count = sum(1 for action in area.actions if action.type == "zone_status")
        if zone_status_count > 1:
            raise HTTPException(
                status_code=400,
                detail=f"Only one 'zone_status' allowed per area (Area ID: {area.area_id})"
            )

        for action in area.actions:
            qc_action = QuickControlAreaAction(
                quick_control_area_id=qc_area.id,
                type=action.type,
                scene_code=action.scene_code if action.type == "set_scene" else None,
                scene_name=action.scene_name if action.type == "set_scene" else None,
                zone_id=action.zone_id if action.type == "zone_status" else None,
                zone_name=action.zone_name if action.type == "zone_status" else None,
                zone_type=action.zone_type if action.type == "zone_status" else None,
                zone_status=action.zone_status if action.type == "zone_status" else None,
                zone_brightness=action.zone_brightness if action.type == "zone_status" else None,
                zone_temperature=action.zone_temperature if action.type == "zone_status" else None,
                occupancy_setting=action.occupancy_setting if action.type == "occupancy" else None,
                shade_group_id=action.shade_group_id if action.type == "shade_group_status" else None,
                shade_level=action.shade_level if action.type == "shade_group_status" else None,
                shade_group_name=action.shade_group_name if action.type == "shade_group_status" else None,
                area_status=action.area_status if action.type == "area_status" else None
            )
            db.add(qc_action)

    db.commit()
    db.refresh(quick_control)
    
    # Return the updated quick control data
    return {
        "id": quick_control.id,
        "name": quick_control.name,
        "quick_control_areas": [area.to_dict() for area in quick_control.quick_control_areas]
    }


def delete_quick_control(db: Session, control_id: int) -> bool:
    quick_control = db.query(QuickControl).filter(QuickControl.id == control_id).first()
    if not quick_control:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    linked_schedule = db.query(Schedule).filter(Schedule.quick_control_id == control_id).first()
    if linked_schedule:
        raise HTTPException(
            status_code=400,
            detail=f"QuickControl is linked to Schedule ID {linked_schedule.id}. Please delete the schedule first."
        )

    db.delete(quick_control)
    db.commit()
    return True


def get_all_quick_controls(db: Session):
    return (
        db.query(QuickControl)
        .filter(QuickControl.creation_mode == CreationMode.quick_control)
        .all()
    )


def get_quick_control_by_id(db: Session, control_id: int):
    return db.query(QuickControl).options(
        joinedload(QuickControl.quick_control_areas).joinedload(QuickControlArea.area),
        joinedload(QuickControl.quick_control_areas).joinedload(QuickControlArea.actions)
    ).filter(QuickControl.id == control_id).first()


def trigger_quick_control_logic(quick_control_id: int, db: Session):
    qc = db.query(QuickControl).filter(QuickControl.id == quick_control_id).first()
    if not qc:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    # No need for processor.first() - each action function gets its own processor!
    # This allows quick controls to work across multiple processors
    
    for area in qc.quick_control_areas:
        area_id = area.area_id
        for action in area.actions:
            try:
                if action.type == "set_scene" and action.scene_code:
                    activate_scene_for_area(area_id, int(action.scene_code), db)

                elif action.type == "zone_status":
                    # For common light status actions (On/Off), use set_all_zones_on_off to control ALL zones in the area
                    if action.zone_status in ["On", "Off"]:
                        from app.crud.area import set_all_zones_on_off
                        set_all_zones_on_off(db, area_id, action.zone_status)
                    else:
                        # For specific zone controls (brightness, temperature, specific zone_id), use existing method
                        zone_data = {
                            "zone_id": action.zone_id,
                            "zone_type": action.zone_type.lower() if action.zone_type else ""
                        }
                        if zone_data["zone_type"] == "switched" and action.zone_status:
                            zone_data["switched_state"] = action.zone_status
                        elif zone_data["zone_type"] in ["dimmed", "whitetune"]:
                            if action.zone_brightness:
                                zone_data["level"] = int(action.zone_brightness.replace("%", ""))
                            if action.zone_temperature and zone_data["zone_type"] == "whitetune":
                                zone_data["kelvin"] = int(action.zone_temperature.replace("K", ""))
                        elif zone_data["zone_type"] == "shade" and action.shade_level:
                            zone_data["level"] = int(action.shade_level.replace("%", ""))
                        update_zones_by_area(db, area_id, [zone_data])

                elif action.type == "occupancy" and action.occupancy_setting:
                    update_area_occupancy_setting(db, area_id, action.occupancy_setting.capitalize())

                elif action.type == "shade_group_status" and action.shade_group_id:
                    zone_data = {
                        "zone_id": action.shade_group_id,
                        "zone_type": "shade",
                        "level": int(action.shade_level.replace("%", "")) if action.shade_level else 0
                    }
                    update_zones_by_area(db, area_id, [zone_data])

                elif action.type == "area_status" and action.area_status:
                    from app.crud.area import set_all_zones_on_off
                    set_all_zones_on_off(db, area_id, action.area_status)

            except Exception as e:
                logger.warning(f"[Trigger] Failed {action.type} in area {area_id}: {e}")
                continue

    return {"status": "success", "message": f"QuickControl '{qc.name}' triggered"}
