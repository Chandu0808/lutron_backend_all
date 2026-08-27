# Monitoring Phase 13 — Production Hardening (final)

## Delivered

- Retention job `monitoring_retention` (batched deletes)
- Config validation warnings
- Startup diagnostics report
- Runtime self-check + schema/index verify + performance bounds review
- Operational docs (OPERATIONS, DEPLOYMENT, RUNBOOK, BACKUP_AND_RECOVERY, TROUBLESHOOTING)
- `MONITORING_SCHEMA_VERSION = 1.0.0`

## Rollback

Unset `MONITORING_ENABLED` / `MONITORING_ANALYTICS_ENABLED`. Retention and engines stop scheduling.

## Not delivered

Frontend, charts, notifications, new telemetry/alerts/analytics features, rule editor.
