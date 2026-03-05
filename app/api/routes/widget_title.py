# app/api/routes/widget_title.py
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Dict

from app.database.session import get_db
from app.models.user_model import User
from app.dependencies.auth import get_current_user
from app.schemas.widget_title import RenameWidgetRequest, RenameWidgetResponse, WidgetTitlesResponse
from app.crud.widget_title import upsert_widget_title, get_all_widget_titles, sync_widget_defaults

router = APIRouter()

@router.post("/rename_widget", response_model=RenameWidgetResponse)
def rename_widget(
    payload: RenameWidgetRequest = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # Optional role check
    # if user.role not in ("admin", "superadmin"):
    #     raise HTTPException(status_code=403, detail="Not allowed to rename widgets")

    row = upsert_widget_title(
        db=db,
        widget_key=payload.widget_key,
        display_name=payload.new_name,
        updated_by=user.id if user else None
    )
    return {
        "status": "success",
        "widget_key": row.widget_key,
        "display_name": row.display_name
    }

@router.get("/widget_titles", response_model=WidgetTitlesResponse)
def get_widget_titles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    # Default display titles
    title_defaults: Dict[str, str] = {
        "savings_by_strategy": "Savings by Strategy",
        "consumption_by_area_groups": "Consumption By Area Groups",
        "light_power_density": "Light Power Density",
        "consumption": "Consumption",
        "savings": "Savings",
        "peak_and_minimum_consumption": "Peak And Minimum Consumption",
        "utilization": "Utilization",
        "instant_occupancy_count": "Occupancy",
        "utilization_by_area_group": "Utilization By Area Group",
        "utilization_by_area": "Utilization By Area",
        "peak_and_minimum_utilization": "Peak And Minimum Utilization"
    }

    # Hardcoded dropdown labels per widget (persisted to DB)
    dropdown_defaults: Dict[str, str] = {
        "savings_by_strategy": "savings by strategy",
        "consumption_by_area_groups": "consumption by area groups",
        "light_power_density": "light power density",
        "consumption": "Consumption",
        "savings": "Savings",
        "peak_and_minimum_consumption": "Peak/Min Consumption",
        "utilization": "Utilization",
        "instant_occupancy_count": "Occupancy",
        "utilization_by_area_group": "utilization by area group",
        "utilization_by_area": "utilization by area",
        "peak_and_minimum_utilization": "Peak/Min Utilization",
    }

    # Ensure DB has rows + dropdowns for all known widgets (idempotent)
    merged = sync_widget_defaults(db, title_defaults, dropdown_defaults)

    # Build response array in stable order of defaults
    titles_array = []
    for k in title_defaults.keys():
        item = merged.get(k, {"display_name": title_defaults[k], "dropdown_name": dropdown_defaults.get(k, "")})
        titles_array.append({
            "key": k,
            "title": item["display_name"] or title_defaults[k],
            "dropdown_name": item["dropdown_name"] or dropdown_defaults.get(k, "")
        })

    return {"status": "success", "titles": titles_array}
