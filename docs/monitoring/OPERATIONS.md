# Monitoring Operations Guide

## Feature flags

| Flag | Purpose | Default |
|------|---------|---------|
| `MONITORING_ENABLED` | Master switch | off |
| `MONITORING_INGEST_ENABLED` | Daemon HTTP ingest | off |
| `MONITORING_INGEST_TOKEN` | Shared secret for ingest | unset |
| `MONITORING_INGEST_URL` | Daemon ingest URL | `http://127.0.0.1:8000/monitoring/ingest` |
| `MONITORING_LEAP_TELEMETRY` | Listener connectivity + ping | off |
| `MONITORING_HTTP_METRICS_ENABLED` | API HTTP aggregates | off |
| `MONITORING_JOBS_ENABLED` | Job run instrumentation | off |
| `MONITORING_ALERTS_ENABLED` | Alert engine | off |
| `MONITORING_ANALYTICS_ENABLED` | Rollups + retention | off |

## Scheduled monitoring jobs

| Job id | When | Flag |
|--------|------|------|
| `monitoring_alert_engine` | every minute | alerts |
| `monitoring_analytics_rollup` | hourly `:05` | analytics |
| `monitoring_retention` | daily `03:30` (configurable) | analytics |

## Day-2 operations

1. Enable `MONITORING_ENABLED`, bootstrap schema/dimensions on API startup.
2. Enable ingest + token on API and daemons for remote heartbeats / LEAP telemetry.
3. Enable alert rules in `monitoring.mon_alert_rule` (`enabled=true`) before expecting opens.
4. Review startup report and self-check lines in API logs.
5. Use JWT dashboard APIs under `/monitoring/*` for reads and alert acknowledge.

## Retention

Batch deletes of historical rows only. Current-state tables are never deleted.
Override windows with `MONITORING_RETENTION_*_DAYS` env vars (see RUNBOOK).
