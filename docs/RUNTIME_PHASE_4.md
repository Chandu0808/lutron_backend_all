# Runtime Recovery Framework — Phase 4

**Status:** Implemented  
**Scope:** Internal Runtime Event Bus (not Monitoring)  
**Reference:** Design + Phases 1–3

## Summary

Added an in-process, synchronous, thread-safe `RuntimeEventBus`. Supervisor and ChildManager **publish** only. HealthMonitor stays detection-only (no policy/event publishing). Built-in Logging / Diagnostics / Recording subscribers. No Monitoring / Instrumentation / alerts / dashboard / Windows Service.

## 1. Files created

| File | Role |
|------|------|
| `app/runtime/events.py` | Event model, bus, subscribers |
| `tests/runtime/test_phase4_events.py` | Phase 4 tests |
| `docs/RUNTIME_PHASE_4.md` | This note |

## 2. Files modified

| File | Change |
|------|--------|
| `app/runtime/child_manager.py` | Publish `ChildStarted` / `ChildStopped` / `ChildFailed` |
| `app/runtime/supervisor.py` | Own bus; publish orchestration + supervisor lifecycle |
| `app/runtime/__init__.py` | Export events / subscribers |

## 3. Event architecture

```
ChildManager ──publish──► RuntimeEventBus ──sync──► Subscribers
Supervisor   ──publish──►        │                    (Logging,
HealthMonitor  (no publish)      │                     Diagnostics,
                                 │                     Recording/tests)
```

## 4. Event model

Immutable dataclasses: `ChildStarted`, `ChildStopped`, `ChildFailed`, `RestartRequested`, `RestartScheduled`, `RestartStarted`, `RestartSucceeded`, `RestartFailed`, `BackoffEntered`, `Abandoned`, `SupervisorStarted`, `SupervisorStopped`.

## 5. Subscriber model

| Subscriber | Role |
|------------|------|
| `LoggingSubscriber` | INFO log each event (default on) |
| `DiagnosticsSubscriber` | Ring buffer; `supervisor.diagnostics` |
| `RecordingSubscriber` | Ordered capture for tests |

Handler exceptions are isolated. Duplicate subscriptions allowed (N deliveries).

## 6. Tests

`pytest tests/runtime/` — **37 passed** (order, filter, duplicates, thread safety, restart sequence with backoff, zero-delay path).

## 7. Acceptance checklist

- [x] Internal bus only (no Monitoring)  
- [x] Supervisor publishes, does not consume  
- [x] ChildManager lifecycle events only  
- [x] HealthMonitor detection-only  
- [x] Restart sequence: Requested → Scheduled → BackoffEntered → Started → Succeeded  

## 8. Rollback

Remove `events.py` wiring; drop `event_bus` args from Supervisor/ChildManager.

## 9. Known limitations

- No persistence / replay  
- Synchronous delivery on publisher thread  
- No Monitoring bridge  
- Diagnostics ring is process-local memory only  
