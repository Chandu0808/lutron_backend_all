# Monitoring Phase 7 — LEAP connectivity & ping telemetry

## Delivered

- `app/monitoring/leap_telemetry.py` — non-blocking queue bridge → Instrumentation
- Listener instrumentation (observational only):
  - Connectivity: `connected` / `disconnected` / `reconnecting` / `connection_failed`
  - LEAP ping: success (+ RTT ms), send failure, timeout failure (~10s)
- Entrypoint start/stop alongside daemon heartbeat
- Unit + integration tests

## Flags

```
MONITORING_ENABLED=true
MONITORING_INGEST_ENABLED=true
MONITORING_LEAP_TELEMETRY=true
MONITORING_INGEST_TOKEN=<secret>
# optional (shared with Phase 6):
MONITORING_INGEST_URL=http://127.0.0.1:8000/monitoring/ingest
```

## Flow

Existing listener state / ping → LeapTelemetryBridge → Instrumentation.connectivity|leap_ping
→ RemoteClient → POST /monitoring/ingest → MonitoringService → Storage

## Storage (no schema changes)

| Signal | Tables |
|--------|--------|
| Connectivity | `mon_processor_connectivity_current`, `mon_event` |
| Ping | `mon_leap_ping_sample` |

## Not delivered (later phases)

Alert Engine, Analytics/rollups, Dashboard, HTTP metrics, Job/Scheduler instrumentation, Energy metrics, Remote configuration.

## Rollback

Unset `MONITORING_LEAP_TELEMETRY` (or `MONITORING_ENABLED` / `MONITORING_INGEST_ENABLED`).
Listener LEAP connect/ping timing is unchanged without the flag / when bridge is idle.
