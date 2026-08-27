# Runtime Recovery Framework — Architecture Design

**Status:** DESIGN ONLY — no implementation  
**Reference:** [`RUNTIME_RECOVERY_ARCHITECTURE_AUDIT.md`](./RUNTIME_RECOVERY_ARCHITECTURE_AUDIT.md)  
**Scope:** Layer 2 — Parent Child Supervisor (inside the API / uvicorn process)  
**Out of scope for this document:** Layer 1 OS service packaging (NSSM/WinSW), Monitoring Platform changes as actuators

---

## 1. Architecture overview

### 1.1 Purpose

Introduce a **new subsystem** `app/runtime/` that owns the lifecycle of multiprocessing child daemons started by the API process:

- Listener  
- Energy Logger  
- LoadController Listener  
- Future runtime daemons registered via descriptors  

It detects unexpected exits, applies restart policies with backoff, supports graceful shutdown, and never restarts uvicorn itself.

### 1.2 Hard boundaries

| Concern | Owner |
|---------|--------|
| Detect child death / restart children | **Runtime Supervisor** |
| Restart uvicorn / host process | **OS (Layer 1)** — never Runtime |
| Detect stale heartbeats / raise alerts | **Monitoring** — detection only |
| Trigger restarts from Alert Engine | **Forbidden** |
| LEAP TCP reconnect inside Listener | **Listener domain logic** — not Runtime |

### 1.3 Placement

```
┌─────────────────────────────────────────────────────────┐
│  uvicorn  (OS process — NOT supervised by Runtime)      │
│                                                         │
│  FastAPI startup/shutdown  ──wires──►  RuntimeSupervisor │
│                                                         │
│  app/runtime/                                           │
│    Supervisor  ──owns──►  ChildManager × N              │
│         │                      │                        │
│         │                      ├── ProcessDescriptor    │
│         │                      ├── RestartPolicy        │
│         │                      ├── BackoffState         │
│         │                      └── multiprocessing.Process (current) │
│         └── RuntimeEventBus (internal only)             │
│                                                         │
│  Monitoring (optional observer via Instrumentation)     │
│    may *observe* restarts if wired later — never drives │
└─────────────────────────────────────────────────────────┘
```

### 1.4 Design principles

1. **Single owner of Process objects** — Supervisor (via ChildManager); `main.py` must not hold long-lived Process handles after migration.  
2. **One-shot Process rule** — never call `.start()` twice; always construct a **new** `Process` on restart.  
3. **Non-daemon children** — supervised children use `daemon=False` so shutdown is explicit and join/terminate semantics are reliable.  
4. **Exception isolation** — supervisor loop failures must never crash FastAPI.  
5. **Flag gated** — default OFF until explicitly enabled (`RUNTIME_SUPERVISOR_ENABLED`).  
6. **Descriptor-driven** — adding a future daemon = register a descriptor, not fork Supervisor logic.  
7. **Runtime events ≠ Monitoring events** — separate event model; optional later bridge is informational only.

---

## 2. Runtime package layout

```
app/runtime/
    __init__.py              # package exports (future)
    supervisor.py            # orchestrator / poll loop / API for main.py
    child_manager.py         # per-child Process ownership + transitions
    process_descriptor.py    # immutable config for a child type
    restart_policy.py        # Never / Always / OnFailure / LimitedRetries / ManualOnly
    restart_backoff.py       # delay strategies + failure window / cooldown
    lifecycle.py             # start/stop sequencing, graceful terminate protocol
    events.py                # internal RuntimeEvent types + in-process bus
```

**Non-members (intentionally elsewhere):**

- Child entrypoints remain in `app/listener.py`, `app/energy_logger.py`, `app/loadcontroller_listener.py`  
- Energy lock acquisition remains inside Energy Logger entrypoint  
- Monitoring stays in `app/monitoring/`  

**Future (not in this design package):**

