# Runtime Recovery — Phase M2A (Windows Named Mutex)

**Date:** 2026-08-05  
**Status:** Implemented and verified  
**Authority:** `docs/ENERGY_LOGGER_RUNTIME_REDESIGN.md` (mutex fence)  
**Prerequisite:** M1 frozen (`docs/RUNTIME_PHASE_M1_IMPLEMENTATION.md`, soak `docs/RUNTIME_M1_REGRESSION_SOAK_REPORT.md`)  
**Evidence pack:** `docs/_m2a_artifacts/`  
**Unit tests:** `tests/runtime/test_energy_logger_mutex.py` + M1 suite (12/12 passed)

---

## Scope (what M2A is / is not)

| In M2A | Out of scope (later) |
|--------|----------------------|
| Windows named mutex as **authoritative** singleton | M2B orphan adoption / reconcile |
| PID file → **diagnostic only** | Heartbeat identity / runtime authority |
| Acquire before heartbeat, scheduler, DB work | Monitoring changes |
| Exit **78** on mutex denial | Redesign of Job Object / daemon=False |
| Supervisor classifies **78 → LOCK_BUSY** (already M1) | Auto-adopt of external holders |

**M1 behaviour was not modified** (Job Object, `daemon=False`, exit classification rules).

---

## Architecture

```
energy_logger_process_entrypoint()
  │
  ├─ 1) acquire_energy_logger_mutex()
  │       CreateMutexW("Global\\Lutron.EnergyLogger.<install_id>")
  │       WaitForSingleObject(handle, 0)
  │       fail → sys.exit(78)   # no heartbeat, no scheduler, no DB
  │       success → hold HANDLE for process lifetime
  │
  ├─ 2) write_diagnostic_pid_file()   # never gates ownership
  │
  ├─ 3) start_daemon_heartbeat("energy_logger")
  │
  └─ 4) asyncio.run(run_scheduler())

Process death (exit / taskkill / Job Object kill)
  └─ OS closes mutex handle → ownership released automatically
     (no ReleaseMutex / CloseHandle required in app code)
```

**Mutex name (normative, from redesign):**

`Global\Lutron.EnergyLogger.<install_id>`

- `install_id` = `LUTRON_INSTALL_ID` env, default `"default"`
- Validated live as `Global\Lutron.EnergyLogger.default`

**PID file:** `%TEMP%\energy_logger.lock` — informational (`pid`, `runtime_id` placeholder, `timestamp`, note). Corrupt/missing/renamed must not block startup.

---

## Files modified

| File | Change |
|------|--------|
| `app/runtime/energy_logger_mutex.py` | **NEW** — CreateMutexW acquire, process-lifetime hold, install_id naming |
| `app/energy_logger.py` | Replace PID-file ownership with mutex-first entrypoint; diagnostic PID helpers |
| `app/runtime/exit_codes.py` | Comment update (78 = mutex denial; constant unchanged) |
| `app/runtime/__init__.py` | Export mutex helpers |
| `tests/runtime/test_energy_logger_mutex.py` | **NEW** — name, denial while held, taskkill release |
| `docs/_m2a_artifacts/m2a_live_validation.py` | Live Tests A–F harness |

**Not changed:** `job_object.py`, `child_manager.py` spawn/Job assign, Monitoring ingest/heartbeat identity, supervisor adopt logic.

---

## Mutex lifecycle

1. **Create/Open** — `CreateMutexW(NULL, bInitialOwner=FALSE, name)` after `SetLastError(0)`.
2. **Acquire** — `WaitForSingleObject(handle, 0)`:
   - `WAIT_OBJECT_0` / `WAIT_ABANDONED` → own singleton; keep handle in module global.
   - `WAIT_TIMEOUT` → close local handle, raise `MutexAcquireError` → exit **78**.
3. **Hold** — handle retained so GC cannot close it before process exit.
4. **Release** — OS on process termination (including `taskkill /F` and Job Object kill-on-close). Application does **not** call `ReleaseMutex` in the graceful-exit path.

---

## Validation summary

| Test | Result | Evidence |
|------|--------|----------|
| **A** Normal startup — mutex + scheduler + HB path | **PASS** | `A_supervisor.json`, `A_mutex.json`, `A_lock_file.json`, `uvicorn_A.out.log` |
| **B** Second energy_logger → exit 78 | **PASS** | `B_second_instance.json` rc=78 |
| **C** Kill first → mutex immediately acquirable | **PASS** | `C_challenger_attempts.json` `ACQUIRED` attempt 0 |
| **D** API restart ×20 — mutex released each time | **PASS** 20/20 | `D_all.json`, `D_cycle_*.json` |
| **E** `taskkill /F` ×20 — mutex released, restart OK | **PASS** 20/20 | `E_all.json`, `E_cycle_*.json` |
| **F** Corrupt / delete / rename PID file | **PASS** 3/3 | `F_cases.json` |
| Supervisor exit 78 → `LOCK_BUSY` (no adopt) | **PASS** | `LB_supervisor_snapshots.json` / report `LOCK_BUSY_supervisor` |

