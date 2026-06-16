from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.crud import dashboard_layout as layout_crud
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
):
    """Return all dashboard layout rows."""
    rows = layout_crud.list_layouts(db)
    return DashboardLayoutListResponse(
        items=[DashboardLayoutItem.model_validate(row) for row in rows]
    )


@router.post("/layout", response_model=DashboardLayoutItem)
def upsert_dashboard_layout(
    payload: DashboardLayoutUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Upsert one dashboard layout by layout_key (Superadmin only)."""
    row = layout_crud.upsert_layout(
        db,
        payload.layout_key,
        payload.layout_json,
        layout_version=payload.layout_version,
        updated_by=current_user.id,
    )
    return DashboardLayoutItem.model_validate(row)
