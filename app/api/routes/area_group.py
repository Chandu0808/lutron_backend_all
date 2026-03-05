from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from app.schemas.area_group import AreaGroupCreate, AreaGroupMappingCreate, AreaGroupOut, AreaGroupUpdateRequest, AreaGroupArea, CombinedAreaGroupCreate, AreaGroupListOut
from app.database.session import get_db
from app.crud import area_group as crud
from fastapi.responses import StreamingResponse
from app.crud.area_group import upload_area_group_csv, generate_area_group_csv
from app.models.area_group import AreaGroup, AreaGroupMapping
from app.models.area import Area
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.utils.activity_logger import log_activity
from app.dependencies.permissions import require_operator_permission_for_scope


router = APIRouter()

@router.post("/create", response_model=dict)
def create_area_group_with_areas(
    data: CombinedAreaGroupCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    # Collect all floor_ids from the request (via the areas inside floors)
    floor_ids = set()
    for floor in data.floors:
        for area_id in floor.area_ids:  # area_ids contains area IDs
            area = db.query(Area).filter(Area.id == area_id).first()
            if area and area.floor_id:
                floor_ids.add(area.floor_id)

    try:
        # Permission check — Operators must have edit permission on all involved floors
        require_operator_permission_for_scope(
            required_level=3,                # 3 = monitor_control_edit
            floor_ids=list(floor_ids),       # enforce permission check for all floors
            enforce_on_empty_scope=True,
            db=db,
            current_user=current_user
        )
    except HTTPException as e:
        if e.status_code == 403:
            # User-friendly: return structured failure instead of hard error
            return {
                "status": "failed",
                "message": "Not authorized to create area group for one or more selected floors"
            }
        raise   # re-raise any unexpected error

    # Proceed with group creation
    result = crud.create_group_with_area_codes(data, db, current_user)
    group_id = result.get("group_id")

    assigned_area_names = []
    floor_names = set()

    # Collect all areas and floors again (for logging)
    for floor in data.floors:
        for area_id in floor.area_ids:  # area_ids contains area IDs
            area = db.query(Area).filter(Area.id == area_id).first()
            if area:
                assigned_area_names.append(area.name)
                if area.floor:
                    floor_names.add(area.floor.name)

    # Decide floor info for logging
    if len(floor_ids) == 1:
        floor_id_value = next(iter(floor_ids))
        floor_name_value = next(iter(floor_names))
    else:
        floor_id_value = None
        floor_name_value = "Multiple Floors" if floor_ids else None

    # Build activity description
    areas_str = ", ".join(assigned_area_names) if assigned_area_names else "No areas assigned"
    description = f"New area group '{data.name}' created"

    # -------- Legacy GUI log --------
    log_activity(
        db=db,
        user_id=current_user.id,
        floor_id=floor_id_value,
        activity_type="GUI Triggered",
        activity_description=description
    )

    # -------- New activity_report logs --------
    try:
        from app.utils.activity_report_logger import activity_report_log

        # First log (AreaGroup)
        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=None,
            activity_type="AreaGroup",
            activity_description=description,
            area_name=floor_name_value
        )

        # Second log (User) — fixed to avoid repetition
        if floor_name_value:
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Created area group '{data.name}'",
                area_name=floor_name_value
            )
        else:
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Created area group '{data.name}'",
                area_name="Multiple Floors"
            )

    except Exception as e:
        pass  # Silently fail activity logging

    return result



