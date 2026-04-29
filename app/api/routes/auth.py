from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.schemas.user import LoginRequest, ChangePasswordRequest
from app.database.session import get_db
from app.core.security import create_access_token, decode_access_token, verify_password, get_password_hash
from app.crud.user import authenticate_user, get_user_by_email
from fastapi.responses import JSONResponse
from app.utils.activity_logger import log_activity
from app.dependencies.auth import get_current_user
from app.utils.activity_report_logger import activity_report_log
from app.models.user_model import User 

router = APIRouter()

@router.post("/login")
def login(login_request: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate a user and return an access token.
    ``username`` must match the user's stored ``name`` (unique among active users).
    JWT ``sub`` remains the user's email for existing token resolution.
    Also log login activity.
    """
    user = authenticate_user(db, login_request.username, login_request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": user.email})

    # ---------- Activity Logs ----------
    try:
        # Internal log
        # log_activity(
        #     db=db,
        #     user_id=user.id,
        #     area_id=None,
        #     activity_type="GUI Triggered",
        #     activity_description=f"User logged in."
        # )

        # Activity Report log
        activity_report_log(
            db=db,
            user_id=user.id,
            area_id=None,
            activity_type="User",
            activity_description=f"User logged in."
        )

    except Exception as log_error:
        print(f"[LOG ERROR] Failed to log user login activity: {log_error}")

    return {
        "access_token": token,
        "token_type": "bearer",
        "name": user.name,
        "role": user.role,
        "change_password": user.change_password
    }


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """
    Handle user logout by decoding the JWT token from headers
    and logging the logout activity.
    """
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = auth_header.split(" ")[1]

    try:
        # Decode token to get user email
        payload = decode_access_token(token)
        user_email = payload.get("sub")
        if not user_email:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Fetch user from DB
        user = get_user_by_email(db, user_email)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # ---------- Activity Logs ----------
        try:
            # log_activity(
            #     db=db,
            #     user_id=user.id,
            #     area_id=None,
            #     activity_type="GUI Triggered",
            #     activity_description=f"User logged out."
            # )

            activity_report_log(
                db=db,
                user_id=user.id,
                area_id=None,
                activity_type="User",
                activity_description=f"User logged out."
            )

        except Exception as log_error:
            print(f"[LOG ERROR] Failed to log user logout activity: {log_error}")

    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return JSONResponse(content={"message": "Logged out successfully"}, status_code=200)

@router.get("/me")
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Get the user from the database with permissions (using the same function as other endpoints)
    user = get_user_by_email(db, current_user.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Build response with user details and floor permissions
    response_data = {
        "name": user.name, 
        "email": user.email,
        "role": user.role
    }
    
    # Add floor permissions for Operators
    if user.role == "Operator" and user.user_permissions:
        response_data["floors"] = [
            {
                "floor_id": up.floor_id,
                "floor_name": up.floor.name if up.floor else None,
                "floor_permission": up.permission_type
            }
            for up in user.user_permissions
        ]
    
    response_data["change_password"] = user.change_password
    
    return response_data

@router.post("/change_password")
def change_password(
    password_request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Allow users to change their own password.
    Required for first-time login when change_password is True.
    """
    # Verify current password
    if not verify_password(password_request.current_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    
    # Update password and clear the change_password flag
    current_user.hashed_password = get_password_hash(password_request.new_password)
    current_user.change_password = False
    db.commit()
    db.refresh(current_user)
    
    # ---------- Activity Logs ----------
    try:
        activity_report_log(
            db=db,
            user_id=current_user.id,
            area_id=None,
            activity_type="User",
            activity_description=f"User changed password."
        )
    except Exception as log_error:
        print(f"[LOG ERROR] Failed to log password change activity: {log_error}")
    
    return {
        "status": "success",
        "message": "Password changed successfully"
    }