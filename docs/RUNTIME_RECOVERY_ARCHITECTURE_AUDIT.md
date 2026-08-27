# Runtime Recovery Framework — Architecture Audit

**Status:** Investigation only (no implementation)  
**Date:** 2026-07-30  
**Scope:** FastAPI backend, Listener, Energy Logger, LoadController Listener, APScheduler, multiprocessing workers  
**Binding context:** Current LMS Windows deployment + `app/main.py` process model + Monitoring Platform (observational only)

---

## 0. How runtime is managed today

| Manager | Used? | Role today |
|---------|-------|------------|
| systemd | No | — |
| supervisord | No | — |
| Docker | No | — |
| Windows Service (SCM) | No | — |
| NSSM / WinSW | No | — |
| Windows Task Scheduler | Partial | Optional `LutronAutoStart` at **user logon** only — starts LMS; does **not** restart on crash |
| Custom launcher | **Yes** | `Start_LMS.cmd` → `LMS_start.ps1` → `Start-Process python -m uvicorn app.main:app` |
| Manual python / uvicorn | Yes (dev) | Same entry as launcher |

**Conclusion:** Runtime is a **custom Windows launcher + fire-and-forget uvicorn process**. There is **no OS-level restart-on-failure** for the API or its children. Stop uses `taskkill /F /T` on the port-8000 process tree (hard kill; FastAPI shutdown hooks may not run).

---

## 1. Runtime architecture diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  Windows Host                                                       │
│                                                                     │
│  [Optional] Task Scheduler: LutronAutoStart (AtLogOn)               │
│       │ once                                                        │
│       ▼                                                             │
│  LMS_start.ps1 / Start_LMS.cmd                                      │
│       │ Start-Process (Hidden)                                      │
│       │ NO supervision after start                                  │
│       ▼                                                             │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  uvicorn uvicorn  (OS process)  ·  app.main:app                   │  │
│  │  Parent of all LMS backend work                               │  │
│  │                                                               │  │
│  │  Threads / in-process:                                        │  │
│  │    • FastAPI / asyncio event loop                             │  │
│  │    • APScheduler BackgroundScheduler (import-time start)      │  │
│  │    • Job worker threads (backfill, reconciliation, daemon)    │  │
│  │    • MonitoringService worker (flag)                          │  │
│  │    • MonitoringWatchdog thread (flag) — heartbeats ONLY       │  │
│  │    • HTTP metrics flusher thread (flag)                       │  │
│  │                                                               │  │
│  │  multiprocessing.Process (daemon=True) started on startup:    │  │
│  │    ├─ Listener          → asyncio LEAP monitors               │  │
│  │    ├─ Energy Logger     → AsyncIOScheduler + PID lock file    │  │
│  │    └─ LoadController    → asyncio LC monitors                 │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Frontend (npm :3000) — out of backend recovery scope               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Process tree

```
LMS_start.ps1 (exits after launch)
 └── python.exe  uvicorn app.main:app --host … --port 8000     [PARENT / API]
       ├── [thread] uvicorn worker / FastAPI
       ├── [thread] APScheduler BackgroundScheduler
       ├── [thread*] monitoring-watchdog          (optional)
       ├── [thread*] monitoring-service worker    (optional)
       ├── [thread*] http-metrics flusher         (optional)
       ├── [mp.Process daemon] listener_process_entrypoint
       │     ├── [thread*] daemon heartbeat       (optional)
       │     ├── [thread*] leap telemetry bridge  (optional)
       │     └── [asyncio tasks] monitor_processor × N
       ├── [mp.Process daemon] energy_logger_process_entrypoint
       │     ├── [thread*] daemon heartbeat       (optional)
       │     ├── AsyncIOScheduler (log_energy_stats @ 15m)
       │     └── PID file: energy_logger.lock
       └── [mp.Process daemon] loadcontroller_listener_entrypoint
             ├── [thread*] daemon heartbeat       (optional)
             └── [asyncio tasks] monitor_loadcontroller × N
```

\* Threads marked optional are gate-controlled by `MONITORING_*` flags.

**Critical multiprocessing facts (current code):**

