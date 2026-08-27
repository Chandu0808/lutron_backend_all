# Runtime Recovery — Phase M2B (Ownership Reconciliation)

**Date:** 2026-08-05  
**Status:** Implemented and verified  
**Authority:** `docs/ENERGY_LOGGER_RUNTIME_REDESIGN.md` (Reconcile / Adopt / Kill)  
**Prerequisites (frozen):** M1 Job Object · M2A named mutex (`docs/RUNTIME_PHASE_M2A_MUTEX.md`)  
**Evidence pack:** `docs/_m2b_artifacts/`  
**Unit tests:** focused runtime suite **28 passed**

---

## Scope

| In M2B | Out of scope |
|--------|----------------|
| Investigate on exit **78** (`LOCK_BUSY`) | Heartbeat / runtime / generation identity (M3) |
| Discover mutex holder candidates (process metadata + diagnostic PID hint) | Changing M2A mutex acquire semantics |
| Terminate unknown holders → wait → spawn | Monitoring health model / effective UP |
| Adopt **only** when ownership proven (Job Object + entrypoint cmdline) | DB schema changes |
| `RECONCILING` / `ADOPTING` states | Permanent `STOPPED` + `lock_busy` without decision |
| Configurable max reconcile attempts | |

**M1 and M2A behaviour unchanged** except: LOCK_BUSY no longer parks forever in `STOPPED`; it enters `RECONCILING`.

---

## Architecture

```
lock_aware child exits 78
  → classify LOCK_BUSY
  → state = RECONCILING   (not permanent STOPPED)
  → HealthMonitor tick → Supervisor._reconcile_ownership()

_reconcile_ownership
  ├─ attempt++  (cap: RUNTIME_RECONCILE_MAX_ATTEMPTS, default 5)
  ├─ probe mutex (OpenMutex read-only — does not acquire)
  ├─ if mutex ABSENT → spawn immediately (holder_gone)
  └─ if mutex PRESENT
       ├─ discover candidates (cmdline tokens + diagnostic PID hint)
       ├─ emit diagnostics (pid, name, cmdline, create_time, ownership_status)
       ├─ proven known Runtime (in Job + energy_logger entrypoint) → ADOPT
       ├─ else UNKNOWN → terminate → wait until mutex absent → spawn
       └─ still held → remain RECONCILING (retry next tick)
                      → exceed max → ABANDONED
```

**Mutex name (unchanged):** `Global\Lutron.EnergyLogger.<install_id>`

**PID file:** still diagnostic only; stale/corrupt content never decides ownership.

---

## Decision tree

```
exit 78?
  no  → existing M1 paths (CRASH / CLEAN / …)
  yes → RECONCILING
         │
         ├─ mutex free? ──yes──► spawn ──► RUNNING
         │
         └─ mutex held
              │
              ├─ collect holder diagnostics
              │
              ├─ proven Runtime child? ──yes──► ADOPTING → RUNNING
              │     (Job assigned_pids ∩ energy_logger entrypoint cmdline)
              │
              └─ unknown / unproven
                    → terminate holder(s)
                    → wait (RUNTIME_RECONCILE_TERMINATE_WAIT_SECONDS)
                    → mutex free? ──yes──► spawn
                                    └─no──► retry / eventually abandon
```

---

## Files modified

| File | Change |
|------|--------|
| `app/runtime/lifecycle.py` | `RECONCILING`, `ADOPTING` |
| `app/runtime/energy_logger_mutex.py` | Read-only `is_mutex_object_present` / `wait_until_mutex_absent` (**no acquire change**) |
| `app/runtime/process_inquiry.py` | **NEW** — process list, diagnostic PID hint, terminate |
| `app/runtime/ownership_reconcile.py` | **NEW** — decision engine + report |
| `app/runtime/child_manager.py` | LOCK_BUSY → `RECONCILING`; reconcile helpers; `adopt_os_pid`; status fields; reset crash counters on successful reconcile |
| `app/runtime/supervisor.py` | Tick runs `_reconcile_ownership` before normal restart policy |
| `app/runtime/events.py` | `ReconcileStarted`, `ReconcileCompleted` |
| `app/runtime/__init__.py` | Exports |
| `tests/runtime/test_ownership_reconcile.py` | **NEW** |
| `tests/runtime/test_phase2_restart.py` | LOCK_BUSY expects reconcile, not permanent STOPPED |
| `tests/runtime/test_phase1_supervisor.py` | Inject `NullJobObject` (M1 job requirement) |
| `docs/_m2b_artifacts/m2b_live_validation.py` | Live Tests A–E |

