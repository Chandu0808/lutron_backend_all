"""
Alert condition helpers and typed findings (Phase 10).

Implements seeded rule types including streams, per-route HTTP, ping, runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.monitoring.registry import RegistrySnapshot
from app.monitoring.storage import MonitoringStorage
from app.monitoring.storage.types import AlertRuleRow


SUPPORTED_RULE_TYPES = frozenset(
    {
        "heartbeat_stale",
        "connectivity_down",
        "job_failures",
        "http_error_rate",
        "metric_threshold",
        "leap_connection_saturation",
        "ping_success_rate",
        "component_status",
        "leap_stream_health",
        "runtime_event",
    }
)

_DEFAULT_HTTP_EXCLUDE_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/monitoring",
)


@dataclass(frozen=True)
class AlertFinding:
    """One currently-true condition for a rule."""

    fingerprint: str
    title: str
    message: str
    component_id: Optional[UUID] = None
    processor_id: Optional[int] = None
    job_definition_id: Optional[UUID] = None
    detail: Dict[str, Any] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def compare_threshold(value: float, operator: str, threshold: float) -> bool:
    op = (operator or "gt").strip().lower()
    if op in ("gt", ">"):
        return value > threshold
    if op in ("gte", ">="):
        return value >= threshold
    if op in ("lt", "<"):
        return value < threshold
    if op in ("lte", "<="):
        return value <= threshold
    if op in ("eq", "==", "="):
        return value == threshold
    if op in ("neq", "!=", "<>"):
        return value != threshold
    return value > threshold


def evaluate_heartbeat_stale(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    stale_seconds = float(cfg.get("stale_seconds", 90))
    now = _as_aware(now) or _utcnow()
    findings: List[AlertFinding] = []

    for health in storage.health.get_all_health_current():
        last = _as_aware(health.last_heartbeat_at)
        if last is None:
            continue
        age = (now - last).total_seconds()
        if age < stale_seconds:
            continue
        component = next(
            (c for c in registry.components_by_code.values() if c.id == health.component_id),
            None,
        )
        code = component.code if component else str(health.component_id)
        findings.append(
            AlertFinding(
                fingerprint=f"heartbeat_stale:{code}",
                title=f"Heartbeat stale: {code}",
                message=(
                    f"Component '{code}' last heartbeat was {int(age)}s ago "
                    f"(threshold {int(stale_seconds)}s)."
                ),
                component_id=health.component_id,
                detail={
                    "component_code": code,
                    "age_seconds": age,
                    "stale_seconds": stale_seconds,
                    "last_heartbeat_at": last.isoformat(),
                },
            )
        )
    return findings


def evaluate_connectivity_down(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    min_down_seconds = float(cfg.get("min_down_seconds", 60))
    now = _as_aware(now) or _utcnow()
    findings: List[AlertFinding] = []

    for row in storage.connectivity.get_connectivity_current():
        if (row.status or "").lower() != "down":
            continue
        anchor = _as_aware(row.last_error_at) or _as_aware(row.updated_at)
        if anchor is None:
            continue
        down_for = (now - anchor).total_seconds()
        if down_for < min_down_seconds:
            continue
        findings.append(
            AlertFinding(
                fingerprint=f"connectivity_down:{row.processor_id}",
                title=f"Processor LEAP down: {row.processor_id}",
                message=(
                    f"Processor {row.processor_id} connectivity is down for "
                    f"{int(down_for)}s (threshold {int(min_down_seconds)}s)."
                ),
                processor_id=row.processor_id,
                detail={
                    "processor_id": row.processor_id,
                    "status": row.status,
                    "down_seconds": down_for,
                    "min_down_seconds": min_down_seconds,
                },
            )
        )
    return findings


def evaluate_job_failures(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    del now  # unused; consecutive runs are relative
    cfg = rule.config_json or {}
    n = int(cfg.get("consecutive_failures", 3))
    if n < 1:
        n = 1
    job_keys = cfg.get("job_keys")
    findings: List[AlertFinding] = []

    jobs = list(registry.jobs_by_key.values())
    if job_keys:
        allow = set(job_keys)
        jobs = [j for j in jobs if j.job_key in allow]

    for job in jobs:
        # Skip the alert engine's own runs to avoid self-feedback loops.
        if job.job_key == "monitoring_alert_engine":
            continue
        runs = storage.jobs.list_job_runs(job_definition_id=job.id, limit=max(n * 3, 20))
        terminal = [
            r
            for r in runs
            if r.outcome in ("success", "failure")
        ]
        if len(terminal) < n:
            continue
        window = terminal[:n]
        if not all(r.outcome == "failure" for r in window):
            continue
        findings.append(
            AlertFinding(
                fingerprint=f"job_failures:{job.job_key}",
                title=f"Job consecutive failures: {job.job_key}",
                message=f"Job '{job.job_key}' failed {n} consecutive runs.",
                job_definition_id=job.id,
                detail={
                    "job_key": job.job_key,
                    "consecutive_failures": n,
                    "run_ids": [r.id for r in window],
                },
            )
        )
    return findings


def _http_route_excluded(route: str, prefixes: List[str]) -> bool:
    r = route or ""
    return any(r.startswith(p) for p in prefixes)


def _http_last_exception(method: str, route: str) -> Dict[str, Any]:
    try:
        from app.monitoring.http_metrics import get_last_api_exception

        meta = get_last_api_exception(method, route)
        return dict(meta) if isinstance(meta, dict) else {}
    except Exception:
        return {}


def evaluate_http_error_rate(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    window_minutes = float(cfg.get("window_minutes", 5))
    max_error_rate = float(cfg.get("max_error_rate", 0.05))
    operator = str(cfg.get("operator", "gt"))
    per_route = bool(cfg.get("per_route", False))
    min_errors = int(cfg.get("min_errors", 1 if not per_route else 3))
    exclude = cfg.get("exclude_route_prefixes") or list(_DEFAULT_HTTP_EXCLUDE_PREFIXES)
    if not isinstance(exclude, list):
        exclude = list(_DEFAULT_HTTP_EXCLUDE_PREFIXES)
    now = _as_aware(now) or _utcnow()
    since = now - timedelta(minutes=window_minutes)

    api_id = registry.component_id("api")
    rows = storage.http_agg.query_http_agg(
        component_id=api_id, since=since, limit=5000
    )
    rows = [r for r in rows if not _http_route_excluded(r.route_template or "", exclude)]
    if not rows:
        return []

    if not per_route:
        total = sum(r.request_count for r in rows)
        if total <= 0:
            return []
        errors = sum(
            r.request_count for r in rows if (r.status_class or "").lower() == "5xx"
        )
        error_count_sum = sum(int(r.error_count or 0) for r in rows)
        if error_count_sum > errors:
            errors = error_count_sum
        if errors < min_errors:
            return []
        rate = errors / float(total)
        if not compare_threshold(rate, operator, max_error_rate):
            return []
        return [
            AlertFinding(
                fingerprint="http_error_rate:api",
                title="HTTP 5xx rate high",
                message=(
                    f"HTTP 5xx rate {rate:.4f} over {int(window_minutes)}m "
                    f"(threshold {max_error_rate})."
                ),
                component_id=api_id,
                detail={
                    "window_minutes": window_minutes,
                    "error_rate": rate,
                    "max_error_rate": max_error_rate,
                    "errors": errors,
                    "total": total,
                },
            )
        ]

    grouped: Dict[tuple, Dict[str, int]] = {}
    for r in rows:
        key = ((r.method or "GET").upper(), r.route_template or "/unknown")
        bucket = grouped.setdefault(key, {"total": 0, "errors": 0})
        bucket["total"] += int(r.request_count or 0)
        if (r.status_class or "").lower() == "5xx":
            bucket["errors"] += int(r.error_count or r.request_count or 0)

    findings: List[AlertFinding] = []
    for (method, route), bucket in grouped.items():
        total = bucket["total"]
        errors = bucket["errors"]
        if total <= 0 or errors < min_errors:
            continue
        rate = errors / float(total)
        if not compare_threshold(rate, operator, max_error_rate):
            continue
        exc_meta = _http_last_exception(method, route)
        detail: Dict[str, Any] = {
            "window_minutes": window_minutes,
            "error_rate": rate,
            "max_error_rate": max_error_rate,
            "errors": errors,
            "total": total,
            "method": method,
            "route_template": route,
        }
        if exc_meta:
            detail["exception"] = exc_meta
            if exc_meta.get("message"):
                detail["error"] = str(exc_meta["message"])
        findings.append(
            AlertFinding(
                fingerprint=f"http_error_rate:{method}:{route}",
                title=f"HTTP 5xx: {method} {route}",
                message=(
                    f"{method} {route} 5xx rate {rate:.4f} "
                    f"({errors}/{total}) over {int(window_minutes)}m."
                ),
                component_id=api_id,
                detail=detail,
            )
        )
    return findings


def evaluate_metric_threshold(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    del now
    cfg = rule.config_json or {}
    metric_key = cfg.get("metric_key")
    if not metric_key:
        return []

    metric_id = registry.metric_id(metric_key)
    if metric_id is None:
        return []

    samples = storage.metrics.query_metric_samples(
        metric_definition_id=metric_id, limit=1
    )
    if not samples:
        return []
    value = float(samples[0].value)

    # Ratio-of-capacity form used by db_pool_pressure seed.
    if "max_ratio_of_capacity" in cfg:
        cap_key = cfg.get("capacity_metric_key") or "db.pool.size"
        cap_id = registry.metric_id(cap_key)
        if cap_id is None:
            return []
        cap_samples = storage.metrics.query_metric_samples(
            metric_definition_id=cap_id, limit=1
        )
        if not cap_samples or float(cap_samples[0].value) <= 0:
            return []
        capacity = float(cap_samples[0].value)
        ratio = value / capacity
        threshold = float(cfg["max_ratio_of_capacity"])
        operator = str(cfg.get("operator", "gt"))
        if not compare_threshold(ratio, operator, threshold):
            return []
        return [
            AlertFinding(
                fingerprint=f"metric_threshold:{metric_key}:ratio",
                title=f"Metric threshold: {metric_key}",
                message=(
                    f"{metric_key} ratio {ratio:.4f} of {cap_key} "
                    f"(threshold {threshold})."
                ),
                detail={
                    "metric_key": metric_key,
                    "value": value,
                    "capacity_metric_key": cap_key,
                    "capacity": capacity,
                    "ratio": ratio,
                    "threshold": threshold,
                },
            )
        ]

    threshold = cfg.get("threshold")
    if threshold is None:
        return []
    operator = str(cfg.get("operator", "gt"))
    thr = float(threshold)
    if not compare_threshold(value, operator, thr):
        return []
    return [
        AlertFinding(
            fingerprint=f"metric_threshold:{metric_key}",
            title=f"Metric threshold: {metric_key}",
            message=f"{metric_key}={value} {operator} {thr}.",
            detail={
                "metric_key": metric_key,
                "value": value,
                "operator": operator,
                "threshold": thr,
            },
        )
    ]


def evaluate_leap_connection_saturation(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    """
    Critical: processor hit LEAP max concurrent clients (10).

    Evidence (either):
    - connectivity current detail.reason == max_clients within lookback
    - latest leap.connection_saturated sample >= 1
    """
    now = now or _utcnow()
    cfg = rule.config_json or {}
    lookback = int(cfg.get("lookback_minutes") or 15)
    since = now - timedelta(minutes=lookback)
    max_conn = int(cfg.get("max_connections") or 10)
    findings: List[AlertFinding] = []
    seen: set = set()

    for row in storage.connectivity.get_connectivity_current():
        detail = row.detail_json or {}
        reason = str(detail.get("reason") or detail.get("last_connect_error") or "")
        event = str(detail.get("event_type") or "")
        is_max = reason == "max_clients" or event == "max_clients" or bool(
            detail.get("max_clients")
        )
        if not is_max:
            continue
        updated = _as_aware(row.updated_at) or _as_aware(row.last_error_at)
        if updated is not None and updated < since:
            continue
        pid = int(row.processor_id)
        if pid in seen:
            continue
        seen.add(pid)
        findings.append(
            AlertFinding(
                fingerprint=f"leap_saturation:{pid}",
                title="Processor LEAP connection saturation",
                message=(
                    f"Processor {pid} rejected LEAP clients "
                    f"(max concurrent connections ~ {max_conn})."
                ),
                processor_id=pid,
                detail={
                    "processor_id": pid,
                    "reason": "max_clients",
                    "max_connections": max_conn,
                    "connectivity_status": row.status,
                    "detail": detail,
                    "lookback_minutes": lookback,
                },
            )
        )

    metric_key = cfg.get("metric_key") or "leap.connection_saturated"
    metric_id = registry.metric_id(metric_key)
    if metric_id is not None:
        samples = storage.metrics.query_metric_samples(
            metric_definition_id=metric_id, since=since, limit=50
        )
        for sample in samples:
            if float(sample.value) < float(cfg.get("threshold") or 1):
                continue
            pid = sample.processor_id
            if pid is None:
                # global sample without processor — still surface once
                fp = "leap_saturation:unknown"
                if fp in seen:
                    continue
                seen.add(fp)
                findings.append(
                    AlertFinding(
                        fingerprint=fp,
                        title="Processor LEAP connection saturation",
                        message=(
                            f"{metric_key} indicates LEAP max-clients saturation "
                            f"(limit {max_conn})."
                        ),
                        detail={
                            "metric_key": metric_key,
                            "value": float(sample.value),
                            "max_connections": max_conn,
                        },
                    )
                )
                continue
            pid_i = int(pid)
            if pid_i in seen:
                continue
            seen.add(pid_i)
            findings.append(
                AlertFinding(
                    fingerprint=f"leap_saturation:{pid_i}",
                    title="Processor LEAP connection saturation",
                    message=(
                        f"Processor {pid_i} LEAP connection saturated "
                        f"(max {max_conn} clients)."
                    ),
                    processor_id=pid_i,
                    detail={
                        "processor_id": pid_i,
                        "metric_key": metric_key,
                        "value": float(sample.value),
                        "max_connections": max_conn,
                    },
                )
            )

    return findings


def evaluate_ping_success_rate(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    del registry
    cfg = rule.config_json or {}
    window_minutes = float(cfg.get("window_minutes", 15))
    min_success_rate = float(cfg.get("min_success_rate", 0.8))
    min_samples = int(cfg.get("min_samples", 5))
    now = _as_aware(now) or _utcnow()
    since = now - timedelta(minutes=window_minutes)
    rows = storage.ping.list_leap_pings(since=since, limit=5000)
    by_proc: Dict[int, List[bool]] = {}
    for row in rows:
        by_proc.setdefault(int(row.processor_id), []).append(bool(row.success))
    findings: List[AlertFinding] = []
    for pid, outcomes in by_proc.items():
        if len(outcomes) < min_samples:
            continue
        successes = sum(1 for ok in outcomes if ok)
        rate = successes / float(len(outcomes))
        if rate >= min_success_rate:
            continue
        findings.append(
            AlertFinding(
                fingerprint=f"ping_success_rate:{pid}",
                title=f"LEAP ping success rate low: {pid}",
                message=(
                    f"Processor {pid} ping success {rate:.2f} "
                    f"({successes}/{len(outcomes)}) over {int(window_minutes)}m "
                    f"(min {min_success_rate})."
                ),
                processor_id=pid,
                detail={
                    "processor_id": pid,
                    "success_rate": rate,
                    "min_success_rate": min_success_rate,
                    "successes": successes,
                    "samples": len(outcomes),
                    "window_minutes": window_minutes,
                },
            )
        )
    return findings


def evaluate_component_status(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    code = str(cfg.get("component_code") or "").strip()
    if not code:
        return []
    component_id = registry.component_id(code)
    if component_id is None:
        return []
    row = storage.health.get_health_current(component_id)
    if row is None:
        return []
    status = (row.status or "").strip().lower()
    healthy = set(cfg.get("healthy_statuses") or ("up", "starting"))
    now = _as_aware(now) or _utcnow()
    last = _as_aware(row.last_heartbeat_at)
    stale_seconds = float(cfg.get("stale_seconds", 90))
    is_stale = False
    if last is not None:
        is_stale = (now - last).total_seconds() >= stale_seconds
    if status in healthy and not is_stale:
        return []
    return [
        AlertFinding(
            fingerprint=f"component_status:{code}",
            title=f"Component degraded: {code}",
            message=(
                f"Component '{code}' status={status or 'unknown'}"
                + (" (heartbeat stale)." if is_stale else ".")
            ),
            component_id=component_id,
            detail={
                "component_code": code,
                "status": status,
                "stale": is_stale,
                "last_heartbeat_at": last.isoformat() if last else None,
            },
        )
    ]


def _latest_metric_by_processor(
    storage: MonitoringStorage,
    metric_id,
    *,
    limit: int = 200,
) -> Dict[int, float]:
    samples = storage.metrics.query_metric_samples(
        metric_definition_id=metric_id, limit=limit
    )
    latest: Dict[int, float] = {}
    for sample in samples:
        if sample.processor_id is None:
            continue
        pid = int(sample.processor_id)
        if pid in latest:
            continue
        latest[pid] = float(sample.value)
    return latest


def evaluate_leap_stream_health(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    streams = cfg.get("streams") or ["area", "zone", "loadcontroller"]
    stale_seconds = float(cfg.get("stale_seconds", 180))
    now = _as_aware(now) or _utcnow()
    now_epoch = now.timestamp()

    connectivity_up: Dict[int, bool] = {}
    try:
        for row in storage.connectivity.get_connectivity_current():
            connectivity_up[int(row.processor_id)] = (
                (row.status or "").lower() == "up"
            )
    except Exception:
        connectivity_up = {}

    metric_last = {
        "area": "leap.stream.area.last_event_epoch",
        "zone": "leap.stream.zone.last_event_epoch",
        "loadcontroller": "leap.stream.lc.last_event_epoch",
    }
    metric_sub = {
        "area": "leap.stream.area.subscribed",
        "zone": "leap.stream.zone.subscribed",
        "loadcontroller": "leap.stream.lc.subscribed",
    }
    findings: List[AlertFinding] = []
    for stream in streams:
        name = str(stream).strip().lower()
        last_key = metric_last.get(name)
        sub_key = metric_sub.get(name)
        if not last_key or not sub_key:
            continue
        last_id = registry.metric_id(last_key)
        sub_id = registry.metric_id(sub_key)
        last_by_proc = (
            _latest_metric_by_processor(storage, last_id) if last_id else {}
        )
        sub_by_proc = _latest_metric_by_processor(storage, sub_id) if sub_id else {}
        processor_ids = set(last_by_proc) | set(sub_by_proc)
        for pid in processor_ids:
            # Avoid duplicating processor LEAP-down when the whole session is dead.
            if connectivity_up.get(pid) is False:
                continue
            subscribed = sub_by_proc.get(pid, 1.0) >= 0.5
            last_epoch = last_by_proc.get(pid, 0.0)
            age = (now_epoch - last_epoch) if last_epoch > 0 else None
            if not subscribed:
                findings.append(
                    AlertFinding(
                        fingerprint=f"leap_stream_down:{name}:{pid}",
                        title=f"{name.title()} stream down: processor {pid}",
                        message=(
                            f"Processor {pid} {name} subscribe stream is not active."
                        ),
                        processor_id=pid,
                        detail={
                            "processor_id": pid,
                            "stream": name,
                            "reason": "unsubscribed",
                            "subscribed": False,
                        },
                    )
                )
                continue
            if age is not None and age >= stale_seconds:
                findings.append(
                    AlertFinding(
                        fingerprint=f"leap_stream_silent:{name}:{pid}",
                        title=f"{name.title()} stream silent: processor {pid}",
                        message=(
                            f"Processor {pid} {name} stream subscribed but no events "
                            f"for {int(age)}s (threshold {int(stale_seconds)}s)."
                        ),
                        processor_id=pid,
                        detail={
                            "processor_id": pid,
                            "stream": name,
                            "reason": "silent",
                            "age_seconds": age,
                            "stale_seconds": stale_seconds,
                        },
                    )
                )
    return findings


def evaluate_runtime_event(
    storage: MonitoringStorage,
    registry: RegistrySnapshot,
    rule: AlertRuleRow,
    *,
    now: Optional[datetime] = None,
) -> List[AlertFinding]:
    cfg = rule.config_json or {}
    runtime_event = str(cfg.get("runtime_event") or "").strip()
    if not runtime_event:
        return []
    window_minutes = float(cfg.get("window_minutes", 10))
    count_threshold = int(cfg.get("count_threshold", 1))
    now = _as_aware(now) or _utcnow()
    since = now - timedelta(minutes=window_minutes)
    event_type = (
        runtime_event
        if runtime_event.startswith("runtime.")
        else f"runtime.{runtime_event}"
    )
    rows = storage.events.list_events(
        event_type=event_type, since=since, limit=500
    )
    by_child: Dict[str, int] = {}
    for row in rows:
        payload = row.payload_json or {}
        child = str(payload.get("child_name") or "api")
        by_child[child] = by_child.get(child, 0) + 1
    findings: List[AlertFinding] = []
    for child, count in by_child.items():
        if count < count_threshold:
            continue
        component_id = registry.component_id(child)
        fp_prefix = str(cfg.get("fingerprint_prefix") or f"runtime_event:{runtime_event}")
        findings.append(
            AlertFinding(
                fingerprint=f"{fp_prefix}:{child}",
                title=f"Runtime {runtime_event}: {child}",
                message=(
                    f"{child} {runtime_event} x{count} in {int(window_minutes)}m "
                    f"(threshold {count_threshold})."
                ),
                component_id=component_id,
                detail={
                    "child_name": child,
                    "runtime_event": runtime_event,
                    "count": count,
                    "window_minutes": window_minutes,
                    "count_threshold": count_threshold,
                },
            )
        )
    return findings


EVALUATORS = {
    "heartbeat_stale": evaluate_heartbeat_stale,
    "connectivity_down": evaluate_connectivity_down,
    "job_failures": evaluate_job_failures,
    "http_error_rate": evaluate_http_error_rate,
    "metric_threshold": evaluate_metric_threshold,
    "leap_connection_saturation": evaluate_leap_connection_saturation,
    "ping_success_rate": evaluate_ping_success_rate,
    "component_status": evaluate_component_status,
    "leap_stream_health": evaluate_leap_stream_health,
    "runtime_event": evaluate_runtime_event,
}
