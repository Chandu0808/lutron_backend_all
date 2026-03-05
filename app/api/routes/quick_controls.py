from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.user_model import User
from app.models.area import Area
from app.dependencies.auth import get_current_user
from app.utils.activity_logger import log_activity
from app.utils.activity_report_logger import activity_report_log
from app.models.quick_controls import QuickControl, QuickControlArea
from app.schemas.quick_controls import (
    QuickControlCreate,
    QuickControlUpdate,
    QuickControlResponse,
    SuccessResponse
)
from app.crud.quick_controls import (
    create_quick_control_entry,
    update_quick_control_entry,
    delete_quick_control,
    get_all_quick_controls,
    get_quick_control_by_id,
    trigger_quick_control_logic
)
from app.dependencies.permissions import require_operator_permission_for_scope
from app.trigger import trigger_quick_control_event


router = APIRouter()

@router.post("/create", response_model=SuccessResponse)
def create_quick_control(
    payload: QuickControlCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Creates a QuickControl entry.
    Admin/Superadmin: always allowed.
    Operators: must have level 3 permission on all involved floors.
    """

    # ---------- Permission Check ----------
    if user.role not in ["Admin", "Superadmin"]:
        floor_ids = set()

        for area_payload in payload.areas:
            area = db.query(Area).filter(Area.id == area_payload.area_id).first()
            if area and area.floor_id is not None:
                floor_ids.add(area.floor_id)

        if not floor_ids:
            return {
                "status": "failed",
                "message": "No valid area-floor mapping found for permission validation."
            }

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
                has_access = True  # If any one floor passes, break early
            except HTTPException as e:
                if e.status_code != 403:
                    raise

        if not has_access:
            return {
                "status": "failed",
                "message": "You don’t have permission to create QuickControl on the selected floor(s)."
            }

    # ---------- Creation ----------
    quick_control = create_quick_control_entry(db, payload)
    if quick_control is None:
        raise HTTPException(status_code=400, detail="QuickControl with the same name already exists")

    # ---------- Activity Logging ----------
    try:
        from collections import OrderedDict
        area_ids = []
        floors_for_areas = OrderedDict()  # preserve discovery order
        for ap in payload.areas:
            a = db.query(Area).filter(Area.id == ap.area_id).first()
            if a:
                area_ids.append(a.id)
                if a.floor and a.floor.name not in floors_for_areas:
                    floors_for_areas[a.floor.name] = a.floor.id if a.floor_id is not None else None

        unique_area_ids = list(OrderedDict.fromkeys(area_ids))
        unique_floor_names = list(floors_for_areas.keys())

        single_area = len(unique_area_ids) == 1
        single_floor = len(unique_floor_names) == 1

        from app.utils.activity_report_logger import activity_report_log

        # -------- QuickControl log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="QuickControl",
                activity_description=f"Created QuickControl '{payload.name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Created QuickControl '{payload.name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Created QuickControl '{payload.name}'",
                area_name="Multiple Floors"
            )

        # -------- User log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="User",
                activity_description=f"Created QuickControl '{payload.name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Created QuickControl '{payload.name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Created QuickControl '{payload.name}'",
                area_name="Multiple Floors"
            )

        # -------- Legacy GUI log (keep one entry for backward compatibility) --------
        log_activity(
            db=db,
            user_id=user.id,
            activity_type="GUI Triggered",
            activity_description=f"Created QuickControl '{payload.name}'"
        )

    except Exception as e:
        print(f"[LOG ERROR] Failed to log activity: {e}")

    return {
        "status": "success",
        "message": f"QuickControl '{payload.name}' created successfully",
        "id": quick_control.id
    }




@router.put("/update/{control_id}", response_model=SuccessResponse)
def update_quick_control(
    control_id: int,
    payload: QuickControlUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Updates a QuickControl.
    Admin/Superadmin: always allowed.
    Operators: must have level 3 permission on all involved floors.
    """

    # ---------- Permission Check ----------
    if user.role not in ["Admin", "Superadmin"]:
        floor_ids = set()

        for area_payload in payload.areas:
            area = db.query(Area).filter(Area.id == area_payload.area_id).first()
            if area and area.floor_id is not None:
                floor_ids.add(area.floor_id)

        if not floor_ids:
            return {
                "status": "failed",
                "message": "No valid area-floor mapping found for permission validation."
            }

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
            except HTTPException as e:
                if e.status_code != 403:
                    raise

        if not has_access:
            return {
                "status": "failed",
                "message": "You don’t have permission to update QuickControl for the selected floor(s)."
            }

    # ---------- Activity Logs ----------
    try:
        from collections import OrderedDict
        from app.utils.activity_report_logger import activity_report_log

        area_ids = []
        floors_for_areas = OrderedDict()
        for ap in payload.areas:
            a = db.query(Area).filter(Area.id == ap.area_id).first()
            if a:
                area_ids.append(a.id)
                if a.floor and a.floor.name not in floors_for_areas:
                    floors_for_areas[a.floor.name] = a.floor.id if a.floor_id is not None else None

        unique_area_ids = list(OrderedDict.fromkeys(area_ids))
        unique_floor_names = list(floors_for_areas.keys())

        single_area = len(unique_area_ids) == 1
        single_floor = len(unique_floor_names) == 1

        # -------- QuickControl log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="QuickControl",
                activity_description=f"Updated QuickControl '{payload.name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Updated QuickControl '{payload.name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Updated QuickControl '{payload.name}'",
                area_name="Multiple Floors"
            )

        # -------- User log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="User",
                activity_description=f"Updated QuickControl '{payload.name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Updated QuickControl '{payload.name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Updated QuickControl '{payload.name}'",
                area_name="Multiple Floors"
            )

        # -------- Legacy GUI log (simplified) --------
        log_activity(
            db=db,
            user_id=user.id,
            area_id=None,
            floor_id=None,
            activity_type="GUI Triggered",
            activity_description=f"Updated QuickControl '{payload.name}'"
        )

    except Exception as e:
        print(f"[LOG ERROR] Failed to log activity: {e}")

    # Update the quick control
    updated_qc = update_quick_control_entry(db, control_id, payload)
    
    return {
        "status": "success",
        "message": f"QuickControl '{payload.name}' updated successfully",
        "id": updated_qc.get("id")
    }



