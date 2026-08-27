"""
In-memory WebSocket fan-out for heatmap live updates.

A background Postgres LISTEN thread receives NOTIFY from listener.py and
schedules async broadcasts to subscribed browser tabs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import select
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from fastapi import WebSocket

from app.database.session import SessionLocal
from app.heatmap.live_constants import HEATMAP_LIVE_NOTIFY_CHANNEL
from app.heatmap.live_payload import (
    build_area_status_message,
    build_floor_light_message,
    build_floor_light_message_for_area,
    build_floor_occupancy_message,
    build_floor_occupancy_message_for_area,
)

logger = logging.getLogger(__name__)


@dataclass
class HeatmapLiveSubscription:
    floor_id: Optional[int] = None
    area_id: Optional[int] = None
    display_mode: str = "Light"


def _norm_display_mode(mode: Optional[str]) -> str:
    return (mode or "Light").strip()


def _should_skip_floor_snapshot_on_subscribe(
    previous: Optional[HeatmapLiveSubscription],
    current: HeatmapLiveSubscription,
) -> bool:
    """
    Area selection changes should refresh area_status only.
    Re-sending full-floor light cache (live=False) recolors every polygon.
    """
    if previous is None or previous.floor_id is None or current.floor_id is None:
        return False
    if int(previous.floor_id) != int(current.floor_id):
        return False
    if _norm_display_mode(previous.display_mode) != _norm_display_mode(current.display_mode):
        return False
    # Same floor + mode; only the open sidebar area changed (including first area pick).
    prev_area = previous.area_id
    cur_area = current.area_id
    if prev_area is None and cur_area is None:
        return False
    return prev_area != cur_area


class HeatmapLiveHub:
    def __init__(self) -> None:
        self._clients: Dict[WebSocket, HeatmapLiveSubscription] = {}
        self._prev_snapshot_sub: Dict[WebSocket, HeatmapLiveSubscription] = {}
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listen_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    async def register(self, websocket: WebSocket, subscription: HeatmapLiveSubscription) -> None:
        async with self._lock:
            self._clients[websocket] = subscription

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.pop(websocket, None)
            self._prev_snapshot_sub.pop(websocket, None)

    async def update_subscription(
        self, websocket: WebSocket, subscription: HeatmapLiveSubscription
    ) -> None:
        async with self._lock:
            if websocket in self._clients:
                self._prev_snapshot_sub[websocket] = self._clients[websocket]
                self._clients[websocket] = subscription
            else:
                self._clients[websocket] = subscription

    async def send_snapshot(
        self, websocket: WebSocket, subscription: HeatmapLiveSubscription
    ) -> None:
        """Push current DB-cache status once on subscribe (not a poll loop)."""
        if subscription.floor_id is None:
            return

        async with self._lock:
            previous = self._prev_snapshot_sub.pop(websocket, None)
        skip_floor = _should_skip_floor_snapshot_on_subscribe(previous, subscription)

        db = SessionLocal()
        try:
            mode = _norm_display_mode(subscription.display_mode)
            floor_msg = None
            if not skip_floor:
                if mode == "Occupancy":
                    floor_msg = build_floor_occupancy_message(db, subscription.floor_id)
                else:
                    floor_msg = build_floor_light_message(db, subscription.floor_id)
            area_msg = (
                build_area_status_message(db, subscription.area_id)
                if subscription.area_id is not None
                else None
            )
        finally:
            db.close()

        if floor_msg:
            await self.send_json(websocket, floor_msg)
        if area_msg:
            await self.send_json(websocket, area_msg)

    async def _snapshot_clients(self) -> list[tuple[WebSocket, HeatmapLiveSubscription]]:
        async with self._lock:
            return list(self._clients.items())

    async def send_json(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            await self.unregister(websocket)

    async def broadcast_notify(self, floor_id: int, area_id: Optional[int]) -> None:
        clients = await self._snapshot_clients()
        if not clients:
            return

        db = SessionLocal()
        try:
            if area_id is not None:
                # Area-specific listener notifications should update only that
                # polygon/sidebar. Broadcasting full-floor live=False cache can
                # recolor unrelated rooms with stale data.
                floor_light_msg = build_floor_light_message_for_area(db, area_id)
                floor_occ_msg = build_floor_occupancy_message_for_area(db, area_id)
                area_msg = build_area_status_message(db, area_id)
            else:
                floor_light_msg = build_floor_light_message(db, floor_id)
                floor_occ_msg = build_floor_occupancy_message(db, floor_id)
                area_msg = None
        finally:
            db.close()

        for websocket, sub in clients:
            if sub.floor_id is None or int(sub.floor_id) != int(floor_id):
                continue

            mode = (sub.display_mode or "Light").strip()
            if mode == "Light" and floor_light_msg:
                await self.send_json(websocket, floor_light_msg)
            elif mode == "Occupancy" and floor_occ_msg:
                await self.send_json(websocket, floor_occ_msg)

            if (
                area_msg
                and sub.area_id is not None
                and area_id is not None
                and int(sub.area_id) == int(area_id)
            ):
                await self.send_json(websocket, area_msg)

    def _schedule_notify(self, floor_id: int, area_id: Optional[int]) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.broadcast_notify(floor_id, area_id),
            self._loop,
        )

    def _listen_worker(self) -> None:
        database_url = os.getenv("DATABASE_HOST_URL")
        if not database_url:
            logger.error("[HeatmapLive] DATABASE_HOST_URL missing; LISTEN thread exiting")
            return

        conn = None
        while not self._stop_event.is_set():
            try:
                if conn is None:
                    conn = psycopg2.connect(database_url)
                    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                    cur = conn.cursor()
                    cur.execute(f"LISTEN {HEATMAP_LIVE_NOTIFY_CHANNEL}")
                    cur.close()
                    logger.info("[HeatmapLive] Postgres LISTEN on %s", HEATMAP_LIVE_NOTIFY_CHANNEL)

                if select.select([conn], [], [], 0.25) == ([], [], []):
                    continue

                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    try:
                        data = json.loads(notify.payload)
                        floor_id = data.get("floor_id")
                        area_id = data.get("area_id")
                        if floor_id is not None:
                            self._schedule_notify(int(floor_id), area_id)
                    except Exception as exc:
                        logger.warning("[HeatmapLive] Bad NOTIFY payload: %s", exc)
            except Exception as exc:
                logger.warning("[HeatmapLive] LISTEN error: %s", exc)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                self._stop_event.wait(1.0)

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._listen_thread and self._listen_thread.is_alive():
            return
        self._loop = loop
        self._stop_event.clear()
        self._listen_thread = threading.Thread(
            target=self._listen_worker,
            name="heatmap-live-listen",
            daemon=True,
        )
        self._listen_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._listen_thread:
            self._listen_thread.join(timeout=2.0)
            self._listen_thread = None


heatmap_live_hub = HeatmapLiveHub()
