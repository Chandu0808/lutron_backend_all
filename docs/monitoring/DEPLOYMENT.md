# Monitoring Deployment

## Prerequisites

- PostgreSQL (same LMS database)
- API process (uvicorn) with env flags
- Optional daemons: `listener`, `energy_logger`, `loadcontroller_listener`

## Fresh install

1. Deploy code with monitoring package present.
2. Set `MONITORING_ENABLED=true` on API.
3. Start API → `ensure_monitoring_schema` + bootstrap seeds run.
4. Confirm log: `[Monitoring Startup Report]` and `[Monitoring Self-Check] OK`.
5. Optionally enable ingest/alerts/analytics/HTTP/jobs/LEAP flags.

## Daemon env (listener / energy / LC)

```
MONITORING_ENABLED=true
MONITORING_INGEST_ENABLED=true
MONITORING_INGEST_TOKEN=<same as API>
MONITORING_INGEST_URL=http://<api-host>:8000/monitoring/ingest
# optional LEAP on listener:
MONITORING_LEAP_TELEMETRY=true
```

## Schema apply (offline)

```
python migrations/apply_monitoring_schema.py
python migrations/apply_monitoring_bootstrap.py
```

Schema version constant: `MONITORING_SCHEMA_VERSION` in `app/database/migrate_monitoring.py`.

## Rollback

Unset `MONITORING_ENABLED` (and child flags). Monitoring modules become inert; LMS continues.
Schema tables may remain; they are unused when flags are off.
