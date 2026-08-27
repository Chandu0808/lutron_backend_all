# Runtime Recovery Framework — Phase 5

**Status:** Implemented  
**Scope:** Windows Service integration (API process recovery via SCM/NSSM)  
**Reference:** Design + Phases 1–4, [`WINDOWS_SERVICE_DEPLOYMENT.md`](./WINDOWS_SERVICE_DEPLOYMENT.md)

## Summary

Layer 1 (Windows) recovers **uvicorn/API**. Layer 2 (`RuntimeSupervisor`) recovers **children**. `ServiceIntegration` detects console vs service, publishes service lifecycle events, and wires graceful shutdown. Supervisor restart logic and Monitoring are unchanged.

## 1. Files created

| File | Role |
|------|------|
| `app/runtime/service_integration.py` | Service abstraction |
| `scripts/install_windows_service.ps1` | NSSM install + auto-restart |
| `scripts/uninstall_windows_service.ps1` | Remove service |
| `docs/WINDOWS_SERVICE_DEPLOYMENT.md` | Ops guide |
| `tests/runtime/test_phase5_service.py` | Phase 5 tests |
| `docs/RUNTIME_PHASE_5.md` | This note |

## 2. Files modified

| File | Change |
|------|--------|
| `app/runtime/events.py` | `ServiceStarted` / `ServiceStopping` / `ServiceStopped` |
| `app/runtime/__init__.py` | Exports |
| `app/main.py` | `prepare_startup` / `prepare_shutdown` / `finalize_shutdown` |

## 3. Service architecture

```
Windows Service Manager / NSSM
  └── uvicorn app.main:app          ← restarted by Windows on crash
        └── RuntimeSupervisor       ← unchanged restart policies
              ├── listener
              ├── energy_logger
              └── loadcontroller_listener
```

## 4. Startup flow

`prepare_startup` → config/DB → Monitoring (optional) → `Supervisor.start` → children → `HealthMonitor` → API ready

## 5. Shutdown flow

`prepare_shutdown` (ServiceStopping) → Monitoring stop → `Supervisor.shutdown` (monitor + children) → scheduler → `finalize_shutdown` (ServiceStopped)

Handles FastAPI shutdown for CTRL+C / SIGTERM / service stop.

## 6. Windows Service deployment

See `docs/WINDOWS_SERVICE_DEPLOYMENT.md`. Install script sets auto-start + AppExit Restart + SCM failure actions.

## 7. Tests

Detection, event order, console vs service, no supervisor coupling in service module.

## 8. Acceptance checklist

- [x] Console unchanged when flags off  
- [x] Service detection via env  
- [x] Service lifecycle events on bus  
- [x] No RuntimeSupervisor restart logic changes  
- [x] No Monitoring changes  
- [x] No systemd  

## 9. Rollback

Uninstall service; run console launcher; unset service env vars.

## 10. Known limitations

- Requires NSSM for scripted install  
- Session-0 heuristic only when feature flag enabled  
- Frontend not packaged in this service  
- Hard `taskkill` may skip graceful FastAPI shutdown  
