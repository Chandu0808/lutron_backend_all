# app/dependencies/permissions.py
from fastapi import Depends, HTTPException, status
from enum import Enum
from typing import Set, Dict
from sqlalchemy.orm import Session
from typing import List, Optional, Tuple
from app.database.session import get_db
from app.models.user_model import User, UserPermission
from app.models.area import Area
from app.dependencies.auth import get_current_user



PERMISSION_HIERARCHY = {
    "monitor": 1,
    "monitor_control": 2,
    "monitor_control_edit": 3
}

def require_operator_permission_for_scope(
    *,
    required_level: int,                        # 1=view, 2=control, 3=edit, 5=superadmin-only
    area_ids: Optional[List[int]] = None,
    floor_ids: Optional[List[int]] = None,
    enforce_on_empty_scope: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Enforces Operator permissions for the provided scope (areas and/or floors).
    - Superadmin: bypass (always allowed)
    - Admin: bypass *except* when required_level=5
    - Operator: must have >= required_level on *every* floor in scope
    - If no scope is provided and enforce_on_empty_scope=False, the check is skipped
    """

    # --- Superadmin bypass (always allowed) ---
    if current_user.role == "Superadmin":
        return current_user

    # --- Superadmin-only check ---
    if required_level == 5:
        # Only Superadmin allowed here
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to Superadmin only."
        )

    # --- Admin bypass for normal levels ---
    if current_user.role == "Admin":
        return current_user

    # --- Operators only from here ---
    if current_user.role != "Operator":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User role not allowed."
        )

    # Resolve floors from areas if needed
    resolved_floor_ids: List[int] = list(floor_ids or [])
    if not resolved_floor_ids and area_ids:
        resolved_floor_ids = [
            fid for (fid,) in (
                db.query(Area.floor_id)
                .filter(Area.id.in_(area_ids))
                .distinct()
                .all()
            )
        ]

    # If still no scope → skip or enforce
    if not resolved_floor_ids:
        if enforce_on_empty_scope:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No floors found for the given scope."
            )
        return current_user

    # Build user's floor -> level map
    user_perms = (
        db.query(UserPermission.floor_id, UserPermission.permission_type)
        .where(UserPermission.user_id == current_user.id)
        .all()
    )
    user_level_by_floor = {
        fid: PERMISSION_HIERARCHY.get(ptype, 0) for fid, ptype in user_perms
    }

    # Check every floor in the scope
    missing: List[int] = []
    insufficient: List[Tuple[int, int]] = []
    for fid in resolved_floor_ids:
        if fid not in user_level_by_floor:
            missing.append(fid)
        else:
            lvl = user_level_by_floor[fid]
            if lvl < required_level:
                insufficient.append((fid, lvl))

    if missing or insufficient:
        details = {
            "message": "Operator lacks access/permission for requested floors.",
            "required_level": required_level,
            "missing_floor_ids": missing,
            "insufficient_floor_ids": [fid for fid, _ in insufficient],
        }
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=details
        )

    return current_user





# Canonical role names used across the app
ROLE_SUPERADMIN = "Superadmin"
ROLE_ADMIN = "Admin"
ROLE_OPERATOR = "Operator"

# Map any incoming string to canonical role (case/spacing resilient)
_CANON_MAP = {
    "superadmin": ROLE_SUPERADMIN,
    "super admin": ROLE_SUPERADMIN,
    "admin": ROLE_ADMIN,
    "operator": ROLE_OPERATOR,
}

# Who can create whom (updated: SuperAdmin cannot create SuperAdmin)
_ALLOWED_CREATIONS: Dict[str, Set[str]] = {
    ROLE_SUPERADMIN: {ROLE_ADMIN, ROLE_OPERATOR},
    ROLE_ADMIN:      {ROLE_OPERATOR},
    ROLE_OPERATOR:   set(),
}

def _canon(role: str) -> str:
    if not isinstance(role, str):
        return ""
    key = role.strip().lower()
    return _CANON_MAP.get(key, role.strip())  # fall back to original if unknown

def can_create_role(creator_role: str, target_role: str) -> bool:
    """
    Return True iff a user with role `creator_role` is allowed to create a user with `target_role`.
    This is case-insensitive and trims whitespace.
    """
    c = _canon(creator_role)
    t = _canon(target_role)
    return t in _ALLOWED_CREATIONS.get(c, set())


def can_manage_user_for_update(actor_role: str, target_role: str) -> bool:
    """
    Who may PATCH-update an existing user (email and role are immutable on that endpoint).

    Mirrors creation reach: Superadmin may change Admin, Operator, or another Superadmin;
    Admin may change Operator only; Operators cannot manage other users via this API.

    Self-edit is allowed when the actor is permitted for the target role (e.g. Superadmin
    editing own name/password). Role elevation is impossible because role is not accepted.
    """
    a = _canon(actor_role)
    t = _canon(target_role)
    if a == ROLE_OPERATOR:
        return False
    if a == ROLE_SUPERADMIN:
        return True
    if a == ROLE_ADMIN:
        return t == ROLE_OPERATOR
    return False