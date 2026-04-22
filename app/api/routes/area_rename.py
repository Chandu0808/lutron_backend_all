from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user_model import User
from app.schemas.area_rename import AreaRenameRequest
from app.crud.area import update_area_name_on_processor_and_db

router = APIRouter()


@router.post("/rename")
def rename_area(
    request: AreaRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Rename an area on the Lutron processor and mirror the name in the database.
    Admin and Superadmin only.
    """
    if current_user.role not in ["Admin", "Superadmin"]:
        raise HTTPException(
            status_code=403,
            detail="Only Admin or Superadmin can rename areas.",
        )

    return update_area_name_on_processor_and_db(
        db,
        request.area_id,
        request.new_name,
    )
