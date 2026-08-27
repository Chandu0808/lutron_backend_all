"""
Per-processor LEAP subscribe-stream health (area / zone / loadcontroller).

In-memory last-event + subscribed flags; emits throttled metrics via
Instrumentation. Never raises into listener/LC receive loops.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.monitoring import instrumentation

logger = logging.getLogger("lutron_monitoring.stream_health")

STREAM_AREA = "area"
STREAM_ZONE = "zone"
STREAM_LC = "loadcontroller"
KNOWN_STREAMS = (STREAM_AREA, STREAM_ZONE, STREAM_LC)

METRIC_LAST_EVENT = {
    STREAM_AREA: "leap.stream.area.last_event_epoch",
    STREAM_ZONE: "leap.stream.zone.last_event_epoch",
    STREAM_LC: "leap.stream.lc.last_event_epoch",
}
METRIC_SUBSCRIBED = {
    STREAM_AREA: "leap.stream.area.subscribed",
    STREAM_ZONE: "leap.stream.zone.subscribed",
    STREAM_LC: "leap.stream.lc.subscribed",
}

_DEFAULT_EMIT_GAP_SECONDS = 15.0

Key = Tuple[int, str]


@dataclass
class StreamState:
    processor_id: int
    stream: str
    last_event_epoch: float = 0.0
    subscribed: bool = False
    last_emit_monotonic: float = 0.0


_lock = threading.Lock()
_states: Dict[Key, StreamState] = {}


def _reset_for_tests() -> None:
    with _lock:
        _states.clear()


def snapshot() -> Dict[Key, StreamState]:
    with _lock:
        return dict(_states)


def note_stream(
    processor_id: int,
    stream: str,
    *,
    event: bool = False,
    subscribed: Optional[bool] = None,
    emit_gap_seconds: float = _DEFAULT_EMIT_GAP_SECONDS,
) -> None:
    """
    Record stream activity. ``event=True`` updates last-event time.
    ``subscribed`` True/False updates subscribe state (None = unchanged).
    """
    try:
        pid = int(processor_id)
        name = (stream or "").strip().lower()
        if pid <= 0 or name not in KNOWN_STREAMS:
            return
        now_wall = time.time()
        now_mono = time.monotonic()
        force = subscribed is False or subscribed is True
        with _lock:
            key = (pid, name)
            state = _states.get(key)
            if state is None:
                state = StreamState(processor_id=pid, stream=name)
                _states[key] = state
            if event:
                state.last_event_epoch = now_wall
            if subscribed is not None:
                state.subscribed = bool(subscribed)
            due = force or (now_mono - state.last_emit_monotonic) >= emit_gap_seconds
            if not due:
                return
            state.last_emit_monotonic = now_mono
            last_epoch = state.last_event_epoch
            is_sub = state.subscribed
        _emit(pid, name, last_epoch=last_epoch, subscribed=is_sub)
    except Exception as exc:
        logger.warning("[monitoring][stream] note failed: %s", exc)


def note_stream_event(processor_id: int, stream: str) -> None:
    note_stream(processor_id, stream, event=True)


def note_stream_subscribed(processor_id: int, stream: str) -> None:
    note_stream(processor_id, stream, subscribed=True)


def note_stream_down(processor_id: int, stream: str) -> None:
    note_stream(processor_id, stream, subscribed=False)


def _emit(processor_id: int, stream: str, *, last_epoch: float, subscribed: bool) -> None:
    last_key = METRIC_LAST_EVENT.get(stream)
    sub_key = METRIC_SUBSCRIBED.get(stream)
    if last_key:
        instrumentation.metric(
            last_key,
            float(last_epoch or 0.0),
            processor_id=processor_id,
            detail={"stream": stream},
        )
    if sub_key:
        instrumentation.metric(
            sub_key,
            1.0 if subscribed else 0.0,
            processor_id=processor_id,
            detail={"stream": stream},
        )
