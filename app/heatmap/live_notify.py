"""
Publish heatmap cache-change events via Postgres NOTIFY.

Called from listener.py after current_area_status / current_zone_status commits.
FastAPI LISTEN task forwards payloads to subscribed WebSocket clients.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text

from app.database.session import engine
from app.heatmap.live_constants import HEATMAP_LIVE_NOTIFY_CHANNEL

logger = logging.getLogger(__name__)


def publish_heatmap_live_event(
    *,
    floor_id: int,
    area_id: Optional[int] = None,
) -> None:
    """Best-effort NOTIFY; never raises to caller (listener must not fail on publish)."""
    if floor_id is None:
        return
    try:
        payload = json.dumps(
            {
                "floor_id": int(floor_id),
                "area_id": int(area_id) if area_id is not None else None,
            }
        )
        with engine.connect() as conn:
            conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": HEATMAP_LIVE_NOTIFY_CHANNEL, "payload": payload},
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[HeatmapLive] NOTIFY failed floor=%s area=%s: %s", floor_id, area_id, exc)


def resolve_floor_id_for_area(db, area_id: Optional[int]) -> Optional[int]:
    if area_id is None:
        return None
    try:
        from app.models.area import Area

        area = db.query(Area).filter(Area.id == area_id).first()
        return area.floor_id if area else None
    except Exception:
        return None