1. Children are created once as **module-level** `Process(...)` objects in `main.py`.
2. `daemon=True` — children are tied to parent lifetime; they are **not** independently supervised by the OS.
3. A `multiprocessing.Process` may be `.start()` **only once**. After exit, the same object **cannot** be restarted without constructing a new `Process`.
4. Parent **never polls** `is_alive()` after startup — child death is silent to the API process.
5. Parent shutdown uses `terminate()` + `join(timeout=5)` — no graceful signal protocol inside children beyond `KeyboardInterrupt` handling / `atexit` (energy logger).

---

## 3. Startup order

### Outer (launcher)

1. Optional: stop existing listeners on ports 8000 / 3000 (`taskkill /F /T`)
2. Start backend: `python -m uvicorn app.main:app --host <host> --port 8000`
3. Wait ~10s + HTTP probe (`/`, `/docs`, `/health`)
4. Start frontend `npm start`
5. Optional: register `LutronAutoStart` (logon)

### Inner (`app.main` import + FastAPI startup)

| Step | What | Notes |
|------|------|-------|
| Import | Create FastAPI app; create three `Process` objects (not started) | Certificate files must exist or import fails |
| Import | `from app.scheduler import scheduler, load_all_schedules` | **`scheduler.start()` runs at import** + schedules device_refresh / backfill / occupancy jobs |
| `on_startup` | DB `create_all` + migrations | |
| `on_startup` | Monitoring bootstrap → Service → lifecycle startup → Watchdog → engines (flags) | Observational |
| `on_startup` | `listener_process.start()` | Best-effort try/except |
| `on_startup` | `energy_logger_process.start()` | Best-effort; may exit immediately if lock held |
| `on_startup` | `loadcontroller_listener_process.start()` | Best-effort |
| `on_startup` | `load_all_schedules()` + ensure scheduler running | User QC schedules from DB |

---

## 4. Shutdown order

### Intended (`@app.on_event("shutdown")`)

1. Stop retention / analytics / alert jobs registration  
2. Stop HTTP metrics  
3. Stop MonitoringWatchdog  
4. Emit monitoring lifecycle shutdown  
5. Stop MonitoringService + detach Instrumentation  
6. `listener_process.terminate()` + join(5)  
7. `energy_logger_process.terminate()` + join(5)  
8. `loadcontroller_listener_process.terminate()` + join(5)  
9. `scheduler.shutdown(wait=False)`

### Actual production stop (`LMS_stop.ps1`)

- Discovers PID via port 8000 / command line  
- `taskkill /PID … /F /T` — **force-kills process tree**  
- FastAPI shutdown event is **often skipped**  
- Children die with the tree (or as orphans depending on Windows + daemon semantics); energy logger lock may go stale until next start detects dead PID

---

## 5. Component dossier

### 5.1 FastAPI backend (uvicorn)

| # | Finding |
|---|---------|
| 1 Entry point | `python -m uvicorn app.main:app` (launcher) |
| 2 Startup | Uvicorn loads `app.main` → FastAPI lifespan `on_startup` |
| 3 Shutdown | FastAPI `on_shutdown` **if** graceful; launcher stop is hard-kill |
| 4 Parent | Windows shell / Task Scheduler / user session (not a service) |
| 5 Children | Listener, Energy Logger, LoadController `Process`es + many threads |
| 6 Kind | **OS process** |
| 7 Restart independently? | Yes in principle (relaunch uvicorn); **nothing does it today** |
| 8 Existing restart logic | **None** after crash |
| 9 Watchdog | MonitoringWatchdog emits `api` / `monitoring_pipeline` heartbeats only — **no restart** |
| 10 Exception handling | Startup monitoring wrapped; child starts wrapped; unhandled uvicorn crash = process death |
| 11 Health detection | Launcher HTTP probe at start only; monitoring heartbeats if enabled; no continuous OS health |
| 12 PID ownership | No PID file; identified by port 8000 / command line in stop script |

---

### 5.2 Listener

