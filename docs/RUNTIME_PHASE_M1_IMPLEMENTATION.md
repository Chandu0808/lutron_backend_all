# Runtime Recovery — Phase M1 Implementation

**Date:** 2026-08-05  
**Status:** Implemented and verified  
**Authority:** `docs/ENERGY_LOGGER_RUNTIME_REDESIGN.md` Phase M1  
**Evidence pack:** `docs/_m1_artifacts/`  
**Unit tests:** `tests/runtime/test_job_object_runtime.py` (8/8 passed)

---

## Architecture implemented

```
RuntimeSupervisor.start()
  └─ create_runtime_job_object()   # Windows Job, KILL_ON_JOB_CLOSE
       └─ assign to every ChildManager

ChildManager._spawn()
  └─ Process(daemon=False)
  └─ job.assign_pid(pid)           # fail → terminate child, start fails
  └─ RUNNING

RuntimeSupervisor.shutdown()
  └─ stop_monitor()
  └─ child.stop() × N              # terminate + join
  └─ job.close()                   # KILL_ON_JOB_CLOSE reaps survivors

API process force-killed (taskkill /F)
  └─ Job handle released by OS
  └─ KILL_ON_JOB_CLOSE terminates all assigned children
```

**Explicitly NOT in M1:** mutex, heartbeat identity, orphan adoption, Monitoring changes, startup reconcile.

---

## Files changed

| File | Change |
|------|--------|
| `app/runtime/job_object.py` | **NEW** — `WindowsJobObject`, `NullJobObject`, `create_runtime_job_object` |
| `app/runtime/exit_codes.py` | **NEW** — `EXPLICIT_LOCK_BUSY_EXITCODE = 78` |
| `app/runtime/child_manager.py` | Job assign on spawn; exit classification; refuse spawn without job |
| `app/runtime/supervisor.py` | Create/own Job; wire to children; shutdown closes job; status exposes `job_active` |
| `app/runtime/process_descriptor.py` | Default `daemon=False` |
| `app/runtime/__init__.py` | Export Job Object / exit code symbols |
| `app/main.py` | Register listener / energy_logger / loadcontroller with `daemon=False` |
| `app/energy_logger.py` | Lock acquire failure → `sys.exit(78)` not `1` |
| `tests/runtime/test_job_object_runtime.py` | **NEW** unit/integration tests |
| `tests/runtime/workers.py` | Picklable workers for spawn tests |

---

## Functions / behaviors changed (why)

### 1. `daemon=False` (`main.py`, `ProcessDescriptor`)

**Why:** Root cause proved `daemon=True` children survive API `taskkill`. Redesign M1 requires non-daemon children plus Job Object tree kill.

### 2. `create_runtime_job_object` / `WindowsJobObject`

**Why:** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is the Windows mechanism that terminates members when the owning process dies or the job handle is closed — directly addresses orphan PID 19284 class failures.

### 3. `ChildManager._spawn` → immediate `job.assign_pid`

**Why:** Redesign: never leave unmanaged children. Assign failure terminates the child and fails start.

### 4. `ChildManager._classify_exit`

**Why:** Proven false `LOCK_BUSY` when `exitcode==1` after `taskkill`. M1 rule:

| Condition | Class |
|-----------|--------|
| intentional stop | `INTENTIONAL_STOP` |
| exitcode `0` | `CLEAN_IDLE` |
| exitcode `78` + `lock_aware` | `LOCK_BUSY` (explicit only) |
| exitcode `1` or other non-zero | **`CRASH`** |
| `None` | `UNKNOWN` |

### 5. `energy_logger` exit `78` on PID-file lock failure

**Why:** Child must *explicitly* report lock failure so true lock-busy remains distinguishable from force-kill. Not a mutex (M2).

### 6. `RuntimeSupervisor.shutdown` order

1. Stop health monitor  
2. `stop()` each child (terminate + join)  
3. `job.close()` (force-kill any survivors via KILL_ON_JOB_CLOSE)

---

## How Job Object handles API death modes

