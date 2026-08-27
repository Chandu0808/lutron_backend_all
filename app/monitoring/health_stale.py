"""Heartbeat stale detection for Monitoring health integrity (P0)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

# Matches seeded alert rule ``component_heartbeat_stale`` default (seeds.py).
DEFAULT_STALE_SECONDS = 90.0


def heartbeat_stale_seconds() -> float:
    raw = (os.getenv("MONITORING_HEARTBEAT_STALE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_STALE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_STALE_SECONDS
    if value <= 0:
        return DEFAULT_STALE_SECONDS
    return value


def _as_aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def is_heartbeat_stale(
    last_heartbeat_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    stale_seconds: Optional[float] = None,
) -> bool:
    last = _as_aware(last_heartbeat_at)
    if last is None:
        return True
    current = _as_aware(now) or datetime.now(timezone.utc)
    threshold = stale_seconds if stale_seconds is not None else heartbeat_stale_seconds()
    return (current - last).total_seconds() >= threshold


def effective_health_status(
    status: Optional[str],
    last_heartbeat_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    stale_seconds: Optional[float] = None,
) -> str:
    """
    If stored status claims liveness but heartbeat is stale, report ``down``.
    """
    raw = (status or "unknown").strip().lower() or "unknown"
    if raw in ("up", "degraded", "starting") and is_heartbeat_stale(
        last_heartbeat_at, now=now, stale_seconds=stale_seconds
    ):
        return "down"
    return raw