**Overall:** `phase_m2a_report.json` → `"overall_pass": true`, `"failures": []`  
**Harness runtime:** ~10.6 minutes (`2026-08-05T06:36:06Z` → `06:46:17Z`)  
**Unit tests:** 12 passed (`test_energy_logger_mutex` + `test_job_object_runtime`)

---

## Raw Win32 evidence

### Mutex present while logger running (Test A)

`OpenMutexW(SYNCHRONIZE, False, "Global\\Lutron.EnergyLogger.default")`:

```json
{
  "name": "Global\\Lutron.EnergyLogger.default",
  "opened": true,
  "last_error": 0
}
```

Source: `docs/_m2a_artifacts/A_mutex.json`

### Mutex gone after API/taskkill (Test D cycle 0 — between kill and restart)

```json
{
  "name": "Global\\Lutron.EnergyLogger.default",
  "opened": false,
  "last_error": 2
}
```

`last_error: 2` = `ERROR_FILE_NOT_FOUND` (named object destroyed when last handle closed).

Source: `docs/_m2a_artifacts/D_cycle_0.json` → `mutex_between_kill_and_start`

### Same after taskkill /F (Test E cycle 0)

```json
{
  "opened": false,
  "last_error": 2
}
```

Source: `docs/_m2a_artifacts/E_cycle_0.json` → `leftover_after_kill.mutex`

### Second acquire denied (Test B stderr)

```
[mutex] acquire denied name=Global\Lutron.EnergyLogger.default already_exists=True wait=TIMEOUT
```

Process exit code **78**. Source: `B_second_instance.json`

### Immediate reclaim after kill (Test C)

```
ACQUIRED Global\Lutron.EnergyLogger.default
```

attempt 0, rc=0. Source: `C_challenger_attempts.json`

---

## Raw API evidence

### Test A — supervisor (energy_logger RUNNING, job_active)

From `A_supervisor.json`:

- `supervisor.job_active`: **true**
- `energy_logger.state`: **RUNNING**, `pid`: **17444**, `lock_aware`: **true**
- listener + loadcontroller_listener **RUNNING**

### Test A — diagnostic PID file (non-authoritative)

```
pid=17444
runtime_id=
timestamp=2026-08-05T12:06:13.433135
note=diagnostic_only_mutex_is_authoritative
```

### Test A — stdout

```
[Energy Logger] Mutex acquired (Global\Lutron.EnergyLogger.default)
[Energy Logger] Process started successfully (PID: 17444)
```

(`uvicorn_A.out.log`)

### Test B — denial message

```
[Energy Logger] Cannot start — mutex busy (Global\Lutron.EnergyLogger.default). Exiting 78.
```

returncode **78**; primary energy_logger remained RUNNING.

### Test F — corrupt / delete / rename

All three cases restarted cleanly with mutex held and `energy_logger` RUNNING (`F_cases.json`). PID file never gated ownership.

---

## Raw supervisor state (LOCK_BUSY, no reconciliation)

External process held the mutex after energy_logger was killed; supervisor respawn exited **78**.

From `phase_m2a_report.json` → `LOCK_BUSY_supervisor.last_el`:

```json
{
  "name": "energy_logger",
  "state": "STOPPED",
  "pid": 10248,
  "exitcode": 78,
  "alive": false,
  "error": "lock_busy",
  "lock_aware": true,
  "exit_class": "lock_busy"
}
```

- Classified **LOCK_BUSY** (explicit 78 only — M1 rule preserved).
- **No orphan adoption / reconcile** (M2B). Child stayed STOPPED under `on_failure` until API bounce for subsequent soak cycles.
- Monitoring endpoints / heartbeat identity **unchanged**.

---

## Entrypoint order guarantee

Normative order in `energy_logger_process_entrypoint()`:

1. Mutex  
2. Diagnostic PID file  
3. Heartbeat  
4. Scheduler  

Mutex failure returns before steps 2–4 (no DB/scheduler/heartbeat on denial). Confirmed by Test B: exit 78 with denial log and no long-running second scheduler.

---

## Remaining work — M2B (not started)

1. **Orphan / external holder adoption** — supervisor reconcile when mutex is held outside tracked child.
2. **Startup reconcile** — detect and decide ownership before spawn storms.
3. **Operator / status surfaces** for mutex holder diagnostics (optional).
4. **Do not** start M3 heartbeat identity or Monitoring authority changes until M2B is specified.

---

## Explicit non-goals (stop after M2A)

- No heartbeat identity  
- No orphan adoption  
- No Monitoring changes  
- No M1 Job Object / daemon / classification rewrites  

**M2A complete.**
