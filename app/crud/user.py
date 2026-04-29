# app/crud/user.py
from sqlalchemy.orm import Session, joinedload
from app.models.user_model import User, UserPermission
from app.core.security import get_password_hash, verify_password
from app.schemas.user import UserCreate, UserUpdate
from app.models.floor import Floor
from app.dependencies.permissions import can_create_role 
from app.models.area import Area 


def create_user(db: Session, user: UserCreate, created_by: User):
    # Enforce who can create whom (resilient to case/spacing)
    if not can_create_role(created_by.role, user.role):
        raise Exception(f"{created_by.role} is not permitted to create a {user.role}")

    # Check if user exists with is_active=True
    existing_active = db.query(User).filter(
        User.email == user.email,
        User.is_active == True
    ).first()
    if existing_active:
        raise Exception("user already exists")

    existing_name = db.query(User).filter(
        User.name == user.name,
        User.is_active == True,
    ).first()
    if existing_name:
        raise Exception("username already exists")

    # If user exists with is_active=False, allow creation (new user with same email)
    # Note: Multiple users can have same email, but only one should be active at a time

    db_user = User(
        name=user.name,
        email=user.email,
        hashed_password=get_password_hash(user.password),
        role=user.role,
        change_password=True,
        is_active=True
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    if db_user.role == "Operator" and getattr(user, "permissions", None):
        for p in user.permissions:
            db.add(UserPermission(
                user_id=db_user.id,
                floor_id=p.floor_id,
                permission_type=p.floor_permission
            ))
        db.commit()
        db.refresh(db_user)
        _ = db_user.user_permissions

    return db_user

def authenticate_user(db: Session, username: str, password: str):
    user = db.query(User).filter(
        User.name == username,
        User.is_active == True,
    ).first()
    if user and verify_password(password, user.hashed_password):
        return user
    return None

def get_users(db: Session):
    return (
        db.query(User)
        .filter(User.is_active == True)
        .options(
            joinedload(User.user_permissions).joinedload(UserPermission.floor)
        )
        .all()
    )

def get_user_by_email(db: Session, email: str):
    return (
        db.query(User)
        .options(
            joinedload(User.user_permissions).joinedload(UserPermission.floor)
        )
        .filter(
            User.email == email,
            User.is_active == True
        )
        .first()
    )


def get_active_user_by_id(db: Session, user_id: int):
    return (
        db.query(User)
        .filter(User.id == user_id, User.is_active == True)
        .options(
            joinedload(User.user_permissions).joinedload(UserPermission.floor)
        )
        .first()
    )


def update_user(db: Session, user_id: int, payload: UserUpdate):
    """
    Apply partial updates for an active user. Role is not modified here.

    When ``payload.name`` or ``payload.email`` is set to a new value, it must not
    match another active user for that field.

    When ``payload.permissions`` is set and the user is an Operator, existing
    ``user_permissions`` rows are removed and replaced in one transaction.
    """
    user = get_active_user_by_id(db, user_id)
    if not user:
        return None

    if payload.name is not None:
        new_name = str(payload.name).strip()
        if new_name != user.name:
            existing_name = (
                db.query(User)
                .filter(
                    User.name == new_name,
                    User.is_active == True,
                    User.id != user.id,
                )
                .first()
            )
            if existing_name:
                raise ValueError("username already exists")
        user.name = new_name
    if payload.email is not None:
        new_email = str(payload.email).strip()
        if new_email != user.email:
            existing_active = (
                db.query(User)
                .filter(
                    User.email == new_email,
                    User.is_active == True,
                    User.id != user.id,
                )
                .first()
            )
            if existing_active:
                raise ValueError("user already exists")
            user.email = new_email
    if payload.password is not None:
        user.hashed_password = get_password_hash(payload.password)

    if user.role == "Operator" and payload.permissions is not None:
        floor_ids = {p.floor_id for p in payload.permissions}
        if floor_ids:
            rows = db.query(Floor.id).filter(Floor.id.in_(floor_ids)).all()
            found_ids = {r[0] for r in rows}
            if found_ids != floor_ids:
                missing = floor_ids - found_ids
                raise ValueError(f"invalid floor_id(s): {sorted(missing)}")

        db.query(UserPermission).filter(UserPermission.user_id == user.id).delete(
            synchronize_session=False
        )
        for p in payload.permissions:
            db.add(
                UserPermission(
                    user_id=user.id,
                    floor_id=p.floor_id,
                    permission_type=p.floor_permission,
                )
            )

    db.commit()
    db.refresh(user)
    return get_active_user_by_id(db, user.id)


def delete_user_by_id(db: Session, user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    # Soft delete: set is_active to False instead of deleting
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user

def get_floors_mapped_to_operator_user(db: Session, operator_user: User):
    rows = (
        db.query(UserPermission.floor_id)
        .filter(UserPermission.user_id == operator_user.id)
        .all()
    )

    return [row[0] for row in rows]

def refine_area_and_floors_of_operator_user(db: Session, area_ids:[], floor_ids:[], operator_user: User):
    
    floor_ids_mapped_to_user = get_floors_mapped_to_operator_user(db, operator_user)

    # when floor_ids is provided, return only those floors that are mapped to the user
    if floor_ids:
        floor_id_subset = []
        for floor_id in floor_ids:
            if floor_id in floor_ids_mapped_to_user:
                floor_id_subset.append(floor_id)
        return [], floor_id_subset

    # when area_ids is provided, return only those floors that are mapped to the user
    if area_ids:
        area_id_subset = []
        for floor_id in floor_ids_mapped_to_user:
            rows = (
                db.query(Area.id)
                .filter(Area.floor_id.in_(floor_ids_mapped_to_user))
                .all()
            )
            area_ids_of_mapped_floors = [row[0] for row in rows]
            for area_id in area_ids_of_mapped_floors:
                if area_id in area_ids:
                    area_id_subset.append(area_id)
        return area_id_subset, []

    # when both area_ids and floor_ids are empty, return all floors mapped to user
    return [], floor_ids_mapped_to_user