| # | Finding |
|---|---------|
| 1 Entry point | `listener_process_entrypoint()` ← `multiprocessing.Process` from `main.py` |
| 2 Startup | Optional daemon heartbeat + leap telemetry → `asyncio.run(main_async())` → tasks per handshake processor |
| 3 Shutdown | Parent `terminate()`; entrypoint catches `KeyboardInterrupt`; stops leap/heartbeat in `finally` |
| 4 Parent | uvicorn / API process |
| 5 Children | Threads (monitoring); asyncio tasks (not OS children) |
| 6 Kind | **`multiprocessing.Process` (daemon=True)** |
| 7 Restart independently? | **No today.** Same Process object cannot be restarted; parent does not recreate it. Could be redesigned to restart without bouncing API. |
| 8 Existing restart logic | **Per-processor LEAP reconnect loop** inside `monitor_processor` (`while not shutdown_event`, sleep 5). **Not** process-level restart. |
| 9 Watchdog | Daemon heartbeat → ingest (observational). MonitoringWatchdog does **not** cover listener process liveness restart. |
| 10 Exception handling | Broad try/except in connection path; reconnect on failure. Process exits if DB query fails or **zero processors**. |
| 11 Health detection | Heartbeats + LEAP connectivity/ping telemetry (flags). Stale heartbeat → alert only (Phase 10), not restart. |
| 12 PID ownership | None (no lock file) |

**Failure mode of note:** If no processors have `handshake_status=True`, `main_async` returns and the **Listener process exits permanently** until API restart.

---

### 5.3 Energy Logger

| # | Finding |
|---|---------|
| 1 Entry point | `energy_logger_process_entrypoint()` |
| 2 Startup | Acquire `energy_logger.lock` → heartbeat → `asyncio.run(run_scheduler())` → AsyncIOScheduler + wait forever |
| 3 Shutdown | `terminate` from parent; `atexit` + `finally` release lock (may miss on hard kill) |
| 4 Parent | uvicorn / API process |
| 5 Children | AsyncIOScheduler thread pool / job threads; monitoring heartbeat thread |
| 6 Kind | **`multiprocessing.Process` (daemon=True)** |
| 7 Restart independently? | **No today.** Lock file allows only one instance; parent does not respawn. |
| 8 Existing restart logic | **None** at process level. Jobs are interval-based inside a living process. |
| 9 Watchdog | Daemon heartbeat only |
| 10 Exception handling | Fatal errors logged; process exits; lock released in `finally` |
| 11 Health detection | Heartbeat + job_run instrumentation for `log_energy_stats` |
| 12 PID ownership | **Yes** — `energy_logger.lock` with PID + liveness check (`OpenProcess` / `os.kill`) |

---

### 5.4 LoadController Listener

| # | Finding |
|---|---------|
| 1 Entry point | `loadcontroller_listener_entrypoint()` |
| 2 Startup | Heartbeat → `asyncio.run(main_async())` → gather loadcontroller tasks |
| 3 Shutdown | Parent terminate; KeyboardInterrupt swallowed; heartbeat stop in `finally` |
| 4 Parent | uvicorn / API process |
| 5 Children | asyncio tasks |
| 6 Kind | **`multiprocessing.Process` (daemon=True)** |
| 7 Restart independently? | **No today** (same as Listener) |
| 8 Existing restart logic | Per-processor reconnect loops inside monitor (similar pattern to Listener) |
| 9 Watchdog | Daemon heartbeat only |
| 10 Exception handling | `gather(..., return_exceptions=True)`; early exit if no processors |
| 11 Health detection | Heartbeat / alerts if enabled |
| 12 PID ownership | None |

---

### 5.5 Scheduler (API APScheduler)

| # | Finding |
|---|---------|
| 1 Entry point | Module `app.scheduler` — singleton `BackgroundScheduler` |
| 2 Startup | **`scheduler.start()` at import time**; `on_startup` calls `load_all_schedules()` again |
| 3 Shutdown | `scheduler.shutdown(wait=False)` in FastAPI shutdown |
| 4 Parent | Lives inside API OS process |
| 5 Children | APScheduler executor threads; some jobs spawn **daemon threads** (backfill, reconciliation) |
| 6 Kind | **In-process library + threads** (not an OS process) |
| 7 Restart independently? | No — dies with API. Jobs can be re-added if scheduler object still alive. |
| 8 Existing restart logic | `max_instances=1` on some jobs; locks for reconciliation; **no** process restart |
| 9 Watchdog | Monitoring seeds treat `scheduler` as a component, but there is **no dedicated scheduler heartbeat emitter** separate from API |
| 10 Exception handling | Per-job try/except; monitoring wrappers swallow instrumentation errors |
| 11 Health detection | Indirect (job_run rows, API up) |
| 12 PID ownership | N/A (same PID as API) |

---