- `app/runtime/registry.py` — optional central list of descriptors  
- `app/runtime/config.py` — env parsing helpers  

---

## 3. Module designs

### 3.1 `process_descriptor.py`

**Purpose**  
Immutable specification of one supervisable child type. Configuration-as-data; no Process handles.

**Responsibilities**

- Declare identity, entrypoint, enablement, policy, timeouts, locks, dependencies, priority  
- Provide defaults safe for production  
- Remain serializable / loggable (no callables except entrypoint reference)

**Public interface (conceptual)**

| Member | Meaning |
|--------|---------|
| `name` | Stable id: `listener`, `energy_logger`, `loadcontroller_listener` |
| `display_name` | Human label |
| `entrypoint` | Callable target for `Process(target=...)` |
| `args` / `kwargs` | Optional Process args |
| `enabled` | Static or callable/env-resolved “should supervise” |
| `restart_policy` | Policy enum + parameters (see §5) |
| `startup_timeout_seconds` | Max time in STARTING before HungStartup |
| `shutdown_timeout_seconds` | Join wait before escalate terminate → kill |
| `poll_interval_seconds` | Override global poll for this child (optional) |
| `expected_heartbeat` | Optional metadata for docs/ops only — **Runtime does not consume Monitoring heartbeats as actuators** |
| `lock` | Optional lock awareness: path, ownership rule (`energy_logger.lock`) |
| `dependencies` | Ordered list of other child `name`s that should be RUNNING first |
| `restart_priority` | Lower number = restart first among simultaneous failures |
| `exit_semantics` | How to classify exit codes / “clean idle exit” (see special cases) |
| `spawn_kwargs` | e.g. `daemon=False`, name=… |

**Owned objects**  
None at runtime — pure data. Built once at registration.

**Interactions**  
Read by Supervisor at start; read by ChildManager on every spawn/restart decision; never mutated by poll loop.

**Lifecycle**  
Created at import/registration → immutable for process lifetime (reload = future concern, out of v1).

**Thread safety**  
Immutable after construction → share freely across threads.

**Failure handling**  
Invalid descriptor rejected at registration (fail closed for that child; do not start Supervisor in a half-defined state for unknown names).

---

### 3.2 `restart_policy.py`

**Purpose**  
Decide whether a stopped/failed child may be restarted, and under what constraints.

**Responsibilities**

- Map `(state, exit_classification, attempt_count, policy_params)` → `ALLOW | DENY | MANUAL`  
- Encode policy kinds and parameters  
- Remain pure (no Process I/O)

**Public interface (conceptual)**

- `RestartPolicyKind`: `Never`, `Always`, `OnFailure`, `LimitedRetries`, `ManualOnly`  
- `RestartDecision` with reason string  
- `evaluate(context) -> RestartDecision`  
- Context includes: exit code, signal/abnormal flag, clean-idle flag, consecutive failures, wall-clock window, operator manual-stop flag, supervisor shutting-down flag

**Owned objects**  
Policy parameters only.

**Interactions**  
Called by ChildManager before scheduling restart; Backoff consulted only if decision is ALLOW.

**Lifecycle**  
Bound to descriptor for life of registration.

**Thread safety**  
Pure functions / immutable params.

**Failure handling**  
On unknown exit classification → treat as failure (conservative) unless policy says Never.

#### Policy usage guidance

| Policy | When to use | Default for |
|--------|-------------|-------------|
| **Never** | Child must not auto-respawn (debug, one-shot tools) | — |
| **Always** | Child should run whenever Supervisor is up, including “clean” exits that mean idle-complete | Prefer **not** for Listener if idle-exit is intentional; use with care |
| **OnFailure** | Restart only unexpected/crash exits; honor clean idle / intentional stop | **Listener**, **LoadController** (recommended) |
| **LimitedRetries** | Cap restarts in a failure window; then abandon until cooldown or manual | **Energy Logger** (lock + crash loops); also safe default overlay on OnFailure |
| **ManualOnly** | Operator/API must request start; Supervisor tracks state only | Maintenance windows |

