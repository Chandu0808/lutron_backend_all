"""
Windows Service deployment — Lutron LMS Backend (Phase 5)

Runtime Supervisor recovers **child** processes (listener, energy logger, LC).
Windows Service Manager / NSSM recovers the **API** (uvicorn) process.

Do not use RuntimeSupervisor to restart uvicorn.
"""

## Prerequisites

- Windows Server / Windows 10+
- Administrator PowerShell
- [NSSM](https://nssm.cc/download) on PATH (or pass `-NssmPath`)
- Backend virtualenv with dependencies installed
- Certificates present (existing LMS requirement)

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `RUNTIME_WINDOWS_SERVICE_ENABLED` | `false` | Packaging intent / optional session-0 heuristic |
| `RUNTIME_RUNNING_AS_SERVICE` | unset | Set by install script → `ServiceIntegration.running_as_service()` |
| `RUNTIME_WINDOWS_SERVICE_NAME` | `LutronLMSBackend` | Display / event payload |

Console mode (today): leave both unset / false — behavior unchanged.

## Installation

```powershell
cd <repo>\lutron_backend
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_service.ps1
```

Optional parameters:

```powershell
.\scripts\install_windows_service.ps1 `
  -ServiceName "LutronLMSBackend" `
  -BackendPath "D:\lutron_latest\lutron_backend" `
  -PythonExe "D:\lutron_latest\lutron_backend\venv\Scripts\python.exe" `
  -HostAddress "0.0.0.0" `
  -Port 8000 `
  -RestartDelayMs 5000 `
  -NssmPath "C:\tools\nssm\nssm.exe"
```

What the script configures:

1. NSSM service running: `python -m uvicorn app.main:app --host … --port …`
2. `AppDirectory` = backend root
3. Env: `RUNTIME_WINDOWS_SERVICE_ENABLED=true`, `RUNTIME_RUNNING_AS_SERVICE=true`
4. **Automatic start** (`SERVICE_AUTO_START`)
5. **Restart on unexpected exit** (`AppExit Default Restart` + SCM `failure` actions)
6. Stdout/stderr under `logs\service-*.log`

## Recovery settings (API process)

| Layer | Setting | Owner |
|-------|---------|-------|
| NSSM | `AppExit Default Restart`, `AppRestartDelay` | Install script |
| SCM | `sc failure … actions= restart/…` | Install script |
| RuntimeSupervisor | Child restart policies / backoff | Unchanged (Phases 2–3) |

If uvicorn crashes, Windows restarts the API. On startup, Supervisor starts children again.

## Upgrade

1. Stop service: `nssm stop LutronLMSBackend`
2. Deploy new backend code / venv
3. Start service: `nssm start LutronLMSBackend`  
   Or re-run `install_windows_service.ps1` (removes and recreates service)

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_windows_service.ps1
```

## Console vs Service

| Mode | How | API recovery |
|------|-----|--------------|
| Console | `uvicorn` / `LMS_start.ps1` | Manual / launcher only |
| Service | NSSM service | Automatic via Windows |

Graceful stop (both): FastAPI shutdown → `ServiceStopping` → stop monitoring → `RuntimeSupervisor.shutdown()` (monitor + children) → `ServiceStopped`.

## Deployment checklist

- [ ] NSSM installed
- [ ] Run install script as Administrator
- [ ] `Get-Service LutronLMSBackend` → Running
- [ ] `http://localhost:8000/docs` responds
- [ ] Kill python/uvicorn once → service restarts within restart delay
- [ ] Children visible again after API restart (listener / energy / LC)
- [ ] `RUNTIME_RUNNING_AS_SERVICE=true` in service environment
- [ ] Logs under `logs\service-stdout.log` / `service-stderr.log`
- [ ] Do **not** enable Monitoring as a restart actuator

## Rollback

```powershell
.\scripts\uninstall_windows_service.ps1
# Resume console launch via deployment_scripts\LMS_start.ps1
```

Unset `RUNTIME_WINDOWS_SERVICE_ENABLED` / `RUNTIME_RUNNING_AS_SERVICE` for console-only.

## Notes

- Linux systemd is out of scope for Phase 5.
- Do not dual-run LMS_start.ps1 and the Windows service on the same port.
- Frontend (`npm`) is not part of this service; run separately or add a second service if required.