### 5.6 APScheduler jobs

| Job | Process home | Kind |
|-----|--------------|------|
| `device_refresh` | API scheduler | Cron thread |
| `daily_data_backfill` | API scheduler → daemon thread | Thread |
| `occupancy_reconciliation` | API scheduler → daemon thread | Thread |
| User / QC schedules | API scheduler | Date/cron jobs |
| `log_energy_stats` | Energy logger AsyncIOScheduler | Separate process |
| `monitoring_alert_engine` | API scheduler (flag) | Interval |
| `monitoring_analytics_rollup` | API scheduler (flag) | Cron |
| `monitoring_retention` | API scheduler (flag) | Cron |

**Restart of a failed job instance:** APScheduler may fire next occurrence depending on misfire settings; **not** a process supervisor. Long-running job threads are fire-and-forget once started.

---

### 5.7 Multiprocessing workers (summary)

| Worker | Created in | `daemon` | Supervised after start? |
|--------|------------|----------|-------------------------|
| Listener | `main.py` | True | No |
| Energy Logger | `main.py` | True | No |
| LoadController | `main.py` | True | No |

There is **no** other multiprocessing worker pool for LMS domain work. Monitoring uses **threads**, not `multiprocessing`.

---

### 5.8 Existing “watchdog” vs recovery

| Mechanism | Restarts processes? | Purpose |
|-----------|---------------------|---------|
| `MonitoringWatchdog` | **No** | Emit API + pipeline heartbeats |
| `daemon_heartbeat` | **No** | Emit remote heartbeats |
| Alert Engine | **No** | Open alerts on stale heartbeat / connectivity / jobs / HTTP |
| LEAP reconnect loops | **Connection only** | Re-open SSL to processor |
| Energy logger lock | **Prevents dual start** | Single-instance guard |
| LMS_start / Task Scheduler | **Start only** | Boot / logon launch |

**Monitoring must remain observational.** It is a detection channel for a future recovery framework, not the recovery actuator.

---

## 6. Failure propagation

### Example A — Listener process dies

```
Listener Process exits (crash / no processors / fatal)
        ↓
Parent uvicorn: Process object shows not alive — BUT nothing polls it
        ↓
Who notices?
  • If monitoring ingest on: heartbeats stop → Alert Engine may open heartbeat_stale
  • Operators / dashboard (human)
  • Parent code: does NOT notice
        ↓
Can it restart itself?  NO
        ↓
Who owns it?  API/uvicorn parent (multiprocessing.Process)
        ↓
Who should restart it?  Parent Child Supervisor (inner recovery) — NOT Watchdog, NOT Scheduler
```

### Example B — uvicorn / API dies

```
uvicorn process exits or is taskkilled
        ↓
daemon children typically die with parent (or orphan briefly)
        ↓
Who notices?
  • Port 8000 closed; frontend API failures
  • Monitoring stops entirely (ingest target gone)
  • Task Scheduler: does NOTHING until next logon
        ↓
Can it restart itself?  NO
        ↓
Who owns it?  OS session / launcher (currently unsupervised)
        ↓
Who should restart it?  OS service manager / service wrapper with Restart=Always
```

### Example C — Energy Logger dies; API stays up

```
Energy logger exits → lock released (if graceful)
        ↓
No parent respawn
        ↓
15-minute energy samples stop; backfill job (API) may still run
        ↓
Who should restart? Parent Child Supervisor (with lock-aware start)
```

### Example D — Single LEAP TCP drop (process still alive)

```
monitor_processor reconnect loop handles it (sleep 5, reconnect)
        ↓
Process-level recovery NOT needed
        ↓
Do not confuse connection recovery with process recovery
```

### Example E — APScheduler job throws

```
Job fails → logged / instrumented
        ↓
Next cron may run (max_instances=1)
        ↓
Does not restart Listener / Energy / API
```

---

## 7. Option analysis (recovery point)