**Recommended defaults (v1):**

- Listener → `OnFailure` + `LimitedRetries` overlay  
- LoadController → same as Listener  
- Energy Logger → `OnFailure` + `LimitedRetries` + lock gate  

---

### 3.3 `restart_backoff.py`

**Purpose**  
Compute delay before next spawn after a restart decision.

**Responsibilities**

- Strategies: fixed, linear, exponential  
- Cap at maximum delay  
- Track failure window, cooldown, retry counter reset  
- Remain pure given input counters + clock

**Public interface (conceptual)**

| Concept | Behavior |
|---------|----------|
| `strategy` | `fixed` \| `linear` \| `exponential` |
| `base_delay_seconds` | First delay / unit step |
| `multiplier` | Exponential factor (e.g. 2.0) |
| `max_delay_seconds` | Cap |
| `max_attempts_in_window` | With LimitedRetries |
| `failure_window_seconds` | Sliding/rolling window for attempt counting |
| `cooldown_seconds` | After abandon: refuse restarts until cooldown elapses |
| `reset_after_stable_seconds` | If child stays RUNNING this long, reset consecutive failure counter |
| `next_delay(attempt_index) -> seconds` | |
| `record_failure(now)` / `record_success_stable(now)` | State updates on BackoffState |

**Owned objects**  
`BackoffState` per child (mutable counters + timestamps).

**Interactions**  
ChildManager owns BackoffState; policy ALLOW → ask backoff for delay → enter BACKOFF → schedule RestartScheduled.

**Lifecycle**  
Reset on stable run; freeze during STOPPING/DISABLED; clear on successful Manual start after abandon (optional).

**Thread safety**  
Mutations only under ChildManager lock (same as state transitions).

**Failure handling**  
Clock skew: use monotonic time for intervals; wall clock only for logging.

---

### 3.4 `child_manager.py`

**Purpose**  
Per-child runtime controller: owns the **current** `multiprocessing.Process` (or none), state machine, and restart scheduling for one descriptor.

**Responsibilities**

- Spawn / stop / restart one child  
- Poll `is_alive()` / exitcode  
- Apply policy + backoff  
- Classify exits (crash vs clean idle vs manual stop)  
- Emit internal RuntimeEvents  
- Enforce lock awareness **before** spawn (advisory check; definitive lock still owned by Energy Logger entrypoint)

**Public interface (conceptual)**

- `start()` / `stop(reason)` / `restart(reason)`  
- `tick(now)` — poll + advance state machine (called by Supervisor)  
- `status() -> ChildStatusSnapshot`  
- `request_manual_start()` / `request_manual_stop()`  
- `apply_enabled(bool)`  

**Owned objects**

- `ProcessDescriptor` (reference)  
- Current `multiprocessing.Process | None`  
- `ChildState`  
- `BackoffState`  
- Last exit code, last error, consecutive failures  
- Restart generation counter (monotonic id per spawn)

**Interactions**

- Supervisor calls `tick` / lifecycle methods  
- Uses `lifecycle` helpers for terminate protocol  
- Uses policy + backoff  
- Publishes to `events` bus  

**Lifecycle**  
CREATED → (start) STARTING → RUNNING → … → STOPPED/FAILED/DISABLED  

**Thread safety**  
One lock per ChildManager; Supervisor may call from poll thread only (preferred single-threaded tick model — see Supervisor).

**Failure handling**

- Spawn exception → FAILED + policy evaluate  
- Double-start guard  
- Never reuse dead Process; set handle to None after join/reap  

#### Process ownership answer

**Yes — ChildManager owns Process objects exclusively.**

Recreation rule:

1. Reap previous Process (`join` with timeout if needed; clear reference).  
2. Construct **new** `Process(target=entrypoint, name=..., daemon=False, ...)`.  
3. `.start()` once.  
4. Store handle + generation id.  
5. On death: read `exitcode`, clear handle, transition, decide restart.

