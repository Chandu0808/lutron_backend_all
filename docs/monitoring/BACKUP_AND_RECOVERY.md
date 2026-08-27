# Monitoring Backup and Recovery

## Backup

Include PostgreSQL schema `monitoring` in regular LMS database backups
(pg_dump / snapshot). Critical objects:

- Dimension tables (components, jobs, metrics, alert rules)
- Current-state tables (health, connectivity)
- Historical samples / events / job runs / http agg / rollups / alert instances

## Restore

1. Restore database including `monitoring` schema.
2. Start API with `MONITORING_ENABLED=true`.
3. Bootstrap is idempotent (re-seed dimensions without clobbering `enabled` on rules).
4. Confirm startup report + self-check.

## Partial recovery

If only dimensions are missing: run `migrations/apply_monitoring_bootstrap.py`.
If schema missing: `migrations/apply_monitoring_schema.py` then bootstrap.

## Data loss notes

Retention permanently deletes aged historical rows. Current-state and open alerts
are preserved. Restore from backup if historical telemetry is required.