| Mode | Mechanism | Children |
|------|-----------|----------|
| **Graceful** FastAPI shutdown / Service stop | `supervisor.shutdown()` → stop children → `job.close()` | Terminated by stop, then job close |
| **CTRL+C** | Signal → FastAPI shutdown → same as graceful | Same |
| **`taskkill /PID <uvicorn>` (soft)** | May allow cleanup handlers | Prefer graceful path |
| **`taskkill /PID <uvicorn> /F`** | Process killed; Job handle closed by OS | **KILL_ON_JOB_CLOSE** terminates assigned children |
| **Hard crash of API** | Same as force-kill | Job handle released → children killed |

Documented and **verified** with 10× `taskkill /F` cycles (see validation).

---

## Unit test results (raw)

```
tests/runtime/test_job_object_runtime.py::test_create_job_object_platform PASSED
tests/runtime/test_job_object_runtime.py::test_child_assignment_to_job PASSED
tests/runtime/test_job_object_runtime.py::test_spawn_fails_without_job PASSED
tests/runtime/test_job_object_runtime.py::test_graceful_shutdown_terminates_children PASSED
tests/runtime/test_job_object_runtime.py::test_restart_after_crash_exitcode_one PASSED
tests/runtime/test_job_object_runtime.py::test_explicit_lock_busy_classification PASSED
tests/runtime/test_job_object_runtime.py::test_unexpected_parent_termination_kills_children PASSED
tests/runtime/test_job_object_runtime.py::test_descriptor_default_daemon_false PASSED
======================== 8 passed in 0.82s =========================
```

---

## Live validation summary

Harness: `docs/_m1_artifacts/m1_live_validation.py`  
Log: `docs/_m1_artifacts/live_validation.out.log`  
Report: `docs/_m1_artifacts/phase_m1_report.json`

| Test | Result |
|------|--------|
| 1 Normal startup — all children RUNNING | **PASS** |
| 2 Shutdown — zero orphans | **PASS** |
| 3 `taskkill /F` API — children dead | **PASS** (×10) |
| 4 Restart API — children RUNNING | **PASS** (×10) |
| 5 Kill→Restart ×10 | **PASS** |
| 6 Monitoring APIs still 200 | **PASS** |

Overall: `"pass": true`, `"failures": []`

---

## Raw evidence

### Test 1 — Supervisor state (raw)

From `docs/_m1_artifacts/test1_supervisor.json`:

```json
{
  "path": "/monitoring/runtime/supervisor",
  "status": 200,
  "body": {
    "supervisor": {
      "running": true,
      "monitor_running": true,
      "job_active": true,
      "job_name": "LutronRuntimeChildren",
      "children": [
        {
          "name": "listener",
          "state": "RUNNING",
          "pid": 9540,
          "alive": true,
          "generation": 1
        },
        {
          "name": "energy_logger",
          "state": "RUNNING",
          "pid": 712,
          "alive": true,
          "lock_aware": true,
          "generation": 1
        },
        {
          "name": "loadcontroller_listener",
          "state": "RUNNING",
          "pid": 11788,
          "alive": true,
          "generation": 1
        }
      ]
    }
  }
}
```

**Job Object assignment evidence:** `job_active: true` on Supervisor status after startup (job created in `RuntimeSupervisor.start`, children assigned in `_spawn`). Unit test `test_child_assignment_to_job` / `test_unexpected_parent_termination_kills_children` prove AssignProcessToJobObject + KILL_ON_JOB_CLOSE.

### Test 1 — Process tree (raw excerpt)

From `docs/_m1_artifacts/phase_m1_report.json` → `test1_startup.spawn_children`:

```json
[
  {
    "pid": 9540,
    "ppid": 23276,
    "cmd": "\"C:\\Program Files\\Python313\\python.exe\" \"-c\" \"from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=23276, pipe_handle=776)\" \"--multiprocessing-fork\""
  },
  {
    "pid": 712,
    "ppid": 23276,
    "cmd": "\"C:\\Program Files\\Python313\\python.exe\" \"-c\" \"from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=23276, pipe_handle=772)\" \"--multiprocessing-fork\""
  },
  {
    "pid": 11788,
    "ppid": 23276,
    "cmd": "\"C:\\Program Files\\Python313\\python.exe\" \"-c\" \"from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=23276, pipe_handle=608)\" \"--multiprocessing-fork\""
  }
]
```

