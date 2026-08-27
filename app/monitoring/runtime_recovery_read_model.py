"""
Phase 4 — read-only Runtime Supervisor recovery enrichment for Issues.

Correlates mon_event runtime.* rows to product Issues by supervised child
identity + time window (+ generation / PID when present). Does not open or
resolve alerts. Does not invent recovery when evidence is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from app.monitoring.storage.types import EventRow

# Exact Runtime Supervisor child names registered in app.main (component_code ≡ child_name).
SUPERVISED_RUNTIME_CHILDREN = frozenset(
    {
        "listener",
        "energy_logger",
        "loadcontroller_listener",
    }
)

RUNTIME_PREFIX = "runtime."

# Evidence that a restart was attempted (product recovery.attempted = true).
ATTEMPT_EVIDENCE_TYPES = frozenset(
    {
        "RestartRequested",
        "RestartStarted",
        "RestartSucceeded",
        "RestartFailed",
        "Abandoned",
    }
)

# Persisted event types fetched in the batched query.
RECOVERY_EVENT_TYPES = tuple(
    f"{RUNTIME_PREFIX}{name}"
    for name in (
        "RestartRequested",
        "RestartScheduled",
        "RestartStarted",
        "RestartSucceeded",
        "RestartFailed",
        "Abandoned",
    )
)

RESULT_SUCCESS = "SUCCESS"
RESULT_FAILED = "FAILED"
RESULT_ABANDONED = "ABANDONED"

# Small explicit boundary around issue lifecycle for event ordering skew.
LIFECYCLE_BOUNDARY = timedelta(seconds=60)

# Hard cap for one issues-page enrichment query (event_type + time + children).
MAX_EVENTS_PER_QUERY = 2000

_EMPTY_RECOVERY: Dict[str, Any] = {
    "attempted": None,
    "result": None,
    "duration_ms": None,
}


def empty_recovery() -> Dict[str, Any]:
    return dict(_EMPTY_RECOVERY)


def is_runtime_correlatable(component_code: Optional[str]) -> bool:
    if not component_code:
        return False
    return component_code.strip() in SUPERVISED_RUNTIME_CHILDREN


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _short_type(event_type: Optional[str]) -> str:
    if not event_type:
        return ""
    if event_type.startswith(RUNTIME_PREFIX):
        return event_type[len(RUNTIME_PREFIX) :]
    return event_type


def _payload(row: EventRow) -> Dict[str, Any]:
    return row.payload_json if isinstance(row.payload_json, dict) else {}


def _child_name(row: EventRow) -> Optional[str]:
    raw = _payload(row).get("child_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _Attempt:
    """One restart attempt chain for a supervised child."""

    child_name: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    generation_before: Optional[int] = None
    generation: Optional[int] = None
    pid: Optional[int] = None
    result: Optional[str] = None
    evidence_times: List[datetime] = field(default_factory=list)
    has_attempt_evidence: bool = False

    def note(self, at: datetime) -> None:
        self.evidence_times.append(at)

    def overlaps(self, window_start: datetime, window_end: datetime) -> bool:
        times = list(self.evidence_times)
        if self.started_at is not None:
            times.append(self.started_at)
        if self.ended_at is not None:
            times.append(self.ended_at)
        if not times:
            return False
        return any(window_start <= t <= window_end for t in times)

    def to_recovery(self) -> Dict[str, Any]:
        if not self.has_attempt_evidence:
            return empty_recovery()
        duration_ms: Optional[int] = None
        if (
            self.result in (RESULT_SUCCESS, RESULT_FAILED, RESULT_ABANDONED)
            and self.started_at is not None
            and self.ended_at is not None
        ):
            delta = self.ended_at - self.started_at
            ms = int(delta.total_seconds() * 1000)
            if ms >= 0:
                duration_ms = ms
        # In-progress: attempted true, result null (do not call FAILED).
        return {
            "attempted": True,
            "result": self.result,
            "duration_ms": duration_ms,
        }


def _generation_matches(started: _Attempt, terminal_generation: Optional[int]) -> bool:
    """Prefer generation_before + 1 == Succeeded.generation when both present."""
    if started.generation_before is None or terminal_generation is None:
        return True
    return terminal_generation == started.generation_before + 1


def build_attempts_for_child(rows: Sequence[EventRow]) -> List[_Attempt]:
    """
    Group ordered runtime events for one child into restart attempts.

    Primary chain boundary: RestartStarted. Terminal: Succeeded / Failed / Abandoned.
    RestartRequested without a following Started still counts as attempt evidence.
    """
    attempts: List[_Attempt] = []
    current: Optional[_Attempt] = None

    def close_current() -> None:
        nonlocal current
        if current is not None:
            attempts.append(current)
            current = None

    for row in rows:
        short = _short_type(row.event_type)
        child = _child_name(row)
        if not child:
            continue
        at = _ensure_aware(row.event_at)
        payload = _payload(row)

        if short == "RestartStarted":
            close_current()
            gen_before = _as_int(payload.get("generation_before"))
            current = _Attempt(
                child_name=child,
                started_at=at,
                generation_before=gen_before,
                has_attempt_evidence=True,
            )
            current.note(at)
            continue

        if short == "RestartSucceeded":
            gen = _as_int(payload.get("generation"))
            pid = _as_int(payload.get("pid"))
            if current is not None and _generation_matches(current, gen):
                current.result = RESULT_SUCCESS
                current.ended_at = at
                current.generation = gen
                current.pid = pid
                current.has_attempt_evidence = True
                current.note(at)
                close_current()
            else:
                close_current()
                att = _Attempt(
                    child_name=child,
                    ended_at=at,
                    generation=gen,
                    pid=pid,
                    result=RESULT_SUCCESS,
                    has_attempt_evidence=True,
                )
                att.note(at)
                attempts.append(att)
            continue

        if short == "RestartFailed":
            if current is not None:
                current.result = RESULT_FAILED
                current.ended_at = at
                current.has_attempt_evidence = True
                current.note(at)
                close_current()
            else:
                att = _Attempt(
                    child_name=child,
                    ended_at=at,
                    result=RESULT_FAILED,
                    has_attempt_evidence=True,
                )
                att.note(at)
                attempts.append(att)
            continue

        if short == "Abandoned":
            if current is not None:
                current.result = RESULT_ABANDONED
                current.ended_at = at
                current.has_attempt_evidence = True
                current.note(at)
                close_current()
            else:
                att = _Attempt(
                    child_name=child,
                    ended_at=at,
                    result=RESULT_ABANDONED,
                    has_attempt_evidence=True,
                )
                att.note(at)
                attempts.append(att)
            continue

        if short in ("RestartRequested", "RestartScheduled"):
            if current is None:
                current = _Attempt(child_name=child, has_attempt_evidence=True)
            else:
                current.has_attempt_evidence = True
            current.note(at)
            continue

    close_current()
    return attempts


def group_events_by_child(rows: Iterable[EventRow]) -> Dict[str, List[EventRow]]:
    grouped: Dict[str, List[EventRow]] = {}
    for row in rows:
        child = _child_name(row)
        if child is None or child not in SUPERVISED_RUNTIME_CHILDREN:
            continue
        short = _short_type(row.event_type)
        if short not in (
            "RestartRequested",
            "RestartScheduled",
            "RestartStarted",
            "RestartSucceeded",
            "RestartFailed",
            "Abandoned",
        ):
            continue
        grouped.setdefault(child, []).append(row)
    for child, items in grouped.items():
        items.sort(key=lambda r: (_ensure_aware(r.event_at), r.id))
    return grouped


def issue_time_window(
    *,
    opened_at: Optional[datetime],
    resolved_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> Optional[Tuple[datetime, datetime]]:
    if opened_at is None:
        return None
    start = _ensure_aware(opened_at) - LIFECYCLE_BOUNDARY
    clock = _ensure_aware(now or datetime.now(timezone.utc))
    if resolved_at is not None:
        end = _ensure_aware(resolved_at) + LIFECYCLE_BOUNDARY
    else:
        end = clock + LIFECYCLE_BOUNDARY
    if end < start:
        end = start
    return start, end


def select_attempt_for_issue(
    attempts: Sequence[_Attempt],
    *,
    window_start: datetime,
    window_end: datetime,
) -> Optional[_Attempt]:
    overlapping = [a for a in attempts if a.overlaps(window_start, window_end)]
    if not overlapping:
        return None
    # Latest recovery chain for this issue lifecycle.
    def sort_key(a: _Attempt) -> datetime:
        if a.ended_at is not None:
            return a.ended_at
        if a.started_at is not None:
            return a.started_at
        if a.evidence_times:
            return max(a.evidence_times)
        return window_start

    return max(overlapping, key=sort_key)


def recovery_for_issue(
    *,
    component_code: Optional[str],
    opened_at: Optional[datetime],
    resolved_at: Optional[datetime],
    attempts_by_child: Dict[str, List[_Attempt]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    if not is_runtime_correlatable(component_code):
        return empty_recovery()
    child = (component_code or "").strip()
    window = issue_time_window(
        opened_at=opened_at, resolved_at=resolved_at, now=now
    )
    if window is None:
        return empty_recovery()
    window_start, window_end = window
    attempts = attempts_by_child.get(child) or []
    chosen = select_attempt_for_issue(
        attempts, window_start=window_start, window_end=window_end
    )
    if chosen is None:
        return empty_recovery()
    return chosen.to_recovery()


def compute_query_bounds(
    issues: Sequence[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Optional[Tuple[datetime, datetime, List[str]]]:
    """
    Aggregate children + time bounds for one batched mon_event query.

    Returns (since, until, child_names) or None when nothing is correlatable.
    """
    clock = _ensure_aware(now or datetime.now(timezone.utc))
    children: List[str] = []
    starts: List[datetime] = []
    ends: List[datetime] = []

    for issue in issues:
        code = None
        comp = issue.get("component")
        if isinstance(comp, dict):
            code = comp.get("code")
        if not is_runtime_correlatable(code):
            continue
        children.append(str(code).strip())
        ts = issue.get("timestamps") or {}
        detected = ts.get("detected_at")
        resolved = ts.get("resolved_at")
        opened = _parse_iso(detected)
        resolved_dt = _parse_iso(resolved)
        window = issue_time_window(
            opened_at=opened, resolved_at=resolved_dt, now=clock
        )
        if window is None:
            continue
        starts.append(window[0])
        ends.append(window[1])

    if not children or not starts:
        return None
    return min(starts), max(ends), sorted(set(children))


def _parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return _ensure_aware(datetime.fromisoformat(text))
    except ValueError:
        return None


def enrich_issues_with_runtime_recovery(
    issues: List[Dict[str, Any]],
    event_rows: Sequence[EventRow],
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Apply recovery enrichment in memory (no I/O). Mutates issue dicts."""
    grouped = group_events_by_child(event_rows)
    attempts_by_child = {
        child: build_attempts_for_child(rows) for child, rows in grouped.items()
    }
    clock = _ensure_aware(now or datetime.now(timezone.utc))
    for issue in issues:
        code = None
        comp = issue.get("component")
        if isinstance(comp, dict):
            code = comp.get("code")
        ts = issue.get("timestamps") or {}
        issue["recovery"] = recovery_for_issue(
            component_code=code if isinstance(code, str) else None,
            opened_at=_parse_iso(ts.get("detected_at")),
            resolved_at=_parse_iso(ts.get("resolved_at")),
            attempts_by_child=attempts_by_child,
            now=clock,
        )
    return issues


def fetch_runtime_recovery_events(
    storage: Any,
    *,
    child_names: Sequence[str],
    since: datetime,
    until: datetime,
    component_ids: Optional[Sequence[UUID]] = None,
    limit: int = MAX_EVENTS_PER_QUERY,
) -> List[EventRow]:
    """
    One bounded query for recovery-relevant runtime events.

    Prefer component_id IN (...) when registry IDs are available; always
    re-filter by payload child_name in Python.
    """
    names = [c for c in child_names if c in SUPERVISED_RUNTIME_CHILDREN]
    if not names:
        return []
    # Fetch newest-first so LIMIT keeps the most recent attempts; reverse for
    # chronological attempt grouping. ASC+LIMIT would silently drop latest events.
    kwargs: Dict[str, Any] = {
        "event_types": list(RECOVERY_EVENT_TYPES),
        "since": since,
        "until": until,
        "order": "desc",
        "limit": max(1, min(int(limit), MAX_EVENTS_PER_QUERY)),
        "child_names": names,
    }
    if component_ids:
        kwargs["component_ids"] = list(component_ids)
    rows = storage.events.list_events(**kwargs)
    allowed = set(names)
    filtered = [r for r in rows if _child_name(r) in allowed]
    filtered.reverse()
    return filtered
