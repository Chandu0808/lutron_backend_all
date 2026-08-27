# Monitoring Phase 9 — Job instrumentation

## Delivered

- `app/monitoring/job_wrapper.py` — observational spans via `Instrumentation.job_run`
- Wired existing jobs:
  - `device_refresh` (API APScheduler)
  - `daily_data_backfill` (API APScheduler thread)
  - `occupancy_reconciliation` (+ `skipped_lock` when lock held)
  - `log_energy_stats` (energy_logger APScheduler)

## Not instrumented (no existing runtime job yet)

- `monitoring_analytics_rollup`
- `monitoring_retention`
- User/quick-control schedules (not in mon_job_definition; standards forbid default registration)

## Flags

```
MONITORING_ENABLED=true
MONITORING_JOBS_ENABLED=true
# energy_logger also needs ingest remote (Phase 6):
MONITORING_INGEST_ENABLED=true
MONITORING_INGEST_TOKEN=<secret>
```

## Flow

Existing job → start/finish timestamps → Instrumentation.job_run →
MonitoringService (API) or RemoteClient (daemon) → Storage → mon_job_run

## Rollback

Unset `MONITORING_JOBS_ENABLED` (or `MONITORING_ENABLED`).

## Not delivered

Alert Engine, Analytics, Dashboard, Energy metrics, HTTP/LEAP changes, new jobs.
