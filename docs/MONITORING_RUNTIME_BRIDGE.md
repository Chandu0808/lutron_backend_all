# Monitoring ↔ Runtime Event Bridge

**Status:** Implemented  
**Scope:** Observer-only bridge from Runtime Event Bus → Monitoring `mon_event`  
**Constraint:** Runtime never imports Monitoring; no restart control from Monitoring

## Summary

`RuntimeMonitoringSubscriber` (in `app/monitoring/runtime_bridge/`) listens to the Runtime Event Bus, maps events to `LifecycleEvent` (`runtime.*` types), and persists via `MonitoringService.record_runtime_event()`. Dashboard exposes read-only Runtime views.

## 1. Files created

| File | Role |
|------|------|
| `app/monitoring/runtime_bridge/__init__.py` | Package exports |
| `app/monitoring/runtime_bridge/mapper.py` | Runtime → Lifecycle mapping |
| `app/monitoring/runtime_bridge/subscriber.py` | Bus subscriber + start/stop |
| `tests/monitoring/unit/test_runtime_bridge.py` | Bridge tests |
| `docs/MONITORING_RUNTIME_BRIDGE.md` | This note |

## 2. Files modified

| File | Change |
|------|--------|
| `app/monitoring/service.py` | `record_runtime_event()` |
| `app/monitoring/storage/events.py` | `event_type_prefix` filter |
| `app/monitoring/dashboard_read_models.py` | Runtime read models |
| `app/api/routes/monitoring_dashboard.py` | Read-only `/runtime/*` routes |
| `app/main.py` | Attach/detach bridge when Monitoring enabled |

## 3. Bridge architecture

```
Runtime Event Bus
      │  (subscribe)
      ▼
RuntimeMonitoringSubscriber   ← app/monitoring only
      │
      ▼
RuntimeEventMapper
      │
      ▼
MonitoringService.record_runtime_event()
      │
      ▼
LifecycleEvent → mon_event (category=runtime)
      │
      ▼
Dashboard GET /monitoring/runtime/*
```

## 4. Event mapping

| Runtime event | Monitoring `event_type` | component |
|---------------|-------------------------|-----------|
| Child* / Restart* / Backoff / Abandoned | `runtime.<Name>` | child code or `api` |
| Supervisor* / Service* | `runtime.<Name>` | `api` |

Payload always includes `category: "runtime"`.

## 5. Dashboard endpoints (JWT, read-only)

| Method | Path |
|--------|------|
| GET | `/monitoring/runtime/events` |
| GET | `/monitoring/runtime/restarts` |
| GET | `/monitoring/runtime/abandoned` |
| GET | `/monitoring/runtime/supervisor` |
| GET | `/monitoring/runtime/restart-counts` |

No restart / control APIs.

## 6. Tests

Mapper, subscriber persistence mock, restart chain mapping, bridge singleton, `record_runtime_event` accept, dashboard helpers importable.

## 7. Acceptance checklist

- [x] Bridge inside Monitoring only  
- [x] Runtime Supervisor / ChildManager / HealthMonitor unchanged  
- [x] Persist via existing `mon_event` / LifecycleEvent  
- [x] Read-only dashboard endpoints  
- [x] No bidirectional control  

## 8. Rollback

Remove bridge start/stop from `main.py`; drop `runtime_bridge` package and `/runtime/*` routes. Historical `runtime.*` rows remain harmless.

## 9. Known limitations

- Requires `MONITORING_ENABLED` + registry component seeds for persistence  
- Unknown child names map to `api`  
- Live supervisor status available only while bridge is attached  
- No Monitoring alerts auto-created from Runtime events (observation only)  