---

### 3.5 `supervisor.py`

**Purpose**  
Process-wide orchestrator: registers children, starts/stops in dependency + priority order, runs health poll loop, exposes status API for diagnostics.

**Responsibilities**

- Hold registry of ChildManagers  
- Startup sequence / shutdown sequence  
- Periodic health poll (`tick` all children)  
- Coordinate dependency-aware start order  
- Coordinate shutdown reverse order  
- Never supervise uvicorn / self  
- Exception-isolate the poll loop  

**Public interface (conceptual)**

| Method | Role |
|--------|------|
| `register(descriptor)` | Add child type |
| `start_all()` | Ordered start |
| `shutdown_all()` | Graceful reverse stop; disable auto-restart |
| `spawn(name)` | Manual/forced start one |
| `stop(name)` | Manual stop one (Manual stop flag) |
| `restart(name)` | Operator restart |
| `remove(name)` | Unregister (stop first); rare |
| `status()` / `status(name)` | Snapshots |
| `tick_once()` | Test/harness |
| `running` | Supervisor loop alive |

**Owned objects**

- Map `name -> ChildManager`  
- Poll thread (or asyncio task — **recommend dedicated daemon thread** for sync Process API)  
- Global stop event  
- Optional RuntimeEventBus  

**Does Supervisor own Process objects?**  
**No directly.** It owns ChildManagers; ChildManagers own Process handles. Supervisor must not call `Process.start` itself.

**Interactions**

- `main.py` lifespan: create Supervisor → register descriptors → `start_all()`  
- Shutdown hook: `shutdown_all()` before or instead of today’s manual terminate block  
- Does **not** import Monitoring actuators  

**Lifecycle**

1. Construct (empty)  
2. Register descriptors  
3. `start_all` + start poll thread  
4. Steady-state ticks  
5. `shutdown_all` + join poll thread  

**Thread safety**

- Prefer **single poll thread** that alone calls `ChildManager.tick`  
- Public `stop`/`restart` from FastAPI thread enqueue commands to poll thread (command queue) to avoid lock inversion  

**Failure handling**

- Tick errors logged per child; continue others  
- Supervisor thread death: log critical; **do not** restart uvicorn; optional self-rearm of poll thread once (careful) — v1: log + DISABLED mode flag in status  

---

### 3.6 `lifecycle.py`

**Purpose**  
Shared protocols for graceful child shutdown and startup readiness.

**Responsibilities**

- Startup: spawn → wait until alive (and optional ready probe) within `startup_timeout`  
- Shutdown: signal → join(`shutdown_timeout`) → terminate → join → kill escalate (platform-specific)  
- Define “manual stop” vs “supervisor shutdown” reasons for exit classification  

**Public interface (conceptual)**

- `spawn_process(descriptor) -> Process`  
- `wait_until_alive(process, timeout)`  
- `graceful_stop(process, timeouts, reason)`  
- `reap(process)`  

**Owned objects**  
None persistent.

**Interactions**  
Used only by ChildManager.

**Lifecycle**  
Stateless helpers.

**Thread safety**  
Caller holds ChildManager lock or runs on poll thread.

**Failure handling**  
Escalate stop levels; never block forever; return structured StopResult.

**Graceful protocol (v1 recommendation)**

Windows + multiprocessing: children today mainly handle `KeyboardInterrupt`; `Process.terminate()` is the practical stop. Design for:

1. Soft request channel (future: Event/Queue) — **v1 may skip** if entrypoints lack cooperative shutdown  
2. `terminate()`  
3. `join(shutdown_timeout)`  
4. Escalate `kill()` if available / still alive  

Document that improving cooperative shutdown inside entrypoints is a **follow-on**, not a Runtime blocker.

---

### 3.7 `events.py`

**Purpose**  
Internal runtime event model and in-process fan-out for logging, diagnostics, and **optional** future informational hooks.

