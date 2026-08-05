from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.crud import variant_dashboard_layout as layout_crud
from app.crud.variant_runtime import resolve_variant
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.superadmin import require_superadmin
from app.models.user_model import User
from app.schemas.dashboard_layout_api import (
    DashboardLayoutItem,
    DashboardLayoutListResponse,
    DashboardLayoutUpsert,
)

router = APIRouter()


@router.get("/layout", response_model=DashboardLayoutListResponse)
def list_dashboard_layouts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    variant: Optional[str] = Query(default=None),
):
    """Return all dashboard layout rows for the requested/current variant."""
    resolved_variant = resolve_variant(db, variant)
    rows = layout_crud.list_variant_layouts(db, resolved_variant)
    return DashboardLayoutListResponse(
        items=[DashboardLayoutItem.model_validate(row) for row in rows]
    )


@router.post("/layout", response_model=DashboardLayoutItem)
def upsert_dashboard_layout(
    payload: DashboardLayoutUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
    variant: Optional[str] = Query(default=None),
):
    """Upsert one dashboard layout by layout_key for a variant."""
    resolved_variant = resolve_variant(db, variant)
    row = layout_crud.upsert_variant_layout(
        db,
        resolved_variant,
        payload.layout_key,
        payload.layout_json,
        layout_version=payload.layout_version,
        updated_by=current_user.id,
    )
    return DashboardLayoutItem.model_validate(row)