@router.get("/list", response_model=AreaGroupListOut)
def list_area_groups(db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    return crud.get_area_groups(db)

@router.get("/get/{group_id}", response_model=dict)
def get_area_group_by_id(group_id: int, db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    group = db.query(AreaGroup).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    areas = []
    for m in group.mappings:
        area = db.query(Area).filter(Area.id == m.area_id).first()
        if area:
            areas.append({
                "id": area.id,
                "name": area.name,
                "code": area.code,
                "floor_id": area.floor_id,
                "floor_name": area.floor.name if area.floor else None,
                "processor_id": area.processor_id
            })

    return {
        "name": group.name,
        "special": group.special,
        "areas": areas
    }

@router.put("/update/{group_id}")
def update_area_group(
    group_id: int,
    payload: AreaGroupUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    group = db.query(AreaGroup).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # ---------- Permission Enforcement ----------
    if current_user.role not in ["Admin", "Superadmin"]:
        floor_ids = {floor.floor_id for floor in payload.floors if floor.floor_id is not None}
        for fid in floor_ids:
            try:
                require_operator_permission_for_scope(
                    required_level=3,  # edit level
                    floor_ids=[fid],
                    enforce_on_empty_scope=True,
                    db=db,
                    current_user=current_user
                )
            except HTTPException as e:
                if e.status_code == 403:
                    return {
                        "status": "failed",
                        "message": "You don't have permission to edit area groups on one or more selected floors."
                    }
                raise

    # ---------- Only Superadmin can mark group as special ----------
    if payload.special and current_user.role != "Superadmin":
        raise HTTPException(status_code=403, detail="Only Superadmin can make a group special")

    # ---------- Validate special assignment conflicts ----------
    if payload.special:
        for floor in payload.floors:
            for area_id in floor.area_ids:
                area = db.query(Area).filter(Area.id == area_id).first()
                if not area:
                    raise HTTPException(status_code=400, detail=f"Area ID {area_id} not found in database")

                existing = (
                    db.query(AreaGroupMapping, AreaGroup)
                    .join(AreaGroup, AreaGroup.id == AreaGroupMapping.group_id)
                    .filter(
                        AreaGroupMapping.area_id == area.id,
                        AreaGroupMapping.group_id != group_id,
                        AreaGroup.special == True
                    )
                    .first()
                )
                
                if existing:
                    existing_map, existing_group = existing
                    raise HTTPException(
                        status_code=400,
                        detail=f"Area '{area.name}' (code: {area.code}, Processor ID: {area.processor_id}) is already part of special group '{existing_group.name}'"
                    )

    # ---------- Update group info ----------
    group.name = payload.name
    group.special = payload.special

    db.query(AreaGroupMapping).filter_by(group_id=group_id).delete()

    for floor in payload.floors:
        for area_id in floor.area_ids:
            area = db.query(Area).filter(Area.id == area_id).first()
            if not area:
                raise HTTPException(status_code=400, detail=f"Area ID {area_id} not found")

            db.add(AreaGroupMapping(
                group_id=group.id,
                area_id=area.id,
                floor_id=floor.floor_id
            ))

    db.commit()

    assigned_area_names = []
    floor_ids = set()
    floor_names = set()

    for fl in payload.floors:
        for area_id in fl.area_ids:  # area_ids contains area IDs
            area = db.query(Area).filter(Area.id == area_id).first()
            if area:
                assigned_area_names.append(area.name)
                if area.floor:
                    floor_ids.add(area.floor.id)
                    floor_names.add(area.floor.name)

    if len(floor_ids) == 1:
        floor_id_value = next(iter(floor_ids))
        floor_name_value = next(iter(floor_names))
    else:
        floor_id_value = None
        floor_name_value = "Multiple Floors" if floor_ids else None

    areas_str = ", ".join(assigned_area_names) if assigned_area_names else "No areas assigned"
    description = f"Area group '{group.name}' updated"

    # -------- Legacy GUI log --------
    log_activity(
        db=db,
        user_id=current_user.id,
        floor_id=floor_id_value,
        activity_type="GUI Triggered",
        activity_description=description
    )

    # -------- New activity_report logs --------
    try:
        from app.utils.activity_report_logger import activity_report_log

        # First log (AreaGroup)
        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=None,
            activity_type="AreaGroup",
            activity_description=description,
            area_name=floor_name_value
        )

        # Second log (User) — fixed
        if floor_name_value:
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Updated area group '{group.name}'",
                area_name=floor_name_value
            )
        else:
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Updated area group '{group.name}'",
                area_name="Multiple Floors"
            )

    except Exception as e:
        pass  # Silently fail activity logging

    return {"message": "Area group updated successfully"}



@router.delete("/delete/{group_id}")
def delete_area_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    group = db.query(AreaGroup).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # ---------- Get mappings before deletion ----------
    mappings = db.query(AreaGroupMapping).filter_by(group_id=group_id).all()

    assigned_area_names = []
    floor_ids = set()
    floor_names = set()

    for mapping in mappings:
        area = db.query(Area).filter(Area.id == mapping.area_id).first()
        if area:
            assigned_area_names.append(area.name)
            if area.floor:
                floor_ids.add(area.floor.id)
                floor_names.add(area.floor.name)

    # ---------- User-friendly permission check ----------
    if current_user.role not in ["Admin", "Superadmin"]:
        if not floor_ids:
            return {
                "status": "failed",
                "message": "No floors found to validate permission for this area group."
            }

        for fid in floor_ids:
            try:
                require_operator_permission_for_scope(
                    required_level=3,
                    floor_ids=[fid],
                    enforce_on_empty_scope=True,
                    db=db,
                    current_user=current_user
                )
            except HTTPException as e:
                if e.status_code == 403:
                    return {
                        "status": "failed",
                        "message": "You don’t have permission to delete this area group (missing edit access to one or more floors)."
                    }
                raise

    # ---------- Proceed with deletion ----------
    db.query(AreaGroupMapping).filter_by(group_id=group_id).delete()
    db.delete(group)
    db.commit()

    # ---------- Logging ----------
    if len(floor_ids) == 1:
        floor_id_value = next(iter(floor_ids))
        floor_name_value = next(iter(floor_names))
    else:
        floor_id_value = None
        floor_name_value = "Multiple Floors" if floor_ids else None

    areas_str = ", ".join(assigned_area_names) if assigned_area_names else "No areas assigned"
    description = f"Area group '{group.name}' deleted"

    # -------- Legacy GUI log --------
    log_activity(
        db=db,
        user_id=current_user.id,
        floor_id=floor_id_value,
        activity_type="GUI Triggered",
        activity_description=description
    )

    # -------- New activity_report logs --------
    try:
        from app.utils.activity_report_logger import activity_report_log

        # First log (AreaGroup)
        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=None,
            activity_type="AreaGroup",
            activity_description=description,
            area_name=floor_name_value
        )

        # Second log (User) — fixed
        if floor_name_value and floor_name_value != "Multiple Floors":
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Deleted area group '{group.name}'",
                area_name=floor_name_value
            )
        else:
            activity_report_log(
                db=db,
                user_id=current_user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"Deleted area group '{group.name}'",
                area_name="Multiple Floors"
            )

    except Exception as e:
        pass  # Silently fail activity logging

    return {"message": "Area group deleted successfully"}




@router.post("/upload_csv", response_model=dict)
def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    upload_area_group_csv(file, db)
    return {"status": "success"}

@router.get("/download_csv")
def download_csv(db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    csv_content = generate_area_group_csv(db)
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=area_groups_template.csv"}
    )
