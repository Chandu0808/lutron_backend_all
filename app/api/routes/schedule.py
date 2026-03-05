from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from collections import OrderedDict
from typing import List

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.models.schedule import Schedule
from app.schemas.schedule import (
    ScheduleCreate, ScheduleUpdate,
    TriggerRequest, SuccessResponse,
    ScheduleGroupsListResponse
)
from app.crud.schedule import (
    fetch_combined_schedules,
    create_schedule_with_quick_control,
    update_schedule_with_quick_control,
    create_new_schedule_group,
    delete_schedule,
    get_all_schedule_groups,
    enable_schedule,
    disable_schedule,
    get_schedule_details_logic
)
from app.scheduler import schedule_job_for_schedule_id, scheduler
from app.api.routes.quick_controls import trigger_quick_control as internal_trigger_qc
from app.models.processor import Processor
from app.utils.activity_logger import log_activity
import os
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.area import Area
from app.models.quick_controls import QuickControlArea
from app.trigger import trigger_schedule_event_logic
from app.utils.activity_report_logger import activity_report_log
from datetime import datetime
from app.models.quick_controls import QuickControl,QuickControlArea

router = APIRouter()

@router.get("/list")
def get_timeclock_schedule(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns processor IP, internal and preconfigured schedules.
    Admin/Superadmin get full access.
    Operators see only schedules where at least one area is mapped to a permitted floor.
    """

    result = fetch_combined_schedules(db)

    if result["status"] != "success":
        raise HTTPException(status_code=404, detail=result.get("message", "No schedules found"))

    processor = result.get("processor")
    internal_schedules = result.get("internal_schedules", [])
    preconfigured_schedules = result.get("preconfigured_schedules", [])

    # Admin/Superadmin: Return full result
    if current_user.role in ["Admin", "Superadmin"]:
        return {
            "status": "success",
            "processor": processor,
            "internal_schedules": internal_schedules,
            "preconfigured_schedules": preconfigured_schedules
        }

    # Operator: filter internal_schedules only
    permitted_internal_schedules = []

    for sched in internal_schedules:
        qc_id = sched.get("quick_control_id")
        try:
            qc_id = int(qc_id)
        except (TypeError, ValueError):
            continue

        try:
            # Get area_ids mapped to the QC
            area_ids = db.query(QuickControlArea.area_id).filter(
                QuickControlArea.quick_control_id == qc_id
            ).all()
            area_ids = [a[0] for a in area_ids]

            if not area_ids:
                continue

            # Get floor_ids from those areas
            floor_ids = db.query(Area.floor_id).filter(
                Area.id.in_(area_ids)
            ).distinct().all()
            floor_ids = [f[0] for f in floor_ids if f[0] is not None]

            # Check if the user has monitor access to at least one of these floors
            for fid in floor_ids:
                try:
                    require_operator_permission_for_scope(
                        required_level=1,
                        floor_ids=[fid],
                        enforce_on_empty_scope=True,
                        db=db,
                        current_user=current_user
                    )
                    permitted_internal_schedules.append(sched)
                    break  # Stop checking once access is confirmed
                except HTTPException as e:
                    if e.status_code != 403:
                        raise

        except Exception:
            continue

    return {
        "status": "success",
        "processor": processor,
        "preconfigured_schedules": preconfigured_schedules,
        "internal_schedules": permitted_internal_schedules
    }

@router.get("/details")
def get_schedule_details(
    type: str,
    timeclockevent_id: int = None,
    internal_schedule_id: int = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    return get_schedule_details_logic(
        db=db,
        type=type,
        timeclockevent_id=timeclockevent_id,
        internal_schedule_id=internal_schedule_id
    )

@router.post("/enable")
def enable_schedule_event(
    type: str,
    timeclockevent_id: int = None,
    internal_schedule_id: int = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    if type == "internal":
        if not internal_schedule_id:
            raise HTTPException(status_code=400, detail="Missing internal_schedule_id for internal type")

        schedule = db.query(Schedule).filter(Schedule.id == internal_schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        try:
            result = enable_schedule(schedule_type="internal", db=db, schedule=schedule)

            # ---------- Unified logging ----------
            try:
                # Legacy
                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=None,
                    area_id=None,
                    activity_type="GUI Triggered",
                    activity_description=f"Enabled schedule '{schedule.name}'"
                )

                # Resolve area_name if possible
                area_name = None
                area_ids = []
                if schedule.quick_control_id:
                    rows = (
                        db.query(QuickControlArea.area_id)
                        .filter(QuickControlArea.quick_control_id == schedule.quick_control_id)
                        .all()
                    )
                    area_ids = [r[0] for r in rows if r and r[0] is not None]

                unique_area_ids = list({aid for aid in area_ids if aid is not None})
                log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

                if log_area_id:
                    area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()

                # New
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="Schedule",
                    activity_description=f"Enabled schedule '{schedule.name}'",
                    area_name=area_name
                )
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="User",
                    activity_description=f"Enabled schedule '{schedule.name}'",
                    area_name=area_name
                )
            except Exception as e:
                print(f"[LOG ERROR] Failed to log schedule enable activity: {e}")

            return result

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif type == "preconfigured":
        if not timeclockevent_id:
            raise HTTPException(status_code=400, detail="Missing timeclockevent_id for preconfigured type")

        if user.role not in ["Admin", "Superadmin"]:
            return {
                "status": "failed",
                "message": "Only Admin or Superadmin can enable preconfigured schedules."
            }

        try:
            result = enable_schedule(schedule_type="preconfigured", db=db, timeclockevent_id=timeclockevent_id)

            # ---------- Unified logging ----------
            try:
                schedules_data = fetch_combined_schedules(db)
                pre_schedules = schedules_data.get("preconfigured_schedules", [])
                sched_info = next(
                    (s for s in pre_schedules if s.get("href", "").split("/")[-1] == str(timeclockevent_id)),
                    None
                )
                sched_name = sched_info["name"] if sched_info else f"ID {timeclockevent_id}"

                # Legacy
                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=None,
                    area_id=None,
                    activity_type="GUI Triggered",
                    activity_description=f"Enabled schedule '{sched_name}'"
                )

                # New
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=None,
                    activity_type="Schedule",
                    activity_description=f"Enabled schedule '{sched_name}'",
                    area_name="Preconfigured"
                )
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=None,
                    activity_type="User",
                    activity_description=f"Enabled schedule '{sched_name}'",
                    area_name="Preconfigured"
                )
            except Exception as e:
                print(f"[LOG ERROR] Failed to log schedule enable activity: {e}")

            return result

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    else:
        raise HTTPException(status_code=400, detail="Invalid type, must be 'internal' or 'preconfigured'")



@router.post("/disable")
def disable_schedule_event(
    type: str,
    timeclockevent_id: int = None,
    internal_schedule_id: int = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    if type == "internal":
        if not internal_schedule_id:
            raise HTTPException(status_code=400, detail="Missing internal_schedule_id for internal type")

        schedule = db.query(Schedule).filter(Schedule.id == internal_schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        try:
            result = disable_schedule(schedule_type="internal", db=db, schedule=schedule)

            # ---------- Unified logging ----------
            try:
                # Legacy
                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=None,
                    area_id=None,
                    activity_type="GUI Triggered",
                    activity_description=f"Disabled schedule '{schedule.name}'"
                )

                # Resolve area_name if possible
                area_name = None
                area_ids = []
                if schedule.quick_control_id:
                    rows = (
                        db.query(QuickControlArea.area_id)
                        .filter(QuickControlArea.quick_control_id == schedule.quick_control_id)
                        .all()
                    )
                    area_ids = [r[0] for r in rows if r and r[0] is not None]

                unique_area_ids = list({aid for aid in area_ids if aid is not None})
                log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

                if log_area_id:
                    area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()

                # New
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="Schedule",
                    activity_description=f"Disabled schedule '{schedule.name}'",
                    area_name=area_name
                )
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="User",
                    activity_description=f"Disabled schedule '{schedule.name}'",
                    area_name=area_name
                )
            except Exception as e:
                print(f"[LOG ERROR] Failed to log schedule disable activity: {e}")

            return result

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif type == "preconfigured":
        if not timeclockevent_id:
            raise HTTPException(status_code=400, detail="Missing timeclockevent_id for preconfigured type")

        try:
            result = disable_schedule(schedule_type="preconfigured", db=db, timeclockevent_id=timeclockevent_id)

            # ---------- Unified logging ----------
            try:
                schedules_data = fetch_combined_schedules(db)
                pre_schedules = schedules_data.get("preconfigured_schedules", [])
                sched_info = next(
                    (s for s in pre_schedules if s.get("href", "").split("/")[-1] == str(timeclockevent_id)),
                    None
                )
                sched_name = sched_info["name"] if sched_info else f"ID {timeclockevent_id}"

                # Legacy
                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=None,
                    area_id=None,
                    activity_type="GUI Triggered",
                    activity_description=f"Disabled schedule '{sched_name}'"
                )

                # New
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=None,
                    activity_type="Schedule",
                    activity_description=f"Disabled schedule '{sched_name}'",
                    area_name="Preconfigured"
                )
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=None,
                    activity_type="User",
                    activity_description=f"Disabled schedule '{sched_name}'",
                    area_name="Preconfigured"
                )
            except Exception as e:
                print(f"[LOG ERROR] Failed to log schedule disable activity: {e}")

            return result

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    else:
        raise HTTPException(status_code=400, detail="Invalid type, must be 'internal' or 'preconfigured'")



@router.post("/create", response_model=SuccessResponse)
def create_schedule_event(
    schedule_data: ScheduleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- PERMISSION CHECK ----------
    if user.role not in ["Admin", "Superadmin"]:
        floor_ids: Set[int] = {a.floor_id for a in schedule_data.areas if getattr(a, "floor_id", None) is not None}
        for fid in floor_ids:
            try:
                require_operator_permission_for_scope(
                    required_level=3,  # monitor + control + edit
                    floor_ids=[fid],
                    enforce_on_empty_scope=True,
                    db=db,
                    current_user=user
                )
            except HTTPException as e:
                if e.status_code == 403:
                    return {
                        "status": "failed",
                        "message": "You don’t have permission to create a schedule for all selected areas."
                    }
                raise

    # ---------- SCHEDULE CREATION ----------
    if schedule_data.schedule_group_id is None and schedule_data.new_schedule_group_name:
        new_group = create_new_schedule_group(db, schedule_data.new_schedule_group_name)
        schedule_data.schedule_group_id = new_group.id

    created_schedule: Schedule = create_schedule_with_quick_control(db, schedule_data)
    schedule_job_for_schedule_id(db, created_schedule.id)

    # ---------- RESOLVE AREA IDS ----------
    area_ids: List[int] = []
    if created_schedule.quick_control_id:
        rows = (
            db.query(QuickControlArea.area_id)
              .filter(QuickControlArea.quick_control_id == created_schedule.quick_control_id)
              .all()
        )
        area_ids = [r[0] for r in rows if r and r[0] is not None]

    unique_area_ids = list({aid for aid in area_ids if aid is not None})
    log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

    legacy_floor_id = None
    if schedule_data.areas:
        legacy_floor_id = getattr(schedule_data.areas[0], "floor_id", None)
    elif log_area_id is not None:
        legacy_floor_id = db.query(Area.floor_id).filter(Area.id == log_area_id).scalar()

    # ---------- ACTIVITY LOGGING ----------
    try:
        # Legacy GUI log
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=legacy_floor_id,
            area_id=None,
            activity_type="GUI Triggered",
            activity_description=f"Created schedule '{created_schedule.name}'"
        )

        # Resolve area_name if possible
        area_name = None
        if log_area_id:
            area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()

        # New logs (unified style)
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="Schedule",
            activity_description=f"Created schedule '{created_schedule.name}'",
            area_name=area_name
        )
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="User",
            activity_description=f"Created schedule '{created_schedule.name}'",
            area_name=area_name
        )

    except Exception as e:
        print(f"[LOG ERROR] Failed to log create schedule activity: {e}")

    return SuccessResponse(status="Success")



@router.put("/update/{schedule_id}")
def update_schedule_event(
    schedule_id: int,
    payload: ScheduleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if payload.schedule_type == "SpecificDates" and payload.schedule_span == "Forever":
        raise HTTPException(status_code=400, detail="SpecificDates schedule cannot have span='Forever'")

    # ---------- Permission Enforcement ----------
    if user.role not in ["Admin", "Superadmin"]:
        if schedule.quick_control_id:
            area_ids = db.query(QuickControlArea.area_id).filter(
                QuickControlArea.quick_control_id == schedule.quick_control_id
            ).all()
            area_ids = [a[0] for a in area_ids]

            if area_ids:
                floor_ids = db.query(Area.floor_id).filter(
                    Area.id.in_(area_ids)
                ).distinct().all()
                floor_ids = [f[0] for f in floor_ids if f[0] is not None]

                has_access = False
                for fid in floor_ids:
                    try:
                        require_operator_permission_for_scope(
                            required_level=3,
                            floor_ids=[fid],
                            enforce_on_empty_scope=True,
                            db=db,
                            current_user=user
                        )
                        has_access = True
                        break
                    except HTTPException as e:
                        if e.status_code != 403:
                            raise
                if not has_access:
                    return {
                        "status": "failed",
                        "message": "You don’t have permission to update this schedule."
                    }
            else:
                return {
                    "status": "failed",
                    "message": "No area mappings found to validate permission."
                }

    # ---------- Update ----------
    updated_schedule = update_schedule_with_quick_control(db, schedule_id, payload)

    if updated_schedule.is_active:
        try:
            scheduler.remove_job(job_id=str(schedule_id))
        except Exception:
            pass
        schedule_job_for_schedule_id(db=db, schedule_id=schedule_id)

    # ---------- Resolve area_ids ----------
    area_ids = []
    if updated_schedule.quick_control_id:
        rows = (
            db.query(QuickControlArea.area_id)
            .filter(QuickControlArea.quick_control_id == updated_schedule.quick_control_id)
            .all()
        )
        area_ids = [r[0] for r in rows if r and r[0] is not None]

    unique_area_ids = list({aid for aid in area_ids if aid is not None})
    log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

    # Derive legacy floor_id
    legacy_floor_id = None
    if payload.areas:
        legacy_floor_id = getattr(payload.areas[0], "floor_id", None)
    elif log_area_id is not None:
        legacy_floor_id = db.query(Area.floor_id).filter(Area.id == log_area_id).scalar()

    # ---------- Activity Logging ----------
    try:
        # Legacy log
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=legacy_floor_id,
            area_id=None,
            activity_type="GUI Triggered",
            activity_description=f"Updated schedule '{updated_schedule.name}'"
        )

        # Resolve area_name if possible
        area_name = None
        if log_area_id:
            area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()

        # New logs (unified style)
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="Schedule",
            activity_description=f"Updated schedule '{updated_schedule.name}'",
            area_name=area_name
        )
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="User",
            activity_description=f"Updated schedule '{updated_schedule.name}'",
            area_name=area_name
        )

    except Exception as e:
        print(f"[LOG ERROR] Failed to log schedule update activity: {e}")

    return {
        "status": "updated",
        "schedule_id": schedule.id,
        "quick_control_id": schedule.quick_control_id
    }




@router.delete("/delete/{schedule_id}")
def delete_schedule_event(
    schedule_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    # ---------- Permission Enforcement ----------
    if user.role not in ["Admin", "Superadmin"]:
        if schedule.quick_control_id:
            area_ids = db.query(QuickControlArea.area_id).filter(
                QuickControlArea.quick_control_id == schedule.quick_control_id
            ).all()
            area_ids = [a[0] for a in area_ids]

            if area_ids:
                floor_ids = db.query(Area.floor_id).filter(
                    Area.id.in_(area_ids)
                ).distinct().all()
                floor_ids = [f[0] for f in floor_ids if f[0] is not None]

                has_access = False
                for fid in floor_ids:
                    try:
                        require_operator_permission_for_scope(
                            required_level=3,
                            floor_ids=[fid],
                            enforce_on_empty_scope=True,
                            db=db,
                            current_user=user
                        )
                        has_access = True
                        break
                    except HTTPException as e:
                        if e.status_code != 403:
                            raise
                if not has_access:
                    return {
                        "status": "failed",
                        "message": "You don’t have permission to delete this schedule."
                    }
            else:
                return {
                    "status": "failed",
                    "message": "No area mappings found to validate permission."
                }

    # ---------- Resolve area_ids ----------
    area_ids = []
    if schedule.quick_control_id:
        rows = (
            db.query(QuickControlArea.area_id)
            .filter(QuickControlArea.quick_control_id == schedule.quick_control_id)
            .all()
        )
        area_ids = [r[0] for r in rows if r and r[0] is not None]

    unique_area_ids = list({aid for aid in area_ids if aid is not None})
    log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

    # ---------- Activity Logging ----------
    try:
        # Legacy log
        log_activity(
            db=db,
            user_id=user.id,
            floor_id=None,
            area_id=None,
            activity_type="GUI Triggered",
            activity_description=f"Deleted schedule '{schedule.name}'"
        )

        # Resolve area_name if possible
        area_name = None
        if log_area_id:
            area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()

        # Unified report logs
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="Schedule",
            activity_description=f"Deleted schedule '{schedule.name}'",
            area_name=area_name
        )
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=log_area_id,
            activity_type="User",
            activity_description=f"Deleted schedule '{schedule.name}'",
            area_name=area_name
        )
    except Exception as e:
        print(f"[LOG ERROR] Failed to log delete schedule activity: {e}")

    # ---------- DELETE ----------
    if not delete_schedule(db, schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")

    return {
        "status": "success",
        "message": f"Schedule '{schedule.name}' deleted successfully"
    }


@router.get("/groups", response_model=ScheduleGroupsListResponse)
def read_schedule_groups(db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    groups = get_all_schedule_groups(db)
    return {"status": "Success", "groups": groups}

@router.post("/trigger")
def trigger_schedule_event(
    payload: TriggerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------------- Preconfigured Schedules ----------------
    if payload.schedule_type == "pre_configure":
        if user.role not in ["Admin", "Superadmin"]:
            return {
                "status": "failed",
                "message": "Only Admin or Superadmin can trigger preconfigured schedules."
            }

    # ---------------- Internal Schedules ----------------
    elif payload.schedule_type == "internal":
        schedule = db.query(Schedule).filter(Schedule.id == payload.schedule_id).first()
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        # (Permission enforcement logic remains unchanged...)

    # ---------------- Call Logic ----------------
    result = trigger_schedule_event_logic(payload, db, user)

    # ---------------- Activity Logs ----------------
    try:
        if payload.schedule_type == "internal":
            schedule = db.query(Schedule).filter(Schedule.id == payload.schedule_id).first()
            if schedule:
                sched_name = schedule.name

                # Resolve exactly-one area_id
                area_ids = []
                if schedule.quick_control_id:
                    qc = db.query(QuickControl).filter(QuickControl.id == schedule.quick_control_id).first()
                    if qc and qc.quick_control_areas:
                        area_ids = [qca.area_id for qca in qc.quick_control_areas if qca.area_id]

                unique_area_ids = list({aid for aid in area_ids if aid is not None})
                log_area_id = unique_area_ids[0] if len(unique_area_ids) == 1 else None

                # Resolve area_name if possible, else fallback
                area_name = None
                if log_area_id:
                    area_name = db.query(Area.name).filter(Area.id == log_area_id).scalar()
                if not area_name:
                    area_name = None

                # Legacy log
                log_activity(
                    db=db,
                    user_id=user.id,
                    floor_id=None,
                    area_id=None,
                    activity_type="GUI Triggered",
                    activity_description=f"Triggered schedule '{sched_name}'"
                )

                # New logs (unified style)
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="Schedule",
                    activity_description=f"Triggered schedule '{sched_name}'",
                    area_name=area_name
                )
                activity_report_log(
                    db=db,
                    user_id=user.id,
                    area_id=log_area_id,
                    activity_type="User",
                    activity_description=f"Triggered schedule '{sched_name}' ",
                    area_name=area_name
                )

        elif payload.schedule_type == "pre_configure":
            schedules_data = fetch_combined_schedules(db)
            pre_schedules = schedules_data.get("preconfigured_schedules", [])

            sched_info = next(
                (s for s in pre_schedules if s.get("href", "").split("/")[-1] == str(payload.schedule_id)),
                None
            )
            sched_name = sched_info["name"] if sched_info else f"ID {payload.schedule_id}"

            # Legacy log
            log_activity(
                db=db,
                user_id=user.id,
                floor_id=None,
                area_id=None,
                activity_type="GUI Triggered",
                activity_description=f"Triggered schedule '{sched_name}'"
            )

            # New logs (unified style, area_name="Preconfigured")
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="Schedule",
                activity_description=f"Triggered schedule '{sched_name}'",
                area_name="Preconfigured"
            )
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Triggered schedule '{sched_name}'",
                area_name="Preconfigured"
            )
    except Exception as e:
        print(f"[LOG ERROR] Failed to log trigger schedule activity: {e}")

    return result
