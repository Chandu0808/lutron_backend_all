from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.widget_configuration import WidgetConfiguration

DEFAULT_WIDGET_CONFIG: Dict[str, Any] = {"version": 1}


def create_widget_configuration(
    db: Session,
    *,
    widget_key: str,
    display_name: str,
    dropdown_name: Optional[str] = None,
    is_visible: bool = True,
    sort_order: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> WidgetConfiguration:
    row = WidgetConfiguration(
        widget_key=widget_key,
        display_name=display_name,
        dropdown_name=dropdown_name,
        is_visible=is_visible,
        sort_order=sort_order,
        config=config if config is not None else deepcopy(DEFAULT_WIDGET_CONFIG),
        updated_by=updated_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_widget_configuration(db: Session, row_id: int) -> Optional[WidgetConfiguration]:
    return db.query(WidgetConfiguration).filter(WidgetConfiguration.id == row_id).first()


def get_widget_configuration_by_key(
    db: Session, widget_key: str
) -> Optional[WidgetConfiguration]:
    return (
        db.query(WidgetConfiguration)
        .filter(WidgetConfiguration.widget_key == widget_key)
        .first()
    )


def list_widget_configurations(db: Session) -> List[WidgetConfiguration]:
    return (
        db.query(WidgetConfiguration)
        .order_by(WidgetConfiguration.sort_order, WidgetConfiguration.widget_key)
        .all()
    )


def update_widget_configuration(
    db: Session,
    row_id: int,
    *,
    display_name: Optional[str] = None,
    dropdown_name: Optional[str] = None,
    is_visible: Optional[bool] = None,
    sort_order: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> Optional[WidgetConfiguration]:
    row = get_widget_configuration(db, row_id)
    if row is None:
        return None
    if display_name is not None:
        row.display_name = display_name
    if dropdown_name is not None:
        row.dropdown_name = dropdown_name
    if is_visible is not None:
        row.is_visible = is_visible
    if sort_order is not None:
        row.sort_order = sort_order
    if config is not None:
        row.config = config
    if updated_by is not None:
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row


def delete_widget_configuration(db: Session, row_id: int) -> bool:
    row = get_widget_configuration(db, row_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def upsert_widget_configuration_by_key(
    db: Session,
    widget_key: str,
    *,
    display_name: Optional[str] = None,
    dropdown_name: Optional[str] = None,
    is_visible: Optional[bool] = None,
    sort_order: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    updated_by: Optional[int] = None,
) -> WidgetConfiguration:
    row = get_widget_configuration_by_key(db, widget_key)
    if row is None:
        if display_name is None:
            raise ValueError("display_name is required when creating a widget configuration")
        return create_widget_configuration(
            db,
            widget_key=widget_key,
            display_name=display_name,
            dropdown_name=dropdown_name,
            is_visible=True if is_visible is None else is_visible,
            sort_order=sort_order,
            config=config,
            updated_by=updated_by,
        )

    if display_name is not None:
        row.display_name = display_name
    if dropdown_name is not None:
        row.dropdown_name = dropdown_name
    if is_visible is not None:
        row.is_visible = is_visible
    if sort_order is not None:
        row.sort_order = sort_order
    if config is not None:
        row.config = config
    if updated_by is not None:
        row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return row