**Responsibilities**

- Define event types and payloads  
- Provide synchronous bus (handlers registered by Supervisor)  
- Guarantee emit never raises into ChildManager (wrap handlers)

**Public interface (conceptual)**

Event types:

| Event | When |
|-------|------|
| `ChildRegistered` | Descriptor accepted |
| `ChildStarted` | Process started and alive |
| `ChildReady` | Optional — passed startup timeout gate |
| `ChildExited` | Process not alive; includes exitcode |
| `ChildFailed` | Failed to start / hung startup / kill escalate |
| `RestartScheduled` | Policy ALLOW + delay chosen |
| `RestartSucceeded` | New generation RUNNING |
| `RestartSkipped` | Policy DENY with reason |
| `RestartAbandoned` | LimitedRetries exhausted / cooldown |
| `BackoffStarted` | Entering BACKOFF |
| `ManualStopRequested` | Operator stop |
| `SupervisorStarted` / `SupervisorShutdown` | Framework lifecycle |

**Owned objects**  
Handler list; optional ring buffer of recent events for `status()`.

**Interactions**  
Producers: ChildManager, Supervisor. Consumers: structured logger; **not** Alert Engine. Future: optional Instrumentation emit is a separate adapter behind a flag — still not an actuator.

**Lifecycle**  
Bus lives with Supervisor.

**Thread safety**  
Copy handler list under lock; invoke outside lock; isolate exceptions.

**Failure handling**  
Handler failure must not change child state decisions (already committed).

---

## 4. Process descriptor design (detail)

### 4.1 Fields (full)

| Field | Type (conceptual) | Notes |
|-------|-------------------|-------|
| `name` | string | Primary key |
| `display_name` | string | |
| `entrypoint` | callable | Module-level function |
| `args` | tuple | Default `()` |
| `kwargs` | dict | Default `{}` |
| `enabled` | bool \| env key | Gate per child |
| `restart_policy` | policy + params | |
| `startup_timeout_seconds` | float | e.g. 30 |
| `shutdown_timeout_seconds` | float | e.g. 5–15 |
| `poll_interval_seconds` | float \| null | Inherit supervisor default |
| `heartbeat` | optional metadata | Expectation docs only in v1 |
| `lock` | `{ path, mode: "exclusive_pidfile" }` \| null | Energy Logger |
| `dependencies` | list[str] | Start-after |
| `restart_priority` | int | Lower = earlier restart |
| `exit_semantics` | see §7 | Idle / crash classification |
| `process_name` | string | `multiprocessing` name= |
| `daemon` | bool | **Always False** for supervised children |

### 4.2 Suggested v1 descriptors

| name | policy | lock | dependencies | priority | exit_semantics |
|------|--------|------|--------------|----------|----------------|
| `listener` | OnFailure + LimitedRetries | none | [] | 10 | IdleZeroProcessors = **non-failure** (see §7) |
| `loadcontroller_listener` | same | none | [] | 20 | same as listener |
| `energy_logger` | OnFailure + LimitedRetries | `energy_logger.lock` | [] | 30 | LockBusy exit = **non-failure / no thrash** |

---

## 5. Restart policy design (detail)

### 5.1 Evaluation inputs

- `supervisor_shutting_down: bool` → always DENY  
- `manual_stop: bool` → DENY until manual start (unless Always + explicit clear)  
- `disabled: bool` → DENY  
- `exit_class`: `Crash` \| `CleanIdle` \| `LockBusy` \| `SignalTerminated` \| `Unknown`  
- `consecutive_failures` / window counts  

### 5.2 Matrix (simplified)

| Policy | Crash | CleanIdle | LockBusy | ManualStop |
|--------|-------|-----------|----------|------------|
| Never | DENY | DENY | DENY | DENY |
| Always | ALLOW | ALLOW | ALLOW* | DENY |
| OnFailure | ALLOW | DENY | DENY | DENY |
| LimitedRetries | ALLOW if under cap | per base policy | DENY | DENY |
| ManualOnly | DENY | DENY | DENY | DENY |

