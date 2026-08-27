"""
Authenticated WebSocket for heatmap live cache push (replaces frontend polling).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.session import SessionLocal
from app.heatmap.live_hub import HeatmapLiveHub, HeatmapLiveSubscription, heatmap_live_hub
from app.models.user_model import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _authenticate_ws_token(token: str, db: Session) -> Optional[User]:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        email = payload.get("sub")
        if not email:
            return None
        return (
            db.query(User)
            .filter(User.email == email, User.is_active == True)
            .first()
        )
    except Exception:
        return None


def _parse_subscription(message: dict) -> Optional[HeatmapLiveSubscription]:
    action = message.get("action")
    if action != "subscribe":
        return None
    floor_id = message.get("floor_id")
    area_id = message.get("area_id")
    display_mode = message.get("display_mode") or "Light"
    try:
        floor_val = int(floor_id) if floor_id is not None else None
    except (TypeError, ValueError):
        floor_val = None
    try:
        area_val = int(area_id) if area_id is not None else None
    except (TypeError, ValueError):
        area_val = None
    return HeatmapLiveSubscription(
        floor_id=floor_val,
        area_id=area_val,
        display_mode=str(display_mode),
    )


@router.websocket("/ws/heatmap/live")
async def heatmap_live_websocket(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    # Accept first so browsers get a completed handshake. Closing before
    # accept() produces a generic "WebSocket connection failed" in Chrome.
    await websocket.accept()

    db = SessionLocal()
    try:
        user = _authenticate_ws_token(token, db)
    finally:
        db.close()

    if not user:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    hub: HeatmapLiveHub = heatmap_live_hub
    await hub.register(websocket, HeatmapLiveSubscription())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            subscription = _parse_subscription(message)
            if subscription:
                await hub.update_subscription(websocket, subscription)
                await hub.send_json(websocket, {"type": "subscribed", "ok": True})
                await hub.send_snapshot(websocket, subscription)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[HeatmapLive] WebSocket closed: %s", exc)
    finally:
        await hub.unregister(websocket)
