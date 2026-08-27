# Monitoring Phase 8 — HTTP request aggregates

## Delivered

- `app/monitoring/http_metrics.py`
  - Thread-safe in-memory aggregator (count / sum / min / max / status_class)
  - Pure ASGI middleware (duration + route template + status class)
  - Periodic flush via `Instrumentation.http_aggregate` only
- Wired in `app/main.py` (middleware install + start/stop with monitoring lifecycle)

## Flags

```
MONITORING_ENABLED=true
MONITORING_HTTP_METRICS_ENABLED=true
# optional:
MONITORING_HTTP_METRICS_FLUSH_SECONDS=15
```

Both flags required. Default OFF.

## Flow

Request → HttpMetricsMiddleware → in-memory agg → flush →
Instrumentation.http_aggregate → MonitoringService → Storage →
`mon_http_request_agg`

## Privacy

Does not capture query params, bodies, headers, JWT, user id, IP, or processor ids.

## Rollback

Unset `MONITORING_HTTP_METRICS_ENABLED` (or `MONITORING_ENABLED`).
Middleware remains installed but no-ops when flags are off.

## Not delivered

Tracing, per-request logs, dashboard, alerts, analytics, jobs, energy, LEAP changes.
