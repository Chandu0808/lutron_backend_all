from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.crud.dashboard_chart_order import (
    get_dashboard_chart_order,
    upsert_dashboard_chart_order,
)
from app.crud.variant_runtime import resolve_variant
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
    variant: Optional[str] = Query(default=None),
):
    """Return dashboard widget slot order for the requested/current variant."""
    resolved_variant = resolve_variant(db, variant)
    return get_dashboard_chart_order(db, variant=resolved_variant)


@router.post("/dashboard_chart_order", response_model=DashboardChartOrderResponse)
def save_dashboard_chart_order(
    payload: DashboardChartOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    variant: Optional[str] = Query(default=None),
):
    """Persist dashboard widget slot order for the requested/current variant."""
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
        and payload.energy_slot_span is None
        and payload.space_charts_tab_span is None
        and payload.space_main_tab_span is None
        and payload.advanced_dashboard_order is None
        and payload.customized_dashboard_order is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "At least one layout field must be provided "
                "(order, span, advanced_dashboard_order, or customized_dashboard_order)"
            ),
        )

    resolved_variant = resolve_variant(db, variant)
    return upsert_dashboard_chart_order(
        db,
        variant=resolved_variant,
        energy_slot_order=payload.energy_slot_order,
        space_charts_tab_order=payload.space_charts_tab_order,
        space_main_tab_order=payload.space_main_tab_order,
        energy_slot_span=payload.energy_slot_span,
        space_charts_tab_span=payload.space_charts_tab_span,
        space_main_tab_span=payload.space_main_tab_span,
        advanced_dashboard_order=payload.advanced_dashboard_order,
        customized_dashboard_order=payload.customized_dashboard_order,
        updated_by=current_user.id,
    )
