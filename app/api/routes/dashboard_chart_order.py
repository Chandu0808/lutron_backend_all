from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.crud.dashboard_chart_order import (
    get_dashboard_chart_order,
    upsert_dashboard_chart_order,
)
from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.user_model import User
from app.schemas.dashboard_chart_order import (
    DashboardChartOrderResponse,
    DashboardChartOrderUpdate,
)

router = APIRouter()


@router.get("/dashboard_chart_order", response_model=DashboardChartOrderResponse)
def read_dashboard_chart_order(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return shared Energy + Space dashboard widget slot order (all authenticated roles)."""
    return get_dashboard_chart_order(db)


@router.post("/dashboard_chart_order", response_model=DashboardChartOrderResponse)
def save_dashboard_chart_order(
    payload: DashboardChartOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist dashboard widget slot order (Superadmin only). Partial updates are merged."""
    require_operator_permission_for_scope(
        required_level=5,
        area_ids=None,
        floor_ids=None,
        enforce_on_empty_scope=False,
        db=db,
        current_user=current_user,
    )

    if (
        payload.energy_slot_order is None
        and payload.space_charts_tab_order is None
        and payload.space_main_tab_order is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of energy_slot_order, space_charts_tab_order, or space_main_tab_order must be provided",
        )

    return upsert_dashboard_chart_order(
        db,
        energy_slot_order=payload.energy_slot_order,
        space_charts_tab_order=payload.space_charts_tab_order,
        space_main_tab_order=payload.space_main_tab_order,
        updated_by=current_user.id,
    )