| Option | Fit | Verdict |
|--------|-----|---------|
| **A. OS service manager** | Correct outer boundary for API death; Windows-native (Service / NSSM / WinSW). Missing today. Alone: restarting whole uvicorn for Listener-only death is coarse (LEAP reconnect storms, API downtime). | Necessary but not sufficient |
| **B. Internal Recovery Manager** (new global actor) | Useful name for policy, but if it lives inside API it cannot revive API; if separate process it becomes a mini-supervisor competing with OS. | Do not invent a second OS |
| **C. Existing watchdog** | Telemetry only by design. Using it to kill/restart violates Monitoring architecture freeze. | Reject as actuator |
| **D. Parent multiprocessing launcher** | Already owns children; natural place for `is_alive` + recreate Process; must drop one-shot Process pattern and revisit `daemon=True`. Cannot revive itself. | Necessary inner boundary |
| **E. Scheduler** | Wrong abstraction; dies with API; job misfires ≠ process supervision. | Reject |
| **F. Combination** | Outer OS restarts API tree; inner parent supervisor restarts children. Matches ownership and failure domains. | **Recommend** |

---

## 8. Recommended architecture (ONE)

### Recommendation: **F — Combination**

**Two-layer recovery aligned to today’s ownership model.**

```
┌──────────────────────────────────────────────────────────┐
│  LAYER 1 — Outer recovery (OS)                           │
│  Windows Service / NSSM / WinSW wrapping:                │
│      uvicorn app.main:app  (single worker)               │
│  Policy: Restart=Always, restart delay, failure throttle │
│  Replaces fire-and-forget Start-Process + logon-only task│
└────────────────────────────┬─────────────────────────────┘
                             │ owns / restarts
                             ▼
┌──────────────────────────────────────────────────────────┐
│  LAYER 2 — Inner recovery (Parent Child Supervisor)      │
│  Lives in API process (evolution of main.py launcher)    │
│  Owns: listener, energy_logger, loadcontroller_listener  │
│  Policy per child:                                       │
│    • poll liveness                                       │
│    • recreate Process (not reuse dead Process object)    │
│    • backoff + max restarts                              │
│    • respect energy_logger.lock                          │
│    • treat “clean exit / no processors” as policy case   │
│  Non-goals: revive uvicorn; replace OS; drive from alerts│
└──────────────────────────────────────────────────────────┘
                             │ detects only
                             ▼
┌──────────────────────────────────────────────────────────┐
│  LAYER 0 — Detection (existing Monitoring Platform)      │
│  Heartbeats, alerts, dashboard — observe, do not actuate │
└──────────────────────────────────────────────────────────┘
```

### Why this and not a single pure option

1. **API death and child death are different failure domains.** Only OS can fix API death. Only the parent can fix child death without bouncing LEAP/API.
2. **Current code already encodes parent ownership** of the three daemons — extending that is lower risk than splitting into four Windows services immediately.
3. **Monitoring Watchdog must stay passive** — recovery must not be bolted onto Phase 4–10 telemetry.
4. **Scheduler cannot supervise OS processes** and shares fate with the API.

### Explicit non-recommendations

- Do **not** make Alert Engine restart processes (alert storms, privilege, circular dependency on ingest).
- Do **not** rely on `daemon=True` + hope; supervision needs clear join/terminate/recreate semantics (`daemon=False` for supervised children is the usual design).
- Do **not** treat Task Scheduler AtLogOn as recovery — it is boot convenience only.
- Do **not** implement multi-uvicorn workers without revisiting single monitoring pipeline leader assumption.

### Design constraints for a future implementation (not doing now)

1. Preserve single uvicorn worker (Monitoring schema writer assumption).  
2. Child supervisor must be exception-isolated (never crash API).  
3. Emit monitoring events on supervised restart (informational) via Instrumentation — still no Storage from supervisor.  
4. Hard-kill stop scripts should eventually prefer graceful stop so locks and LEAP sessions clean up.  
5. Listener exit-on-zero-processors needs an explicit policy (idle wait vs exit vs supervisor reopen).  

---

## 9. Verdict

| Question | Answer |
|----------|--------|
| Is there automatic recovery today? | **No** (only LEAP connection reconnect + one-shot job retries) |
| Who is the parent of domain daemons? | **uvicorn / `app.main`** |
| Who should restart daemons? | **Parent Child Supervisor** (Layer 2) |
| Who should restart the API? | **OS service wrapper** (Layer 1) |
| Can Monitoring Watchdog be the recovery engine? | **No** |
| Best architecture | **F — Combination (OS outer + parent multiprocessing inner)** |

**Next step (when approved):** design a detailed Child Supervisor + Windows service packaging ADR — still without implementing until explicitly requested.
