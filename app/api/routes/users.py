#E:\Gcon\lutron\lutron_backend\app\api\routes\users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.schemas.user import UserCreate, UserUpdate
from app.crud.user import create_user, get_users, delete_user_by_id, update_user
from app.database.session import get_db
from app.dependencies.auth import require_admin
from typing import Optional
from app.core.security import get_password_hash
from app.models.user_model import User
from app.utils.activity_logger import log_activity
from app.dependencies.permissions import require_operator_permission_for_scope
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import can_create_role, can_manage_user_for_update


router = APIRouter()

@router.post("/create")
def create(
    user: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Your existing gate — ensure SuperAdmin passes this check too
    require_operator_permission_for_scope(
        required_level=4,
        db=db,
        current_user=current_user
    )

    # Explicit rule check (fast fail with 403)
    if not can_create_role(current_user.role, user.role):
        raise HTTPException(
            status_code=403,
            detail=f"{current_user.role} is not permitted to create a {user.role}"
        )

    try:
        new_user = create_user(db, user, created_by=current_user)

        # ---------- Activity Logs ----------
        try:
            # Existing log
            log_activity(
                db=db,
                user_id=new_user.id,
                activity_type="GUI Triggered",
                activity_description=f"New user {new_user.name} was created with role {new_user.role}."
            )

            # New activity_report_log
            from app.utils.activity_report_logger import activity_report_log
            activity_report_log(
                db=db,
                user_id=current_user.id,   # the one performing the action
                area_id=None,              # user creation is not tied to area
                activity_type="User",
                activity_description=f"New user {new_user.name} was created with role {new_user.role}."
            )

        except Exception as log_error:
            print(f"[LOG ERROR] Failed to log user creation activity: {log_error}")

        # ---------- Response ----------
        return {
            "status": "success",
            "message": "User created successfully",
            "data": {
                "id": new_user.id,
                "name": new_user.name,
                "email": new_user.email,
                "role": new_user.role,
                "user_permissions": [
                    {"floor_id": up.floor_id, "permission_type": up.permission_type}
                    for up in (new_user.user_permissions or [])
                ]
            }
        }
    except Exception as e:
        error_message = str(e)
        # Return specific error message for user already exists
        if "user already exists" in error_message.lower():
            raise HTTPException(status_code=400, detail="user already exists")
        if "username already exists" in error_message.lower():
            raise HTTPException(status_code=400, detail="username already exists")
        raise HTTPException(status_code=400, detail=error_message)

@router.get("")
def list_users(db: Session = Depends(get_db)):
    users = get_users(db)

    payload = []
    for u in users:
        data = {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
        }

        # Only add user_permissions for Operators
        if u.role == "Operator":
            data["user_permissions"] = [
                {
                    "floor_id": up.floor_id,
                    "floor_name": up.floor.name if up.floor else None,
                    "permission_type": up.permission_type
                }
                for up in (u.user_permissions or [])
            ]

        payload.append(data)

    return {
        "status": "success",
        "count": len(payload),
        "data": payload
    }


@router.patch("/update")
def patch_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update an active user by id (``user_id`` query parameter). ``role`` is immutable.

    ``name`` and ``email`` may be updated when provided (each unique among active users).

    Operator targets may receive ``permissions`` / ``floor`` to replace all floor assignments;
    for Admin/Superadmin targets that field must be omitted.
    """
    require_operator_permission_for_scope(
        required_level=4,
        db=db,
        current_user=current_user,
    )

    target = (
        db.query(User)
        .filter(User.id == user_id, User.is_active == True)
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if not can_manage_user_for_update(current_user.role, target.role):
        raise HTTPException(
            status_code=403,
            detail=f"{current_user.role} is not permitted to update this user",
        )

    if body.permissions is not None and target.role != "Operator":
        raise HTTPException(
            status_code=422,
            detail="permissions may only be provided for Operator users",
        )

    if (
        body.name is None
        and body.password is None
        and body.permissions is None
        and body.email is None
    ):
        raise HTTPException(
            status_code=422,
            detail="At least one of name, email, password, or permissions must be provided",
        )

    try:
        updated = update_user(db, user_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    data = {
        "id": updated.id,
        "name": updated.name,
        "email": updated.email,
        "role": updated.role,
    }
    if updated.role == "Operator":
        data["user_permissions"] = [
            {
                "floor_id": up.floor_id,
                "floor_name": up.floor.name if up.floor else None,
                "permission_type": up.permission_type,
            }
            for up in (updated.user_permissions or [])
        ]

    try:
        log_activity(
            db=db,
            user_id=updated.id,
            activity_type="GUI Triggered",
            activity_description=(
                f"User {updated.name} (ID: {updated.id}) was updated."
            ),
        )
        from app.utils.activity_report_logger import activity_report_log

        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=None,
            activity_type="User",
            activity_description=(
                f"User {updated.name} (ID: {updated.id}) was updated."
            ),
        )
    except Exception as log_error:
        print(f"[LOG ERROR] Failed to log user update activity: {log_error}")

    return {
        "status": "success",
        "message": "User updated successfully",
        "data": data,
    }


@router.put("/{email}")
def legacy_put_user_by_email(
    email: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    db: Session = Depends(get_db),
    Admin: User = Depends(require_admin),
):
    """
    Legacy password update by email. Role changes are not supported; use admin workflows
    or future role-specific APIs. Prefer ``PATCH /users/update?user_id=`` for name,
    password, and Operator permissions.
    """
    if role is not None:
        raise HTTPException(
            status_code=422,
            detail="Role cannot be changed via this endpoint.",
        )
    user = db.query(User).filter(
        User.email == email,
        User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if password:
        user.hashed_password = get_password_hash(password)

    db.commit()
    db.refresh(user)
    return {
        "email": user.email,
        "role": user.role,
    }


@router.post("/delete")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # ---------- Permission Check ----------
    require_operator_permission_for_scope(
        required_level=4,   # Admin/Superadmin only
        db=db,
        current_user=current_user
    )

    # ---------- Delete User ----------
    user = delete_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ---------- Activity Logs ----------
    try:
        # Existing log
        log_activity(
            db=db,
            user_id=current_user.id,
            activity_type="GUI Triggered",
            activity_description=f"User {user.name} (ID: {user.id}) was deleted."
        )

        # New activity_report_log
        from app.utils.activity_report_logger import activity_report_log
        activity_report_log(
            db=db,
            user_id=current_user.id,   # the one performing deletion
            area_id=None,              # not tied to an area
            activity_type="User",
            activity_description=f"User {user.name} (ID: {user.id}) was deleted."
        )

    except Exception as log_error:
        print(f"[LOG ERROR] Failed to log user deletion activity: {log_error}")

    return {
        "status": "success",
        "deleted_id": user_id,
        "message": f"User {user.name} deleted successfully"
    }
