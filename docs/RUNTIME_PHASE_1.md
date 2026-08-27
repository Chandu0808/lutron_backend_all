# Runtime Recovery Framework — Phase 1

**Status:** Implemented  
**Scope:** Supervisor foundation / ownership only  
**Reference:** [`RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md`](./RUNTIME_RECOVERY_FRAMEWORK_DESIGN.md)

## Summary

Replaced one-shot `multiprocessing.Process` handles in `app/main.py` with `RuntimeSupervisor` → descriptors → `ChildManager` ownership. No restart, polling, backoff, policies, events bus, Windows Service, or Monitoring integration.

## Files created

| File | Role |
|------|------|
| `app/runtime/__init__.py` | Package exports |
| `app/runtime/process_descriptor.py` | Immutable `ProcessDescriptor` |
| `app/runtime/lifecycle.py` | `ChildState` enum |
| `app/runtime/child_manager.py` | Per-child Process owner |
| `app/runtime/supervisor.py` | `RuntimeSupervisor` |
| `tests/runtime/__init__.py` | Test package |
| `tests/runtime/test_phase1_supervisor.py` | Phase 1 unit tests |
| `docs/RUNTIME_PHASE_1.md` | This note |

## Files modified

| File | Change |
|------|--------|
| `app/main.py` | Wire supervisor register / start_all / shutdown |
| `.gitignore` | Allow `tests/runtime/**` |

## Supervisor architecture

```
main.py
  └── RuntimeSupervisor
        ├── ChildManager(listener)
        ├── ChildManager(energy_logger)   # lock_aware metadata only
        └── ChildManager(loadcontroller_listener)
              └── owns multiprocessing.Process (never exposed)
```

## Descriptor structure

Immutable fields: `name`, `entrypoint`, `args`, `kwargs`, `enabled`, `startup_timeout`, `shutdown_timeout`, `restart_policy` (placeholder `"none"`), `dependencies`, `lock_aware`, `display_name`, `daemon` (Phase 1 default `True` for identical prior behavior).

## ChildManager responsibilities

Construct Process · start · stop · join · `is_alive()` · `exitcode()` · `status()` · record natural exit state. **No restart.**

## Lifecycle states

`CREATED` → `REGISTERED` → `STARTING` → `RUNNING` → `STOPPING` → `STOPPED` | `FAILED`

## Startup flow

Supervisor created → three descriptors registered → `start()` → `start_all()` → scheduler starts (unchanged).

## Shutdown flow

Monitoring teardown (unchanged) → `supervisor.shutdown()` (`stop_all` + mark shut down) → scheduler shutdown.

## Tests

`pytest tests/runtime/test_phase1_supervisor.py`

## Acceptance checklist

- [x] No one-shot Process handles in `main.py`
- [x] Three children registered and started via Supervisor
- [x] Energy logger lock still owned by entrypoint
- [x] Monitoring untouched
- [x] No restart / poll / backoff / policy engine
- [x] Tests cover descriptor, manager, supervisor, natural exit

## Rollback

Revert `app/main.py` to prior Process starts; remove `app/runtime/` if needed.

## Known limitations (Phase 1)

- No automatic restart when a child dies
- No health poll thread
- `daemon=True` retained for behavior parity (design prefers `False` in later phase)
- `restart_policy` / `lock_aware` are metadata only
- Natural exit updates state only when `status` / `is_alive` is observed