@router.get("/list", response_model=list[QuickControlResponse])
def list_all_quick_controls(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Admin/Superadmin: full list.
    Operator: only QuickControls with areas on permitted floors (level 1).
    """
    all_qcs = get_all_quick_controls(db)

    if user.role in ["Admin", "Superadmin"]:
        return all_qcs

    permitted_qcs = []

    for qc in all_qcs:
        area_ids = db.query(QuickControlArea.area_id).filter(
            QuickControlArea.quick_control_id == qc.id
        ).all()
        area_ids = [a[0] for a in area_ids]

        if not area_ids:
            continue

        floor_ids = db.query(Area.floor_id).filter(
            Area.id.in_(area_ids)
        ).distinct().all()
        floor_ids = [f[0] for f in floor_ids if f[0] is not None]

        for fid in floor_ids:
            try:
                require_operator_permission_for_scope(
                    required_level=1,
                    floor_ids=[fid],
                    enforce_on_empty_scope=True,
                    db=db,
                    current_user=user
                )
                permitted_qcs.append(qc)
                break
            except HTTPException as e:
                if e.status_code != 403:
                    raise

    return permitted_qcs

@router.get("/details/{control_id}")
def get_quick_control_details(
    control_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    control = get_quick_control_by_id(db, control_id)
    if not control:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    return {
        "id": control.id,
        "name": control.name,
        "quick_control_areas": [area.to_dict() for area in control.quick_control_areas]
    }

@router.post("/trigger/{quick_control_id}")
def trigger_quick_control(
    quick_control_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    qc = db.query(QuickControl).filter(QuickControl.id == quick_control_id).first()
    if not qc:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    # ---------- Permission Enforcement ----------
    if user.role not in ["Admin", "Superadmin"]:
        area_ids = db.query(QuickControlArea.area_id).filter(
            QuickControlArea.quick_control_id == quick_control_id
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
                        required_level=2,   # Control-level trigger
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
                    "message": "You don’t have permission to trigger this QuickControl."
                }
        else:
            return {
                "status": "failed",
                "message": "No area mappings found to validate permission."
            }

    # ---------- Activity Logging ----------
    try:
        from collections import OrderedDict
        from app.utils.activity_report_logger import activity_report_log

        qc_name = qc.name if qc else f"ID {quick_control_id}"

        qc_areas = db.query(QuickControlArea).filter(
            QuickControlArea.quick_control_id == quick_control_id
        ).all()

        area_ids = []
        floors_for_areas = OrderedDict()
        for qca in qc_areas:
            a = db.query(Area).filter(Area.id == qca.area_id).first()
            if a:
                area_ids.append(a.id)
                if a.floor and a.floor.name not in floors_for_areas:
                    floors_for_areas[a.floor.name] = a.floor.id if a.floor_id is not None else None

        unique_area_ids = list(OrderedDict.fromkeys(area_ids))
        unique_floor_names = list(floors_for_areas.keys())

        single_area = len(unique_area_ids) == 1
        single_floor = len(unique_floor_names) == 1

        # -------- QuickControl log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="QuickControl",
                activity_description=f"Triggered QuickControl '{qc_name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Triggered QuickControl '{qc_name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Triggered QuickControl '{qc_name}'",
                area_name="Multiple Floors"
            )

        # -------- User log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="User",
                activity_description=f"Triggered QuickControl '{qc_name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Triggered QuickControl '{qc_name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Triggered QuickControl '{qc_name}'",
                area_name="Multiple Floors"
            )

    except Exception:
        pass

    # ---------- Proceed with Trigger ----------
    return trigger_quick_control_event(quick_control_id, db, user)




@router.delete("/delete/{control_id}")
def delete_quick_control_endpoint(
    control_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    qc = db.query(QuickControl).filter(QuickControl.id == control_id).first()
    if not qc:
        raise HTTPException(status_code=404, detail="QuickControl not found")

    # ---------- Permission Enforcement ----------
    if user.role not in ["Admin", "Superadmin"]:
        area_ids = db.query(QuickControlArea.area_id).filter(
            QuickControlArea.quick_control_id == control_id
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
                    "message": "You don’t have permission to delete this QuickControl."
                }
        else:
            return {
                "status": "failed",
                "message": "No area mappings found to validate permission."
            }

    # ---------- Activity Logging ----------
    try:
        from collections import OrderedDict
        from app.utils.activity_report_logger import activity_report_log

        qc_name = qc.name if qc else f"ID {control_id}"

        # collect all areas linked
        qc_areas = db.query(QuickControlArea).filter(
            QuickControlArea.quick_control_id == control_id
        ).all()

        area_ids = []
        floors_for_areas = OrderedDict()
        for qca in qc_areas:
            a = db.query(Area).filter(Area.id == qca.area_id).first()
            if a:
                area_ids.append(a.id)
                if a.floor and a.floor.name not in floors_for_areas:
                    floors_for_areas[a.floor.name] = a.floor.id if a.floor_id is not None else None

        unique_area_ids = list(OrderedDict.fromkeys(area_ids))
        unique_floor_names = list(floors_for_areas.keys())

        single_area = len(unique_area_ids) == 1
        single_floor = len(unique_floor_names) == 1

        # -------- QuickControl log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="QuickControl",
                activity_description=f"Deleted QuickControl '{qc_name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Deleted QuickControl '{qc_name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="QuickControl",
                activity_description=f"Deleted QuickControl '{qc_name}'",
                area_name="Multiple Floors"
            )

        # -------- User log --------
        if single_area:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=unique_area_ids[0],
                activity_type="User",
                activity_description=f"Deleted QuickControl '{qc_name}'"
            )
        elif single_floor:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Deleted QuickControl '{qc_name}'",
                area_name=unique_floor_names[0]
            )
        else:
            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Deleted QuickControl '{qc_name}'",
                area_name="Multiple Floors"
            )

        # -------- Legacy GUI log (simplified) --------
        log_activity(
            db=db,
            user_id=user.id,
            area_id=None,
            floor_id=None,
            activity_type="GUI Triggered",
            activity_description=f"Deleted QuickControl '{qc_name}'"
        )

    except Exception as e:
        print(f"[LOG ERROR] Failed to log delete activity: {e}")

    # ---------- Deletion ----------
    delete_quick_control(db, control_id)
    
    return {
        "status": "success",
        "message": f"QuickControl '{qc_name}' deleted successfully"
    }

