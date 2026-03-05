from typing import Dict
from sqlalchemy.orm import Session
from app.models.widget_title import WidgetTitle

DEFAULT_WIDGET_TITLES: Dict[str, str] = {
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
    "peak_and_minimum_utilization": "Peak And Minimum Utilization",
}

def get_widget_titles_map(db: Session) -> Dict[str, str]:
    """
    Merge DB overrides with defaults.
    """
    titles = DEFAULT_WIDGET_TITLES.copy()
    for row in db.query(WidgetTitle).all():
        if row.widget_key in titles and row.display_name:
            titles[row.widget_key] = row.display_name
    return titles

def append_widget_title(db: Session, widget_key: str, data):
    """
    Append widget_title to any API response.
    """
    titles = get_widget_titles_map(db)
    widget_title = titles.get(widget_key, DEFAULT_WIDGET_TITLES.get(widget_key, ""))
    
    if isinstance(data, dict):
        return {**data, "widget_title": widget_title}
    else:
        return {"data": data, "widget_title": widget_title}