\* Always + LockBusy is dangerous (spawn storm); **v1 forbids Always on energy_logger**. Treat LockBusy as DENY + schedule long cooldown check.

---

## 6. State machine

### 6.1 States

| State | Meaning |
|-------|---------|
| `CREATED` | Manager exists; no Process |
| `STARTING` | Process constructed + start called; awaiting alive/ready |
| `RUNNING` | Alive; steady state |
| `STOPPING` | Graceful/forced stop in progress |
| `STOPPED` | Not alive; intentional stop or clean idle without restart |
| `FAILED` | Start failed / hung / abnormal terminal without immediate restart |
| `RESTARTING` | Transition shell: about to spawn new generation (brief) |
| `BACKOFF` | Waiting delay before next spawn |
| `DISABLED` | Enabled=false or Supervisor abandoned auto mode for child |

### 6.2 Transitions (canonical)

```
CREATED ──start──► STARTING ──alive──► RUNNING
                      │
                      └──timeout/error──► FAILED ──policy ALLOW──► BACKOFF ──► RESTARTING ──► STARTING
                                          │
                                          └──policy DENY──► STOPPED/DISABLED

RUNNING ──unexpected death──► (classify) ──► FAILED or STOPPED
RUNNING ──stop──► STOPPING ──► STOPPED (manual_stop=true)
RUNNING ──supervisor shutdown──► STOPPING ──► STOPPED

BACKOFF ──timer──► RESTARTING ──► STARTING
BACKOFF ──shutdown/manual stop──► STOPPED

FAILED ──LimitedRetries exhausted──► DISABLED (or STOPPED + RestartAbandoned)
DISABLED ──enable/manual start──► STARTING
```

### 6.3 Invariants

- At most one Process handle non-None in STARTING/RUNNING/STOPPING/RESTARTING.  
- BACKOFF/STOPPED/FAILED/DISABLED/CREATED ⇒ Process handle None (reaped).  
- `RESTARTING` does not keep the old Process.

---

## 7. Special cases

### 7.1 Listener — no processors

**Current behavior (today):** `main_async` returns → process exits.

**Options:**

| Option | Pros | Cons |
|--------|------|------|
| Exit | Simple | Supervisor may thrash if misclassified as Crash |
| Sleep/retry inside Listener | Domain owns idle | Changes Listener semantics |
| Remain idle in-process (event wait) | Stable RUNNING | Requires Listener change |

**Recommendation (one behavior):**

**Remain idle inside the Listener process** when zero handshake processors are found:

- Log clearly  
- Wait on a wake condition (periodic re-query every N minutes, or Event)  
- Stay RUNNING so Supervisor does not see an exit  

Until that Listener change exists, Runtime must treat **exit with known clean-idle classification** as **OnFailure → DENY** (do not restart in a tight loop). Prefer implementing idle-wait in Listener as part of Runtime adoption order (see §13).

**Same recommendation for LoadController** for consistency.

### 7.2 Energy Logger — lock

- Descriptor declares lock path.  
- ChildManager **preflight**: if lock exists and PID alive → do not spawn; emit `RestartSkipped(LockBusy)`; enter long BACKOFF or STOPPED.  
- Entrypoint remains authoritative (`acquire_process_lock`); if race lost, process exits quickly → classify `LockBusy`, do not burn retry budget aggressively.  
- Never start a second Energy Logger to “break” the lock.

### 7.3 LoadController

Mirror Listener: same policy, same idle semantics recommendation, no lock file.

---

## 8. Event model (internal)

Events are **not** Monitoring schema events and **not** written to `monitoring.*` by Runtime.

Suggested payload fields common to all:

- `child_name`  
- `generation`  
- `timestamp`  
- `state_from` / `state_to` (when applicable)  
- `detail` (exitcode, delay, reason)

