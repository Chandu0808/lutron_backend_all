from typing import Dict

from sqlalchemy.orm import Session

from app.crud.widget_title_adapter import get_display_name_for_widget, get_widget_titles_map
from app.crud.widget_title_defaults import TITLE_DEFAULTS

# Backward-compatible export for modules that import DEFAULT_WIDGET_TITLES.
DEFAULT_WIDGET_TITLES: Dict[str, str] = TITLE_DEFAULTS.copy()


def append_widget_title(db: Session, widget_key: str, data):
    """
    Append widget_title to any API response.
    Reads widget_configuration first, then legacy widget_titles, then defaults.
    """
    widget_title = get_display_name_for_widget(db, widget_key)
    if not widget_title:
        widget_title = TITLE_DEFAULTS.get(widget_key, "")

    if isinstance(data, dict):
        return {**data, "widget_title": widget_title}
    return {"data": data, "widget_title": widget_title}


def get_widget_titles_map_legacy(db: Session) -> Dict[str, str]:
    """Deprecated alias — use app.crud.widget_title_adapter.get_widget_titles_map."""
    return get_widget_titles_map(db)
