# Runtime Recovery Framework — Phase 3

**Status:** Implemented  
**Scope:** Restart policies + backoff / crash-loop protection  
**Reference:** [`RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md`](./RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md), Phase 1–2 notes

## Summary

HealthMonitor is **detection only**. Restarts go through `RuntimeSupervisor.request_restart()` → policy evaluation → backoff → `ChildManager.restart()`. Crash loops end in `ABANDONED` after `RUNTIME_MAX_RESTARTS` failures in the failure window. No Windows Service, Monitoring, or events bus.

## 1. Files created

| File | Role |
|------|------|
| `app/runtime/restart_policy.py` | Policy kinds + evaluate |
| `app/runtime/restart_backoff.py` | Delay strategies + counters |
| `tests/runtime/test_phase3_policies.py` | Phase 3 tests |
| `docs/RUNTIME_PHASE_3.md` | This note |

## 2. Files modified

| File | Change |
|------|--------|
| `app/runtime/lifecycle.py` | `BACKOFF`, `ABANDONED` |
| `app/runtime/process_descriptor.py` | Default policy `OnFailure` |
| `app/runtime/child_manager.py` | Backoff state; no direct auto-restart |
| `app/runtime/health_monitor.py` | Detection-only `on_tick` |
| `app/runtime/supervisor.py` | `request_restart`, `handle_monitor_tick` |
| `app/runtime/__init__.py` | Exports |
| `app/main.py` | Explicit `on_failure` on descriptors |
| Phase 1–2 tests | Policy default + fast backoff config |

## 3. Restart policy architecture

```
HealthMonitor.tick
  → Supervisor.handle_monitor_tick()
      → ChildManager.observe()
      → if FAILED/STOPPED needing eval: request_restart(name)
      → if BACKOFF due: execute restart

request_restart(name)
  → evaluate_restart_policy(kind, context)
  → DENY | ABANDON | ALLOW
  → ALLOW → backoff delay → BACKOFF (or immediate if delay=0)
  → later → ChildManager.restart()  # new Process only
```

| Policy | Behavior |
|--------|----------|
| **Never** | No auto-restart |
| **ManualOnly** | No auto-restart |
| **OnFailure** | Restart crashes only (default) |
| **LimitedRetries** | Same crash gate + shared max-restarts |
| **Always** | Restart crash and clean idle (not lock_busy) |

All auto policies enforce `max_restarts` in the failure window.

## 4. Backoff algorithm

- Strategies: `fixed`, `linear`, `exponential` (default)
- `delay = min(cap, base * multiplier^attempt)` for exponential
- Failure timestamps in sliding `failure_window_seconds`
- After `reset_after_stable_seconds` of continuous RUNNING, counters reset
- On abandon: cooldown (`cooldown_seconds`); state stays `ABANDONED` until manual `start()`

## 5. Crash-loop protection

```
Crash → (allow) BACKOFF/RESTART → Crash → … → failures > MAX_RESTARTS
  → ABANDONED → no further auto-restart
```

## 6. Lifecycle updates

`RUNNING → FAILED → BACKOFF → RESTARTING → RUNNING`  
`RUNNING → FAILED → ABANDONED`

## 7. Configuration

| Env | Default |
|-----|---------|
| `RUNTIME_MAX_RESTARTS` | `5` |
| `RUNTIME_RESTART_DELAY_SECONDS` | `2` |
| `RUNTIME_MAX_BACKOFF_SECONDS` | `60` |
| `RUNTIME_FAILURE_WINDOW_SECONDS` | `600` |
| `RUNTIME_BACKOFF` | `exponential` |
| `RUNTIME_POLL_INTERVAL_SECONDS` | `2` |

## 8. Tests

`pytest tests/runtime/` — **29 passed** (policies, backoff curve, stable reset, never/manual/always, crash-loop abandon, delayed BACKOFF).

## 9. Acceptance checklist

- [x] HealthMonitor does not call `restart()` directly  
- [x] Policies Never / Always / OnFailure / LimitedRetries / ManualOnly  
- [x] Backoff + max restarts → ABANDONED  
- [x] Counter reset after stable run  
- [x] No Monitoring / Windows Service / events.py  

## 10. Rollback

Revert Phase 3 modules; restore Phase 2 direct `handle_health_tick` restart path if needed.

## 11. Known limitations

- No internal Runtime event bus yet  
- `Always` + immediate clean-exit can burn restart budget quickly  
- Abandon cooldown clears counters but state remains `ABANDONED` until manual start  
- No dashboard / Monitoring alerts on abandon  