Consumers (v1): logging only.  
Future optional adapter: map `RestartSucceeded` → Instrumentation lifecycle/metric **informational** — still never Alert-driven restart.

---

## 9. Failure handling scenarios

| Scenario | Detection | Action |
|----------|-----------|--------|
| Listener crash | `is_alive()==False`, exit_class=Crash | OnFailure → BACKOFF → respawn new Process |
| Energy Logger crash | same | same + lock preflight |
| LoadController crash | same | same as Listener |
| Repeated crash loop | consecutive failures / window | LimitedRetries → `RestartAbandoned` → DISABLED until cooldown or manual |
| Hung startup | STARTING beyond startup_timeout | stop process → FAILED → policy |
| Slow shutdown | join timeout | terminate → kill escalate → STOPPED; continue shutting down others |
| Lock already exists (live PID) | preflight | skip spawn; no retry storm |
| Manual stop | API/ops `stop(name)` | STOPPING → STOPPED; `manual_stop`; policy DENY auto |
| Application shutdown | Supervisor `shutdown_all` | set shutting_down; stop reverse priority; DENY all restarts; join poll thread |
| Poll thread exception | isolated | log; continue next tick; child states unchanged |
| Child start raises in parent | spawn failure | FAILED + event; no uvicorn impact |

---

## 10. Configuration proposal

All default **off / conservative**. No implementation now.

| Variable | Purpose | Suggested default |
|----------|---------|-------------------|
| `RUNTIME_SUPERVISOR_ENABLED` | Master gate | `false` |
| `RUNTIME_POLL_INTERVAL_SECONDS` | Health poll period | `2`–`5` |
| `RUNTIME_MAX_RESTARTS` | Default LimitedRetries cap in window | `5` |
| `RUNTIME_FAILURE_WINDOW_SECONDS` | Window for cap | `600` |
| `RUNTIME_BACKOFF` | `fixed` \| `linear` \| `exponential` | `exponential` |
| `RUNTIME_RESTART_DELAY` | Base delay seconds | `2` |
| `RUNTIME_MAX_BACKOFF_SECONDS` | Cap | `60`–`300` |
| `RUNTIME_COOLDOWN_SECONDS` | After abandon | `300` |
| `RUNTIME_STABLE_RESET_SECONDS` | Reset failure count after stable run | `120` |
| `RUNTIME_CHILD_LISTENER_ENABLED` | Per-child gate | `true` when supervisor on |
| `RUNTIME_CHILD_ENERGY_LOGGER_ENABLED` | Per-child | `true` |
| `RUNTIME_CHILD_LOADCONTROLLER_ENABLED` | Per-child | `true` |
| `RUNTIME_SHUTDOWN_TIMEOUT_SECONDS` | Default join | `5` |
| `RUNTIME_STARTUP_TIMEOUT_SECONDS` | Default starting | `30` |

Per-child overrides live on descriptors (code/registry), not necessarily all env vars in v1.

---

## 11. Sequence diagrams

### 11.1 Startup

```
main.on_startup
    → RuntimeSupervisor.register(descriptors)
    → Supervisor.start_all()
        → sort by dependencies + restart_priority
        → for each: ChildManager.start()
            → lifecycle.spawn_process (new Process, daemon=False)
            → STARTING → wait alive → RUNNING
            → emit ChildStarted
    → Supervisor.start_poll_thread()
```

### 11.2 Unexpected crash + restart

```
PollThread.tick
    → ChildManager: RUNNING but not alive
    → emit ChildExited
    → classify Crash
    → policy OnFailure + LimitedRetries → ALLOW
    → backoff.next_delay → BACKOFF
    → emit RestartScheduled
    → (later) RESTARTING → new Process() → start → RUNNING
    → emit RestartSucceeded
    → reset counters after stable window
```

### 11.3 Application shutdown

