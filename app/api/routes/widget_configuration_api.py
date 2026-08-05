from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.crud import variant_widget_configuration as widget_crud
from app.crud.variant_runtime import resolve_variant
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
    variant: Optional[str] = Query(default=None),
):
    """Return widget configuration rows for the requested/current variant."""
    resolved_variant = resolve_variant(db, variant)
    rows = widget_crud.list_variant_widget_configurations(db, resolved_variant)
    return WidgetConfigurationListResponse(
        items=[WidgetConfigurationItem.model_validate(row) for row in rows]
    )


@router.post("/configuration", response_model=WidgetConfigurationItem)
def upsert_widget_configuration(
    payload: WidgetConfigurationUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    variant: Optional[str] = Query(default=None),
):
    """Create or update widget configuration by widget_key for a variant."""
    try:
        resolved_variant = resolve_variant(db, variant)
        row = widget_crud.upsert_variant_widget_configuration_by_key(
            db,
            resolved_variant,
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
