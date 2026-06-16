# app/api/routes/widget_title.py
from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.schemas.widget_title import RenameWidgetRequest, RenameWidgetResponse, WidgetTitlesResponse
from app.crud.widget_title_adapter import (
    build_widget_titles_response_items,
    rename_widget_via_configuration,
)

router = APIRouter()


@router.post("/rename_widget", response_model=RenameWidgetResponse)
def rename_widget(
    payload: RenameWidgetRequest = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = rename_widget_via_configuration(
        db=db,
        widget_key=payload.widget_key,
        display_name=payload.new_name,
        updated_by=user.id if user else None,
    )
    return {
        "status": "success",
        "widget_key": row.widget_key,
        "display_name": row.display_name,
    }


@router.get("/widget_titles", response_model=WidgetTitlesResponse)
def get_widget_titles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read-only: titles from widget_configuration with legacy fallback."""
    titles_array = build_widget_titles_response_items(db)
    return {"status": "success", "titles": titles_array}