```
main.on_shutdown
    → Supervisor.shutdown_all()
        → shutting_down=true (deny restarts)
        → stop children reverse priority
        → STOPPING → graceful_stop → STOPPED
        → stop poll thread
    → emit SupervisorShutdown
```

### 11.4 Lock busy (Energy Logger)

```
ChildManager.start/preflight
    → lock PID alive
    → emit RestartSkipped(LockBusy)
    → remain STOPPED or long BACKOFF (no spawn)
```

---

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Restart storm / LEAP reconnect storms | LimitedRetries + exponential backoff + stable reset |
| Duplicate Energy Logger | Lock preflight + entrypoint lock; classify LockBusy |
| `daemon=True` orphans / unclear shutdown | Mandate `daemon=False` for supervised children |
| Hard `taskkill` skips Supervisor shutdown | Layer 1 + improve stop scripts later; lock stale recovery already exists |
| Supervisor bug crashes API | Exception isolation; no shared mutable Process in main |
| Treating Monitoring heartbeats as liveness for restart | **Forbidden in v1** — Process liveness only |
| Listener zero-processor exit loops | Idle-in-process recommendation + CleanIdle ≠ Crash |
| Dependency deadlocks | Keep v1 dependencies empty unless proven need |
| Multiprocessing spawn import cost on Windows | Accept slower restart; backoff absorbs |
| Double supervision if main.py also starts Processes | Migration: Supervisor exclusive owner |

---

## 13. Design decisions (resolved)

| ID | Decision |
|----|----------|
| D1 | Runtime is a **new package** `app/runtime/`, not under `app/monitoring/` |
| D2 | Supervisor **does not** restart uvicorn |
| D3 | ChildManager **owns** Process; recreate on every start/restart |
| D4 | Supervised children use **`daemon=False`** |
| D5 | Liveness = `Process.is_alive()` / exitcode — **not** Monitoring heartbeats |
| D6 | Listener/LC with no processors: **prefer idle-in-process**; until then classify clean exit as non-failure |
| D7 | Energy Logger: respect lock; never break lock |
| D8 | Default policy: **OnFailure + LimitedRetries** |
| D9 | Default backoff: **exponential** with cap + failure window + stable reset |
| D10 | Runtime events are **internal only** |
| D11 | Single poll thread + command queue for cross-thread ops |
| D12 | Feature flag default **OFF** |
| D13 | Future daemons = new descriptors, same Supervisor |

---

## 14. Recommended implementation order

*(Guidance only — do not implement in this phase.)*

1. **Descriptors + policies + backoff** (pure units, no Process)  
2. **Events bus + state machine tests** (simulated Process double)  
3. **ChildManager + lifecycle** against fake Process  
4. **Supervisor** poll loop + start/shutdown ordering  
5. **Wire flag-gated behind `RUNTIME_SUPERVISOR_ENABLED`** in `main.py` (replace direct Process starts when ON)  
6. **Listener/LC idle-when-no-processors** change (reduces false restarts)  
7. **Energy Logger lock preflight** integration  
8. **Ops docs** + status endpoint (optional, later)  
9. **Layer 1 OS service** packaging (separate track)  

---

## 15. Explicit non-goals (this design)

- Restarting uvicorn / FastAPI  
- Using Alert Engine or MonitoringWatchdog as restart triggers  
- Supervising APScheduler threads or Monitoring worker threads  
- Docker/Kubernetes controllers  
- Distributed multi-host supervision  
- Changing LEAP reconnect timing inside Listener as part of Runtime  

---

## 16. Acceptance criteria for a future implementation

- With flag OFF, behavior matches today  
- With flag ON, unexpected child death yields bounded automatic respawn  
- Crash loops abandon cleanly with event `RestartAbandoned`  
- Shutdown never respawns children  
- Energy Logger never double-starts against a live lock  
- No Monitoring Storage writes from Runtime  
- No code path where Runtime kills/restarts the API process  

---

**End of design.** Awaiting approval before any implementation.
