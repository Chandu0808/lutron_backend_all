"""UTC bucket helpers for Analytics rollups (Phase 11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

BUCKET_HOUR = "1h"
BUCKET_DAY = "1d"


def ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def floor_hour_utc(ts: datetime) -> datetime:
    t = ensure_utc(ts)
    return t.replace(minute=0, second=0, microsecond=0)


def floor_day_utc(ts: datetime) -> datetime:
    t = ensure_utc(ts)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def iter_hour_buckets(start: datetime, end: datetime) -> List[datetime]:
    """Half-open [start, end) hour buckets. Both bounds floored to hour UTC."""
    cur = floor_hour_utc(start)
    stop = floor_hour_utc(end)
    out: List[datetime] = []
    while cur < stop:
        out.append(cur)
        cur = cur + timedelta(hours=1)
    return out


def iter_day_buckets(start: datetime, end: datetime) -> List[datetime]:
    cur = floor_day_utc(start)
    stop = floor_day_utc(end)
    out: List[datetime] = []
    while cur < stop:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def bucket_end(bucket_start: datetime, bucket_size: str) -> datetime:
    start = ensure_utc(bucket_start)
    if bucket_size == BUCKET_DAY:
        return start + timedelta(days=1)
    return start + timedelta(hours=1)


def complete_hour_range(
    now: Optional[datetime] = None,
    *,
    lookback_hours: int = 48,
) -> tuple[datetime, datetime]:
    """
    Inclusive start / exclusive end for completed hours only
    (excludes the in-progress hour).
    """
    end = floor_hour_utc(now or datetime.now(timezone.utc))
    start = end - timedelta(hours=max(1, lookback_hours))
    return start, end


def complete_day_range(
    now: Optional[datetime] = None,
    *,
    lookback_days: int = 14,
) -> tuple[datetime, datetime]:
    end = floor_day_utc(now or datetime.now(timezone.utc))
    start = end - timedelta(days=max(1, lookback_days))
    return start, end


def buckets_for_size(
    bucket_size: str,
    start: datetime,
    end: datetime,
) -> Iterable[datetime]:
    if bucket_size == BUCKET_DAY:
        return iter_day_buckets(start, end)
    return iter_hour_buckets(start, end)
