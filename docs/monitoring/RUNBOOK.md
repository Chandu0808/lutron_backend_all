# Monitoring Runbook

## Enable alerts

```sql
UPDATE monitoring.mon_alert_rule SET enabled = true
WHERE code = 'component_heartbeat_stale';
```

Set `MONITORING_ALERTS_ENABLED=true` and `MONITORING_JOBS_ENABLED=true`.

## Acknowledge an alert

`POST /monitoring/alerts/{id}/acknowledge` with LMS JWT.

## Force retention cycle (ops)

With analytics enabled, call from a shell:

```python
from app.monitoring.retention_job import run_retention_once
print(run_retention_once())
```

## Retention env overrides

| Env | Default days |
|-----|--------------|
| `MONITORING_RETENTION_PING_DAYS` | 7 |
| `MONITORING_RETENTION_HTTP_DAYS` | 14 |
| `MONITORING_RETENTION_EVENTS_DAYS` | 90 |
| `MONITORING_RETENTION_JOB_RUN_DAYS` | 30 |
| `MONITORING_RETENTION_SAMPLES_DAYS` | 7 |
| `MONITORING_RETENTION_ALERT_DAYS` | 90 |
| `MONITORING_RETENTION_ROLLUP_DAYS` | 180 |
| `MONITORING_RETENTION_HOUR` / `_MINUTE` | 3 / 30 |

## Never deleted

- `mon_component_health_current`
- `mon_processor_connectivity_current`
- Dimension tables (`mon_component`, definitions, `mon_alert_rule`)
- Open / acknowledged alert instances
