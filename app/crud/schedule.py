from fastapi import HTTPException
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.processor import Processor
from app.models.schedule import Schedule, ScheduleGroups
from app.models.quick_controls import QuickControl, QuickControlArea, QuickControlAreaAction,CreationMode
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate
from app.utils.lutron_helpers import is_processor_reachable
from app.utils.json_connection import connect_to_processor, send_json, recv_json
from app.scheduler import schedule_job_for_schedule_id, scheduler
from app.crud.quick_controls import get_quick_control_by_id







# -------------------- Fetch Combined Schedules --------------------
def fetch_combined_schedules(db: Session) -> Dict:
    processor = db.query(Processor).first()
    if not processor:
        return {"status": "error", "message": "No processor found"}

    preconfigured_schedules = []
    events = []
    statuses = []

    if is_processor_reachable(processor.ipv4):
        try:
            with connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                # Fetch all timeclock events
                send_json(ssock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/timeclockevent"}})
                events_response = recv_json(ssock)
                events = events_response.get("Body", {}).get("TimeclockEvents", [])

                # Fetch all timeclock statuses
                send_json(ssock, {"CommuniqueType": "ReadRequest", "Header": {"Url": "/timeclockevent/status"}})
                status_response = recv_json(ssock)
                statuses = status_response.get("Body", {}).get("TimeclockEventStatuses", [])

        except Exception as e:
            print(f"[ERROR] Timeclock fetch error: {e}")

    if events and statuses and len(events) == len(statuses):
        for i, event in enumerate(events):
            end_date = event.get("EndDate", {})
            schedule_span = "Forever" if not end_date else "CustomDates"
            href = event.get("href", "")

            event_data = {
                "href": href,
                "timeclock_id": event.get("Parent", {}).get("href", "").split("/")[-1],
                "programming_model": event.get("ProgrammingModel", {}).get("href", ""),
                "name": event.get("Name", ""),
                "schedule_type": event.get("ScheduleType", ""),
                "schedule_span": schedule_span,
                "days": {d: event.get(d, False) for d in ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
                "begin_date": event.get("BeginDate", {}),
                "end_date": event.get("EndDate", {}),
                "exception_dates": event.get("ExceptionDates", []),
                "event_type": event.get("TimeclockEventType", ""),
                "EnableState": statuses[i].get("EnableState", "Unknown")  # Correct: match by index
            }

            if event_data["event_type"] == "FixedTime":
                event_data["time_of_day"] = event.get("TimeOfDay", {})
            elif event_data["event_type"] == "Astronomic":
                event_data["astronomic_type"] = event.get("AstronomicEventType", "")
                event_data["astronomic_offset"] = event.get("AstronomicTimeOffset", "")

            preconfigured_schedules.append(event_data)

    db_schedules = db.query(Schedule).all()
    internal_schedules = []
    for s in db_schedules:
        internal_schedules.append({
            "id": s.id,
            "name": s.name,
            "schedule_type": s.schedule_type,
            "schedule_span": s.schedule_span,
            "days": s.days,
            "specific_dates": s.specific_dates,
            "begin_date": s.begin_date,
            "end_date": s.end_date,
            "time_of_day": s.time_of_day,
            "quick_control_id": s.quick_control_id,
            "group_id": s.group_id,
            "group_name": s.group.name if s.group else None,
            "EnableState": "Enabled" if s.is_active else "Disabled"
        })

    return {
        "status": "success",
        "processor": processor.ipv4,
        "preconfigured_schedules": preconfigured_schedules,
        "internal_schedules": internal_schedules
    }


# -------------------- Create Schedule and QuickControl --------------------
def create_schedule_with_quick_control(db: Session, schedule_data: ScheduleCreate) -> Schedule:
    quick_control = QuickControl(name=schedule_data.name, creation_mode=CreationMode.schedule)
    db.add(quick_control)
    db.flush()

    schedule = Schedule(
        name=schedule_data.name,
        schedule_type=schedule_data.schedule_type,
        schedule_span=schedule_data.schedule_span,
        group_id=schedule_data.schedule_group_id,
        days=schedule_data.days,
        specific_dates=[d.model_dump() for d in schedule_data.specific_dates] if schedule_data.specific_dates else None,
        begin_date=schedule_data.begin_date.model_dump() if schedule_data.begin_date else None,
        end_date=schedule_data.end_date.model_dump() if schedule_data.end_date else None,
        time_of_day=schedule_data.time_of_day.model_dump() if schedule_data.time_of_day else None,
        quick_control_id=quick_control.id,
        is_active=True
    )
    db.add(schedule)

    for area in schedule_data.areas:
        qc_area = QuickControlArea(
            quick_control_id=quick_control.id,
            area_id=area.area_id
        )
        db.add(qc_area)
        db.flush()

        for action in area.actions:
            qc_action = QuickControlAreaAction(
                quick_control_area_id=qc_area.id,
                type=action.type,
                scene_code=action.scene_code,
                scene_name=action.scene_name,
                zone_id=action.zone_id,
                zone_name=action.zone_name,
                zone_type=action.zone_type,
                zone_status=action.zone_status,
                zone_brightness=action.zone_brightness,
                zone_temperature=action.zone_temperature,
                occupancy_setting=action.occupancy_setting,
                shade_group_id=action.shade_group_id,
                shade_group_name=action.shade_group_name,
                shade_level=action.shade_level,
                area_status=action.area_status if action.type == "area_status" else None
            )
            db.add(qc_action)

    db.commit()
    db.refresh(schedule)
    return schedule

# -------------------- Update Schedule --------------------
def update_schedule_with_quick_control(db: Session, schedule_id: int, schedule_data: ScheduleUpdate) -> Optional[Schedule]:
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        return None

    schedule.name = schedule_data.name
    schedule.schedule_type = schedule_data.schedule_type
    schedule.schedule_span = schedule_data.schedule_span
    schedule.days = schedule_data.days
    schedule.begin_date = schedule_data.begin_date.model_dump() if schedule_data.begin_date else None
    schedule.end_date = schedule_data.end_date.model_dump() if schedule_data.end_date else None
    schedule.time_of_day = schedule_data.time_of_day.model_dump() if schedule_data.time_of_day else None
    schedule.specific_dates = [d.model_dump() for d in schedule_data.specific_dates] if schedule_data.specific_dates else None
    schedule.group_id = schedule_data.model_dump().get("schedule_group_id")

    quick_control = db.query(QuickControl).filter(QuickControl.id == schedule.quick_control_id).first()
    if not quick_control:
        return None
    quick_control.name = schedule_data.name

    db.query(QuickControlAreaAction).filter(
        QuickControlAreaAction.quick_control_area_id.in_(
            db.query(QuickControlArea.id).filter(
                QuickControlArea.quick_control_id == quick_control.id
            )
        )
    ).delete(synchronize_session=False)
    db.query(QuickControlArea).filter(
        QuickControlArea.quick_control_id == quick_control.id
    ).delete(synchronize_session=False)

    db.flush()

    for area in schedule_data.areas:
        qc_area = QuickControlArea(
            quick_control_id=quick_control.id,
            area_id=area.area_id
        )
        db.add(qc_area)
        db.flush()

        for action in area.actions:
            qc_action = QuickControlAreaAction(
                quick_control_area_id=qc_area.id,
                type=action.type,
                scene_code=action.scene_code,
                scene_name=action.scene_name,
                zone_id=action.zone_id,
                zone_name=action.zone_name,
                zone_type=action.zone_type,
                zone_status=action.zone_status,
                zone_brightness=action.zone_brightness,
                zone_temperature=action.zone_temperature,
                occupancy_setting=action.occupancy_setting,
                shade_group_id=action.shade_group_id,
                shade_group_name=action.shade_group_name,
                shade_level=action.shade_level,
                area_status=action.area_status if action.type == "area_status" else None
            )
            db.add(qc_action)

    db.commit()
    db.refresh(schedule)
    return schedule

# -------------------- Delete Schedule --------------------
def delete_schedule(db: Session, schedule_id: int) -> bool:
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        return False

    if schedule.quick_control_id:
        db.query(QuickControlAreaAction).filter(
            QuickControlAreaAction.quick_control_area_id.in_(
                db.query(QuickControlArea.id).filter(QuickControlArea.quick_control_id == schedule.quick_control_id)
            )
        ).delete(synchronize_session=False)

        db.query(QuickControlArea).filter(
            QuickControlArea.quick_control_id == schedule.quick_control_id
        ).delete(synchronize_session=False)

        db.query(QuickControl).filter(
            QuickControl.id == schedule.quick_control_id
        ).delete(synchronize_session=False)

    db.delete(schedule)
    db.commit()
    return True


# -------------------- Enable / Disable --------------------
def enable_schedule(schedule_type: str, db: Session, schedule: Schedule = None, timeclockevent_id: int = None):
    if schedule_type == "internal":
        if not schedule:
            raise Exception("Schedule instance required for internal type")
        schedule.is_active = True
        db.commit()
        schedule_job_for_schedule_id(db, schedule.id)
        return {
            "status": "enabled",
            "schedule_type": "internal",
            "schedule_id": schedule.id,
            "quick_control_id": schedule.quick_control_id
        }

    elif schedule_type == "preconfigured":
        if not timeclockevent_id:
            raise Exception("timeclockevent_id required for preconfigured type")

        processor = db.query(Processor).first()
        if not processor:
            raise Exception("No processor configured")

        try:
            with connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                # Send UpdateRequest to enable the event
                send_json(ssock, {
                    "CommuniqueType": "UpdateRequest",
                    "Header": {
                        "Url": f"/timeclockevent/{timeclockevent_id}/status"
                    },
                    "Body": {
                        "TimeclockEventStatus": {
                            "EnableState": "Enabled"
                        }
                    }
                })

                response = recv_json(ssock)

            status = response.get("Header", {}).get("StatusCode", "")
            if response.get("CommuniqueType") == "ErrorResponse" or status not in ['200 OK', '201 Created']:
                raise Exception(f"Failed to enable event {timeclockevent_id}: {status}")

            return {
                "EnableState": "Enabled",
                "schedule_type": "preconfigured",
                "timeclockevent_id": timeclockevent_id
            }

        except Exception as e:
            raise Exception(f"Failed to enable preconfigured schedule: {e}")

    else:
        raise Exception("Invalid schedule_type provided")


def disable_schedule(schedule_type: str, db: Session, schedule: Schedule = None, timeclockevent_id: int = None):
    if schedule_type == "internal":
        if not schedule:
            raise Exception("Schedule instance required for internal type")

        schedule.is_active = False
        db.commit()

        try:
            if schedule.schedule_type == "DayOfWeek":
                scheduler.remove_job(job_id=f"schedule_{schedule.id}")
            elif schedule.schedule_type == "SpecificDates" and schedule.specific_dates:
                for date_entry in schedule.specific_dates:
                    year, month, day = date_entry["year"], date_entry["month"], date_entry["day"]
                    job_id = f"schedule_{schedule.id}_{year}_{month}_{day}"
                    scheduler.remove_job(job_id=job_id)
        except LookupError:
            pass

        return {
            "status": "disabled",
            "schedule_type": "internal",
            "schedule_id": schedule.id,
            "quick_control_id": schedule.quick_control_id
        }

    elif schedule_type == "preconfigured":
        if not timeclockevent_id:
            raise Exception("timeclockevent_id required for preconfigured type")

        processor = db.query(Processor).first()
        if not processor:
            raise Exception("No processor configured")

        try:
            with connect_to_processor(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as ssock:
                # First, validate the event exists
                send_json(ssock, {
                    "CommuniqueType": "ReadRequest",
                    "Header": {"Url": f"/timeclockevent/{timeclockevent_id}"}
                })
                response = recv_json(ssock)
                status = response.get("Header", {}).get("StatusCode", "")
                if response.get("CommuniqueType") == "ErrorResponse" or status not in ['200 OK', '201 Created']:
                    raise Exception(f"Timeclock event {timeclockevent_id} not valid on processor")

                # Now send the UpdateRequest to disable
                disable_payload = {
                    "CommuniqueType": "UpdateRequest",
                    "Header": {
                        "Url": f"/timeclockevent/{timeclockevent_id}/status"
                    },
                    "Body": {
                        "TimeclockEventStatus": {
                            "EnableState": "Disabled"
                        }
                    }
                }
                send_json(ssock, disable_payload)
                update_response = recv_json(ssock)

                # Optional: validate response
                update_status = update_response.get("Header", {}).get("StatusCode", "")
                if update_status not in ['200 OK', '204 No Content']:
                    raise Exception(f"Failed to disable timeclock event {timeclockevent_id}. Status: {update_status}")

                return {
                    "EnableState": "Disabled",
                    "schedule_type": "preconfigured",
                    "timeclockevent_id": timeclockevent_id
                }

        except Exception as e:
            raise Exception(f"Failed to disable preconfigured schedule: {e}")

    else:
        raise Exception("Invalid schedule_type provided")


# -------------------- Schedule Group Management --------------------
def create_new_schedule_group(db: Session, new_schedule_group_name: str) -> ScheduleGroups:
    new_group = ScheduleGroups(name=new_schedule_group_name)
    db.add(new_group)
    db.commit()
    db.refresh(new_group)
    return new_group


def get_all_schedule_groups(db: Session):
    return db.query(ScheduleGroups).all()



# -------------------- Schedule details using schedule ID --------------------
def get_schedule_details_logic(
    db: Session,
    type: str,
    timeclockevent_id: int = None,
    internal_schedule_id: int = None,
):
    result = fetch_combined_schedules(db)
    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result["message"])

    preconfigured_schedules = result.get("preconfigured_schedules", [])
    internal_schedules = result.get("internal_schedules", [])

    if type == "preconfigured":
        if not timeclockevent_id:
            raise HTTPException(status_code=400, detail="timeclockevent_id is required")
        for event in preconfigured_schedules:
            href_id = event.get("href", "").split("/")[-1]
            if str(href_id) == str(timeclockevent_id):
                return {
                    "status": "success",
                    "schedule_details": {
                        "id": int(href_id),
                        "type": "preconfigured",
                        **event
                    }
                }
        raise HTTPException(status_code=404, detail="Preconfigured schedule not found")

    elif type == "internal":
        if not internal_schedule_id:
            raise HTTPException(status_code=400, detail="internal_schedule_id is required")
        for schedule in internal_schedules:
            if str(schedule.get("id")) == str(internal_schedule_id):
                quick_control_id = schedule.get("quick_control_id")
                areas = []
                if quick_control_id:
                    quick_control = get_quick_control_by_id(db, quick_control_id)
                    if quick_control and quick_control.quick_control_areas:
                        areas = [area.to_dict() for area in quick_control.quick_control_areas]
                return {
                    "status": "success",
                    "schedule_details": {
                        "id": schedule.get("id"),
                        "type": "internal",
                        **{k: v for k, v in schedule.items() if k != "id"}
                    },
                    "areas": areas
                }
        raise HTTPException(status_code=404, detail="Internal schedule not found")

    raise HTTPException(status_code=400, detail="Invalid type")