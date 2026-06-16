# app/crud/widget_title.py
from sqlalchemy.orm import Session
from typing import Optional, Dict
from app.models.widget_title import WidgetTitle

def upsert_widget_title(
    db: Session,
    widget_key: str,
    display_name: Optional[str],
    updated_by: Optional[int],
    *,
    dropdown_name: Optional[str] = None,
    only_if_absent: bool = False,
) -> WidgetTitle:
    """
    If only_if_absent=True:
      - display_name is only set if currently NULL/empty
      - dropdown_name is only set if currently NULL/empty
    If display_name=None or dropdown_name=None, those fields are left unchanged.
    """
    row = db.query(WidgetTitle).filter(WidgetTitle.widget_key == widget_key).one_or_none()
    if row is None:
        row = WidgetTitle(
            widget_key=widget_key,
            display_name=display_name or widget_key,   # initial fallback
            dropdown_name=dropdown_name,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        if display_name is not None:
            if not only_if_absent or not (row.display_name and row.display_name.strip()):
                row.display_name = display_name
        if dropdown_name is not None:
            if not only_if_absent or not (row.dropdown_name and row.dropdown_name.strip()):
                row.dropdown_name = dropdown_name
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row

def get_all_widget_titles(db: Session) -> Dict[str, Dict[str, str]]:
    rows = db.query(WidgetTitle).all()
    return {
        r.widget_key: {
            "display_name": r.display_name,
            "dropdown_name": r.dropdown_name or ""
        }
        for r in rows
    }

def sync_widget_defaults(
    db: Session,
    title_defaults: Dict[str, str],
    dropdown_defaults: Dict[str, str],
) -> Dict[str, Dict[str, str]]:
    """
    Ensure each known widget exists and has a dropdown_name/title if missing.
    Will NOT overwrite existing names.
    """
    keys = set(title_defaults.keys()) | set(dropdown_defaults.keys())
    for k in keys:
        # create row if missing (with dropdown_name if provided)
        upsert_widget_title(
            db=db,
            widget_key=k,
            display_name=title_defaults.get(k),
            updated_by=None,
            dropdown_name=dropdown_defaults.get(k),
            only_if_absent=True,  # <- critical: do not overwrite
        )
    return get_all_widget_titles(db)

def get_title_of_widget(db: Session, widget_key: str):
    from app.crud.widget_title_adapter import get_display_name_for_widget

    return get_display_name_for_widget(db, widget_key)
