"""Floor list ordering: auto (natural name) vs manual (sort_order)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import natsort
from sqlalchemy.orm import Session

from app.crud.installation_settings import (
    SETTING_KEY_FLOOR_MANUAL_SORT_ENABLED,
    get_setting,
    upsert_setting,
)
from app.models.floor import Floor


def get_manual_sort_enabled(db: Session) -> bool:
    row = get_setting(db, SETTING_KEY_FLOOR_MANUAL_SORT_ENABLED)
    if row is None:
        return False
    return bool(row.setting_value)


def set_manual_sort_enabled(
    db: Session,
    enabled: bool,
    updated_by: Optional[int] = None,
) -> bool:
    upsert_setting(
        db,
        SETTING_KEY_FLOOR_MANUAL_SORT_ENABLED,
        enabled,
        updated_by=updated_by,
    )
    if enabled:
        backfill_sort_order_from_auto(db)
    return enabled


def backfill_sort_order_from_auto(db: Session) -> None:
    """Assign sort_order from current natural name order when any floor lacks it."""
    floors = db.query(Floor).all()
    if not floors:
        return

    needs_backfill = any(f.sort_order is None for f in floors)
    if not needs_backfill:
        return

    ordered = natsort.natsorted(floors, key=lambda f: f.name or "")
    for index, floor in enumerate(ordered):
        floor.sort_order = index
    db.commit()


def _next_sort_order(db: Session) -> int:
    rows = db.query(Floor.sort_order).filter(Floor.sort_order.isnot(None)).all()
    if not rows:
        return 0
    return max(r[0] for r in rows) + 1


def assign_sort_order_on_create(db: Session, floor: Floor) -> None:
    if not get_manual_sort_enabled(db):
        floor.sort_order = None
        return
    floor.sort_order = _next_sort_order(db)


def sort_floor_dicts(
    items: List[Dict[str, Any]],
    manual_sort_enabled: bool,
) -> List[Dict[str, Any]]:
    if manual_sort_enabled:
        return sorted(
            items,
            key=lambda x: (
                x.get("sort_order") is None,
                x.get("sort_order") if x.get("sort_order") is not None else 0,
                x.get("floor_name") or "",
            ),
        )
    return natsort.natsorted(items, key=lambda x: x.get("floor_name") or "")


def reorder_floors(db: Session, floor_ids: List[int]) -> None:
    if not get_manual_sort_enabled(db):
        raise ValueError("Manual floor sorting is disabled")

    existing_ids = {f.id for f in db.query(Floor.id).all()}
    if set(floor_ids) != existing_ids:
        missing = existing_ids - set(floor_ids)
        extra = set(floor_ids) - existing_ids
        if missing or extra:
            raise ValueError("floor_ids must include every floor exactly once")

    id_to_floor = {f.id: f for f in db.query(Floor).filter(Floor.id.in_(floor_ids)).all()}
    for index, floor_id in enumerate(floor_ids):
        floor = id_to_floor.get(floor_id)
        if floor is None:
            raise ValueError(f"Floor {floor_id} not found")
        floor.sort_order = index
    db.commit()
