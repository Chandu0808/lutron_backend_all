# Monitoring Troubleshooting

## Monitoring disabled (503 on dashboard)

Cause: `MONITORING_ENABLED` false.  
Fix: set flag and restart API.

## Ingest 401

Cause: missing/wrong `MONITORING_INGEST_TOKEN`.  
Fix: align API + daemon token; header `X-Monitoring-Ingest-Token` or Bearer.

## Daemon heartbeats missing

Check ingest URL reachability, flags, token, and API ingest enabled.
Review daemon logs for `[monitoring]` warnings.

## Alerts never open

1. `MONITORING_ALERTS_ENABLED=true`
2. Rule `enabled=true` in DB
3. Scheduler running; job `monitoring_alert_engine` present
4. Telemetry present (e.g. stale heartbeat)

## Self-check FAILED

Read `[Monitoring Self-Check] FAILED ...` details:

- `pipeline_worker` → service failed to start
- `watchdog_running` → watchdog start error
- `*_registered` → flag on but scheduler job missing (start order / flag mismatch)
- `schema_indexes` → run schema ensure

## Queue drops / degraded pipeline

Ingress queue is bounded. Under load, lower-priority events drop per drop policy.
Check `/monitoring/pipeline` counters.

## Retention not deleting

Requires `MONITORING_ANALYTICS_ENABLED`. Confirm job `monitoring_retention` scheduled
and cutoffs / data age exceed policy windows.
