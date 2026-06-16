from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud import widget_configuration as widget_crud
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.superadmin import require_superadmin
from app.models.user_model import User
from app.schemas.widget_configuration_api import (
    WidgetConfigurationItem,
    WidgetConfigurationListResponse,
    WidgetConfigurationUpsert,
)

router = APIRouter()


@router.get("/configuration", response_model=WidgetConfigurationListResponse)
def list_widget_configuration(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all widget configuration rows."""
    rows = widget_crud.list_widget_configurations(db)
    return WidgetConfigurationListResponse(
        items=[WidgetConfigurationItem.model_validate(row) for row in rows]
    )


@router.post("/configuration", response_model=WidgetConfigurationItem)
def upsert_widget_configuration(
    payload: WidgetConfigurationUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Create or update widget configuration by widget_key (Superadmin only)."""
    try:
        row = widget_crud.upsert_widget_configuration_by_key(
            db,
            payload.widget_key,
            display_name=payload.display_name,
            dropdown_name=payload.dropdown_name,
            is_visible=payload.is_visible,
            sort_order=payload.sort_order,
            config=payload.config,
            updated_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return WidgetConfigurationItem.model_validate(row)
