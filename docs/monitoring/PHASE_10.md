# Monitoring Phase 10 — Alert Engine

## Delivered

- `app/monitoring/alerts/`
  - `conditions.py` — seeded rule types only
  - `evaluator.py` — evaluate one rule
  - `state_manager.py` — open / suppress / ack / resolve
  - `engine.py` — scheduled cycle + `monitoring_alert_engine` job
- Job seed: `monitoring_alert_engine`
- Storage: active-alert lookup includes `acknowledged` for dedup
- Wired in `main.py` behind `MONITORING_ALERTS_ENABLED`

## Supported rule types

| rule_type | Seed code |
|-----------|-----------|
| `heartbeat_stale` | component_heartbeat_stale |
| `connectivity_down` | processor_leap_down |
| `job_failures` | job_consecutive_failures |
| `http_error_rate` | http_5xx_rate |
| `metric_threshold` | db_pool_pressure (+ generic threshold) |

Unsupported seeded types (`ping_success_rate`, `component_status`) are skipped when enabled.

## Flags

```
MONITORING_ENABLED=true
MONITORING_ALERTS_ENABLED=true
MONITORING_JOBS_ENABLED=true   # so engine job_run persists
# optional:
MONITORING_ALERT_EVAL_INTERVAL_SECONDS=60
```

Enable individual rules in DB (`mon_alert_rule.enabled=true`); seeds default to disabled.

## Writes

Only `mon_alert_instance` (via Storage). Own `job_run` via Instrumentation only.

## Rollback

Unset `MONITORING_ALERTS_ENABLED`. Remove scheduled job on shutdown/start skip.

## Not delivered

Email/Slack/SMS/webhooks, dashboard, analytics, rule UI, escalation, silencing, maintenance windows.