---

## Mutex lifecycle (unchanged from M2A)

Acquire still happens only in `energy_logger` via `CreateMutexW` + `WaitForSingleObject(0)`.  
Reconcile uses **OpenMutex** probe + process discovery; it never calls `acquire_energy_logger_mutex` in the Supervisor.

---

## Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| `RUNTIME_RECONCILE_MAX_ATTEMPTS` | `5` | Cap reconcile loops before `ABANDONED` |
| `RUNTIME_RECONCILE_WAIT_SECONDS` | `2` | Wait when mutex held but no candidates |
| `RUNTIME_RECONCILE_TERMINATE_WAIT_SECONDS` | `3` | Wait for mutex release after terminate |

---

## Validation summary

| Test | Result | Notes |
|------|--------|-------|
| **A** External holder → diagnose → terminate → spawn | **PASS** | `last_reconcile_decision=terminate_and_spawn`, RUNNING |
| **B** Crash while mutex held → recover | **PASS** | Recovered RUNNING |
| **C** 50 cycles — no permanent LOCK_BUSY | **PASS** 50/50 | `fail_count=0` |
| **D** Stale PID file ignored | **PASS** | `pid=1` corrupt file; still RUNNING |
| **E** Unexpected python holder | **PASS** | Diagnostics + terminate + recover |

**Overall:** `phase_m2b_report.json` → `"overall_pass": true`  
**Harness window:** `2026-08-05T07:26:34Z` → `07:29:54Z` (~3.3 min)  
**Unit tests:** 28 passed (`test_ownership_reconcile` + mutex + job object + focused phase1/2)

---

## Raw evidence

### Supervisor JSON (Test A final)

From `A_supervisor_final.json`:

```json
{
  "name": "energy_logger",
  "state": "RUNNING",
  "pid": 8900,
  "exit_class": "lock_busy",
  "last_reconcile_decision": "terminate_and_spawn",
  "reconcile_attempts": 0
}
```

### Mutex state (Test A)

Before start (external holder):

```json
{ "name": "Global\\Lutron.EnergyLogger.default", "held": true }
```

After reconcile recovery: holder terminated; energy_logger RUNNING (mutex held by clean child).

### Windows process information (Test E)

`E_holder_procs.json` / report `holder_procs_before`:

- `python.exe … docs\_m2b_artifacts\_acquire_energy_logger_mutex_holder.py`
- Discovered via cmdline token `acquire_energy_logger_mutex`
- Ownership status: **unknown** → **terminate** → recover RUNNING

### Decision / recovery timeline (Test C)

50 consecutive cycles with external holder contention; none remained permanently `STOPPED`/`lock_busy` without reconcile.  
Successful reconcile resets crash-loop counters so kill→78→reconcile storms do not falsely `ABANDON`.

### Unit / decision-tree evidence

`tests/runtime/test_ownership_reconcile.py`:

- exit 78 → `RECONCILING` (not `STOPPED`)
- unknown holder → terminate → spawn
- stale diagnostic PID ignored
- Supervisor tick leaves lock_busy path out of permanent `STOPPED`

---

## Known limitations

1. **Mutex owner PID is not queried from the kernel object** (no handle-table walk). Holders are inferred from cmdline tokens + diagnostic PID hint. A process that holds the mutex with an unrelated cmdline may require a wait/retry until the object disappears or an operator kill.
2. **Adopt is rare** under normal Job Object operation — orphans are usually outside the current job → classified **unknown** → terminate + spawn (safe default).
3. **`RECONCILING` may be brief** under a 1s poll — UI may observe `RUNNING` with `last_reconcile_decision` rather than catch `RECONCILING` mid-flight.
4. **No heartbeat identity** — Monitoring UP/DOWN semantics unchanged (M3).
5. **Multiprocessing children** often show `spawn_main` cmdlines; discovery relies on diagnostic PID file and/or scripts that include known tokens (as in live harness).

---

## Remaining work (not started)

- **M3** Heartbeat identity (`runtime_id` / instance id) + Monitoring effective status  
- Stronger kernel-level mutex owner enumeration (optional hardening)  
- Startup-time reconcile before first spawn (partially covered when first child exits 78)

**Stop after M2B.**
