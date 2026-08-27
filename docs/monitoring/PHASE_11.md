# Monitoring Phase 11 — Analytics Engine

## Delivered

- `app/monitoring/analytics/`
  - `bucket.py` — UTC hour/day buckets
  - `aggregators.py` — HTTP / jobs / ping / alerts / metric samples
  - `retention.py` — constants/helpers only (no deletes)
  - `rollup_engine.py` — incremental idempotent cycle + schedule
- Additive metric seeds for analytics.* rollup targets
- Storage read: `list_alert_instances(since/until)`
- Wired in `main.py` behind `MONITORING_ANALYTICS_ENABLED`

## Flags

```
MONITORING_ENABLED=true
MONITORING_ANALYTICS_ENABLED=true
MONITORING_JOBS_ENABLED=true
# optional:
MONITORING_ANALYTICS_LOOKBACK_HOURS=48
MONITORING_ANALYTICS_LOOKBACK_DAYS=14
```

## Schedule

`monitoring_analytics_rollup` — Cron at minute 5 each hour (`max_instances=1`).

## Writes

Only `mon_metric_rollup` (via Storage upsert). Own `job_run` via Instrumentation.

## Rollback

Unset `MONITORING_ANALYTICS_ENABLED`.

## Not delivered

Dashboard, charts, API changes, retention deletion, alerts/notifications.
