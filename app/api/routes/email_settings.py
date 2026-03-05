from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.schemas.email_settings import EmailServerSettingsCreate, EmailServerSettingsInDB, SendEmailRequest, EmailServerSettingsPublic
from app.crud import email_settings as email_crud
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope


router = APIRouter()


@router.post("/create", response_model=EmailServerSettingsInDB)
def create_email_server_settings(
    settings: EmailServerSettingsCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- Admin & Superadmin Permission Check ----------
    require_operator_permission_for_scope(
        required_level=4,   # Admin + Superadmin only
        db=db,
        current_user=user
    )

    return email_crud.create_email_settings(db, settings)


@router.get("/list", response_model=list[EmailServerSettingsPublic])
def list_email_server_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- Admin & Superadmin Permission Check ----------
    require_operator_permission_for_scope(
        required_level=4,   # Admin + Superadmin only
        db=db,
        current_user=user
    )

    return email_crud.get_all_email_settings(db)


@router.post("/send-test-email")
def send_email_api(
    payload: SendEmailRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # ---------- Admin & Superadmin Permission Check ----------
    require_operator_permission_for_scope(
        required_level=4,   # Admin + Superadmin only
        db=db,
        current_user=user
    )

    success = email_crud.send_email(
        db=db,
        to_email=payload.to_email,
        subject=payload.subject,
        body="Hello there!\n\nThis is a test email to check SMTP configurations. Please ignore..\n\nThank you",
        is_html=False
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send email.")

    return {"status": "success", "message": "Email sent successfully."}