Parent `23276` = uvicorn worker hosting Supervisor.

### Test 2 — After shutdown (raw)

```json
{
  "child_pids_before": [9540, 712, 11788],
  "orphan_pids_still_alive": [],
  "remaining_python_matching": [],
  "pass": true
}
```

### Test 3 — Kill cycle 0 (raw excerpt)

From `docs/_m1_artifacts/cycle_00.json`:

```json
{
  "uvicorn_killed": [14488, 21808],
  "child_pids_before": [2444, 11404, 5488],
  "kill": [
    {
      "pid": 14488,
      "returncode": 0,
      "stdout": "SUCCESS: The process with PID 14488 has been terminated.\n"
    }
  ],
  "alive_children_after_kill": [],
  "kill_pass": true,
  "restart_ok": true
}
```

Child PID history for cycle 0: listener=2444, energy_logger=11404, loadcontroller=5488 → all dead after `taskkill /F` of uvicorn.

### Test 5 — Ten cycles (raw)

```json
"kill_restart_cycles": [
  {"i": 0, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 1, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 2, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 3, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 4, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 5, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 6, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 7, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 8, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []},
  {"i": 9, "kill_pass": true, "restart_ok": true, "alive_children_after_kill": []}
]
```

### Test 6 — Monitoring regression (raw)

```json
{
  "all_200": true,
  "summary": [
    {"path": "/monitoring/summary", "status": 200},
    {"path": "/monitoring/health", "status": 200},
    {"path": "/monitoring/components", "status": 200},
    {"path": "/monitoring/jobs", "status": 200},
    {"path": "/monitoring/runtime/supervisor", "status": 200},
    {"path": "/monitoring/runtime/events", "status": 200}
  ]
}
```

### Exit classification (unit evidence)

- `test_restart_after_crash_exitcode_one`: exitcode `1` → `ExitClass.CRASH`; `request_restart` allowed.  
- `test_explicit_lock_busy_classification`: exitcode `78` → `ExitClass.LOCK_BUSY`; `request_restart` denied under `on_failure`.

### Startup log (raw)

From `docs/_m1_artifacts/uvicorn_t1.out.log`:

```
[Startup] Listener process started
[Startup] Energy logger process started
[Startup] LoadController listener process started
```

### Validation timeline (raw log)

```
[2026-08-05T05:24:00.880152+00:00] M1 live validation start
[2026-08-05T05:24:13.544892+00:00] TEST1 done running_ok=True
[2026-08-05T05:24:27.746046+00:00] TEST2 pass=True
[2026-08-05T05:24:44.664373+00:00] CYCLE 1 PASS kill+restart
...
[2026-08-05T05:27:53.229199+00:00] CYCLE 10 PASS kill+restart
[2026-08-05T05:27:57.161468+00:00] DONE pass=True failures=0
```

Full per-cycle process lists: `docs/_m1_artifacts/cycle_00.json` … `cycle_09.json`.

---

## Remaining work for M2+

Do **not** implement in this phase (per redesign):

| Phase | Work |
|-------|------|
| **M2** | OS named mutex; replace PID-file-as-sole-lock; Supervisor reconcile/Adopt/Kill |
| **M3** | Heartbeat identity (`runtime_id`, `generation`, `pid`, `instance_id`); effective health = Runtime-authoritative |
| **M4** | Remove legacy PID-file reliance; finalize exit-code coupling cleanup |

M1 eliminates **orphan-on-API-death**. Split-brain via foreign heartbeat while Supervisor STOPPED can still occur if an energy_logger is started **outside** the Job Object (manual / pre-M1 leftover). That is M2 orphan adoption + M3 identity.

---

## Stop

Phase M1 is complete. No mutex implementation follows in this change set.
