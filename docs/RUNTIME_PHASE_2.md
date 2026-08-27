# Runtime Recovery Framework — Phase 2

**Status:** Implemented  
**Scope:** Health monitor + immediate restart on unexpected child exit  
**Reference:** [`RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md`](./RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md), [`RUNTIME_PHASE_1.md`](./RUNTIME_PHASE_1.md)

## Summary

Added a single `HealthMonitor` thread on `RuntimeSupervisor` that detects unexpected child exits and calls `ChildManager.restart()` (dispose old Process → construct new → start). No policies, backoff, retry limits, Windows Service, or Monitoring integration.

## Files created

| File | Role |
|------|------|
| `app/runtime/health_monitor.py` | Poll thread |
| `tests/runtime/test_phase2_restart.py` | Phase 2 tests |
| `docs/RUNTIME_PHASE_2.md` | This note |

## Files modified

| File | Change |
|------|--------|
| `app/runtime/lifecycle.py` | Add `RESTARTING` |
| `app/runtime/child_manager.py` | `restart()`, clean-exit rules, health tick |
| `app/runtime/supervisor.py` | `start_monitor` / `stop_monitor` |
| `app/runtime/__init__.py` | Export `HealthMonitor` |
| `app/main.py` | `start_monitor()` after `start_all()` |

## Monitor thread architecture

```
RuntimeSupervisor
  └── HealthMonitor (one daemon thread)
        every RUNTIME_POLL_INTERVAL_SECONDS (default 2s):
          for each ChildManager:
            handle_health_tick(allow_restart=supervisor.running)
```

Starts after `start_all()`. Stops before `stop_all()` / on `shutdown()`.

## Restart flow

```
RUNNING → process dies unexpectedly
       → FAILED
       → RESTARTING (dispose old Process)
       → new Process.start()
       → RUNNING
```

Restart only if: enabled, was RUNNING, unexpected exit, supervisor not shutting down, not intentional stop.

## Lifecycle changes

Added `RESTARTING`. Clean exits stay `STOPPED` (no restart):

- exit `0` — Listener / LoadController idle (no processors)
- `lock_aware` + exit `1` — Energy Logger lock busy

## Tests

`pytest tests/runtime/` — unexpected crash restart, new Process identity, clean idle, lock busy, intentional stop, monitor shutdown.

## Acceptance checklist

- [x] Unexpected exit → new Process restarted  
- [x] Clean idle / lock busy / intentional stop → no restart  
- [x] Monitor stops on shutdown  
- [x] No policies / backoff / Monitoring hooks  

## Rollback

Disable `start_monitor()` in `main.py`; remove `health_monitor.py` and Phase 2 ChildManager/Supervisor methods if needed.

## Known limitations

- Immediate restart with **no** delay, retry cap, or backoff (crash loops possible)  
- Windows `terminate()` may report exit `0`/`1`; classification uses exitcode rules above  
- `lock_aware` + exit `1` always treated as clean (matches energy logger lock exit)  
- No Monitoring events  
