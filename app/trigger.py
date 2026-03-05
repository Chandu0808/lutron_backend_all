from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.utils.activity_logger import log_activity
from app.models.processor import Processor
from app.models.schedule import Schedule
from app.models.quick_controls import QuickControl, QuickControlArea
from app.models.area import Area
from app.crud.quick_controls import trigger_quick_control_logic


def trigger_schedule_event_logic(payload, db: Session, user):
    processor = db.query(Processor).first()
    if not processor:
        raise HTTPException(status_code=404, detail="No processor found")

    if payload.schedule_type == "pre_configure":
        timeclock_id = payload.timeclock_id
        try:
            with connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                send_json(ssock, {
                    "CommuniqueType": "CreateRequest",
                    "Header": {"Url": f"/timeclockevent/{timeclock_id}/commandprocessor"},
                    "Body": {"Command": {"CommandType": "TestThisTimeclockEvent"}}
                })
                response = recv_json(ssock)

            status = response.get("Header", {}).get("StatusCode", "")
            if response.get("CommuniqueType") == "ErrorResponse" or status not in ['200 OK', '201 Created', '204 NoContent']:
                raise HTTPException(status_code=400, detail=f"Processor rejected timeclock_id {timeclock_id}")

            try:
                log_activity(
                    db=db,
                    user_id=user.id,
                    activity_type="Schedule Manual Trigger",
                    activity_description=f"Manually triggered preconfigured Schedule event (ID: {timeclock_id})"
                )
            except Exception as e:
                pass

            return {
                "status": "success",
                "type": "pre_configure",
                "timeclock_id": timeclock_id,
                "response": response
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Timeclock trigger failed: {e}")

    elif payload.schedule_type == "internal":
        schedule = db.query(Schedule).filter(Schedule.id == payload.schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        if not schedule.quick_control_id:
            raise HTTPException(status_code=400, detail="Schedule missing quick_control_id")

        try:
            result = trigger_quick_control_logic(schedule.quick_control_id, db)

            try:
                floor_id = None
                area_id = None
                if schedule.quick_control and schedule.quick_control.quick_control_areas:
                    first_qc_area = schedule.quick_control.quick_control_areas[0]
                    area_id = first_qc_area.area_id
                    floor_id = first_qc_area.floor_id

                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=floor_id,
                    area_id=area_id,
                    activity_type="Schedule Manual Trigger",
                    activity_description=f"Manually triggered internal schedule '{schedule.name}'"
                )
            except Exception as e:
                pass

            return {
                "status": "success",
                "type": "internal",
                "schedule_id": schedule.id,
                "quick_control_id": schedule.quick_control_id,
                "result": result
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"QuickControl trigger failed: {e}")


def trigger_quick_control_event(quick_control_id: int, db: Session, user):
    try:
        qc = db.query(QuickControl).filter(QuickControl.id == quick_control_id).first()
        qc_name = qc.name if qc else f"ID {quick_control_id}"

        floor_id = None
        qc_area = (
            db.query(QuickControlArea)
            .filter(QuickControlArea.quick_control_id == quick_control_id)
            .first()
        )
        if qc_area:
            area = db.query(Area).filter(Area.id == qc_area.area_id).first()
            floor_id = area.floor_id if area else None

        log_activity(
            db=db,
            user_id=user.id if user else 1,   # fallback to system user
            area_id=None,
            floor_id=floor_id,
            activity_type="QuickControl Trigger",
            activity_description=f"Triggered QuickControl '{qc_name}'"
        )
    except Exception as e:
        pass

    return trigger_quick_control_logic(quick_control_id, db)