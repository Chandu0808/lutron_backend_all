# Energy Logger — Runtime Recovery Architecture Redesign

**Date:** 2026-08-05  
**Mode:** Architecture / design only — **no code, no implementation**  
**Scope:** Guarantee **exactly one** `energy_logger` and eliminate Supervisor↔Monitoring split-brain after crash, `taskkill`, API restart, reboot, stale PID files, and orphans.

---

## Executive summary

Proven failure is not “Monitoring invents UP.” It is **split ownership**:

| Concern | Who owns it today | What breaks |
|---------|-------------------|-------------|
| Process lifecycle | Runtime Supervisor’s `multiprocessing.Process` | Only knows *its* last Process object |
| Singleton | Child’s PID file (`energy_logger.lock`) | Not an OS lock; orphans keep ownership |
| Health signal | Child’s `daemon_heartbeat` → ingest | Reports producer liveness, not Supervisor ownership |
| Exit class | `exitcode == 1` ⇒ `LOCK_BUSY` if `lock_aware` | Force-kill misclassified; restart denied |

**Recommended architecture (hybrid of C + B + Monitoring identity):**

1. **Runtime Supervisor owns the singleton contract** for `energy_logger` (spawn, adopt, stop, restart, reap).
2. **OS-backed named mutex** (Windows) as a **safety fence**, held open for the life of the process — not a closed PID file.
3. **Children are non-daemon**, joined to a **Windows Job Object** (or equivalent process-tree kill) so API death does not leave orphans.
4. **Exit classification never uses bare `exitcode==1` alone**; Supervisor distinguishes crash / lock-busy / force-kill using spawn outcome + lock probe + expected PID.
5. **Monitoring effective status** for supervised daemons is derived from **Supervisor ownership first**, heartbeat second (with instance identity). UP+STOPPED becomes impossible for the same instance.

This document is written so implementation can proceed without further architectural decisions.

---

## Current architecture (as proven)

```
Windows / Console
 └─ uvicorn (API)  ← Windows Service Manager recovers THIS process (Phase 5)
      ├─ RuntimeSupervisor
      │    └─ ChildManager(energy_logger, lock_aware=True, daemon=True)
      │         └─ multiprocessing.Process → energy_logger_process_entrypoint
      │              ├─ acquire_process_lock()  → write PID to %TEMP%\energy_logger.lock, close file
      │              ├─ start_daemon_heartbeat("energy_logger")  → thread → POST /monitoring/ingest
      │              └─ AsyncIOScheduler(log_energy_stats @ 15m)
      └─ MonitoringWatchdog / MonitoringService  (API-local heartbeats for api/pipeline)
```

**Proven behaviors (do not re-litigate):**

- Singleton intended; enforced only by PID file.
- Force-kill of uvicorn can leave energy_logger alive (orphan PID 19284).
- Orphan keeps heartbeat, scheduler, and lock file content.
- Supervisor status reflects last child Process (e.g. 22824 exited 1 → STOPPED/`lock_busy`).
- Monitoring UP reflects orphan heartbeat producer.
- `exitcode==1` + `lock_aware` ⇒ false `LOCK_BUSY` after `taskkill` of child 7236.

---

## Problems to solve

| ID | Problem | Evidence |
|----|---------|----------|
| P1 | Orphan survives API `taskkill` | Root cause §6–§7; PID 19284 |
| P2 | Split brain: Supervisor STOPPED vs Monitoring UP | Root cause executive answer |
| P3 | False `LOCK_BUSY` on force-killed child | Root cause §A; exitcode 1 collision |
| P4 | True lock conflict after orphan → restart denied forever | Root cause §B; 22824 vs 19284 |
| P5 | Stale PID file after orphan dies | Host 2026-08-05: content 19284, process gone |
| P6 | Heartbeat identity is component name only | Cannot tell orphan vs supervised instance |
| P7 | `daemon=True` does not provide reliable tree teardown under force-kill | Proven on Windows Phase 2 |

**Success criteria for the redesign:**

- At most one energy_logger process running on the host.
- After crash / taskkill / API restart / reboot / stale file / orphan: Supervisor reaches RUNNING with the live instance, or consciously STOPPED with no live producer.
- Monitoring never reports UP for energy_logger when Runtime says STOPPED for the **authoritative** instance (see Q8).
- No reliance on `exitcode==1` alone for lock_busy.

---

## Alternative comparison

### Alternative A — Improve current PID-file approach

**Idea:** Keep PID file; harden validation (PID + create-time + image path), startup reclaim, periodic orphan scan, fix exitcode classification.

| Dimension | Assessment |
|-----------|------------|
| **Advantages** | Smallest code delta; reuses existing lock path; no new OS primitives |
| **Disadvantages** | Still advisory; race between check and write; force-kill still skips atexit; PID reuse risk on Windows if only PID checked |
| **Failure modes** | Orphan still possible if process-tree kill not added; two processes can both pass “stale” checks under race; Monitoring still trusts any heartbeat with component code |
| **Recovery** | Startup: if file PID alive and matches expected image → adopt or kill-then-start; if dead → delete file. Periodic scan can kill unmatched PIDs |
| **Complexity** | Low–medium |
| **Risk** | Medium — leaves singleton as best-effort without kernel help |
| **Migration** | Low |
| **Production suitability** | **Insufficient alone** given proven orphan + split-brain. Acceptable only as a transitional hardening layer under a stronger owner |

---

### Alternative B — Replace PID file with OS-backed lock

**Idea:** Hold a Windows named mutex / file lock (`CreateMutex` / `LockFileEx`) for process lifetime so death auto-releases the kernel object.

| Dimension | Assessment |
|-----------|------------|
| **Advantages** | Kernel releases lock on process death (fixes stale file after crash/kill of **holder**); clear “busy” vs “free” at acquire time |
| **Disadvantages** | Does not by itself stop orphans from **being** the holder; Supervisor can still spawn, fail acquire, mark STOPPED while orphan heartbeats; does not fix Monitoring identity or exitcode classification |
| **Failure modes** | Orphan holds mutex → new child cannot start → same STOPPED+UP if HB continues; abandoned mutex name collisions across installs if name not scoped |
| **Recovery** | New process blocks/fails fast on mutex; must still **find and stop** the holder (needs Supervisor/orphan policy) |
| **Complexity** | Medium |
| **Risk** | Low for lock correctness; medium if treated as full solution |
| **Migration** | Medium (replace acquire/release; keep fallback logging) |
| **Production suitability** | **Necessary safety fence**, not sufficient alone |

---

### Alternative C — Supervisor-owned singleton

**Idea:** Runtime Supervisor is the only authority allowed to start/stop energy_logger. On startup: discover existing instance → adopt or terminate → then spawn. Child does not decide singleton alone.

| Dimension | Assessment |
|-----------|------------|
| **Advantages** | Aligns lifecycle UI/API with actual process; enables adopt-on-restart; can emit consistent runtime events; matches product model (Supervisor already owns listener / loadcontroller / energy_logger) |
| **Disadvantages** | Requires reliable process discovery (command line / job / registry); must coordinate with OS lock; more Supervisor logic |
| **Failure modes** | Mis-identify unrelated python PID; adopt wrong process; if discovery fails, may spawn second instance unless OS lock also present |
| **Recovery** | Startup/reconcile loop: discover → adopt or kill → spawn if missing → never leave “STOPPED” while foreign singleton lives without a decision |
| **Complexity** | Medium–high |
| **Risk** | Medium (discovery correctness) |
| **Migration** | Medium |
| **Production suitability** | **Core of the recommended design** |

---

### Alternative D — Windows Service owns energy_logger independently

**Idea:** Separate Windows Service for energy_logger; API Supervisor no longer parents it. SCM recovers the logger independently of uvicorn.

| Dimension | Assessment |
|-----------|------------|
| **Advantages** | Survives API crash by design; clear OS-level singleton (one service); SCM restart policies mature |
| **Disadvantages** | Diverges from current Phase 5 model (Service recovers **API**; Supervisor recovers **children** — proven in `service_integration.py`); two deployables; Monitoring ingest still needs API up; ops complexity (two services, two configs); local console/dev UX worse |
| **Failure modes** | API down → HB ingest fails even if logger runs (unless logger buffers — not current design); duplicate if both Supervisor and Service start logger during migration |
| **Recovery** | SCM restart on failure; API restart independent |
| **Complexity** | High |
| **Risk** | High organizational/ops risk; conflicts with existing Runtime architecture |
| **Migration** | High |
| **Production suitability** | **Not recommended now** — viable long-term if product wants logger HA independent of API, but not required to fix proven bugs and fights current ownership model |

---

### Alternative E — Supervisor-owned singleton + OS mutex + Job Object + heartbeat identity (recommended)

**Idea:** Combine C (authority) + B (kernel fence) + Windows Job Object / non-daemon children (orphan prevention) + Monitoring instance identity + honest exit classification.

| Dimension | Assessment |
|-----------|------------|
| **Advantages** | Closes P1–P7 with layered defense; matches existing Runtime/Monitoring split of duties; implementable incrementally |
| **Disadvantages** | More moving parts than A alone; Windows-specific Job Object (Linux would use process group — out of scope unless needed) |
| **Failure modes** | Job Object unavailable → fall back to explicit child kill list on shutdown; mutex name must be per-install; adopt logic bugs |
| **Recovery** | See sequence diagrams below |
| **Complexity** | Medium–high (one coherent design) |
| **Risk** | Low–medium if phased |
| **Migration** | Medium, phased (see Migration plan) |
| **Production suitability** | **Recommended** |

---

## Comparison matrix

| Criterion | A PID harden | B OS lock | C Supervisor singleton | D Separate Service | **E Hybrid (rec.)** |
|-----------|--------------|-----------|------------------------|--------------------|---------------------|
| Prevents orphan on API `taskkill` | Weak | No | Partial (if kill-on-shutdown) | N/A (not child) | **Yes (Job Object)** |
| Prevents stale lock after death | Partial | **Yes** | Partial | SCM | **Yes** |
| Fixes false LOCK_BUSY | Needs explicit fix | Needs explicit fix | Needs explicit fix | Different | **Yes (required)** |
| Fixes STOPPED+UP split | No (HB alone) | No | Better | Different topology | **Yes** |
| Fits Phase 5 Service=API | Yes | Yes | **Yes** | **No** | **Yes** |
| Complexity | L | M | M–H | H | M–H |
| Production fit for current product | Poor alone | Incomplete | Strong core | Poor fit now | **Best** |

---

## Recommended architecture

### Ownership model (normative)

| Concern | Owner | Rule |
|---------|-------|------|
| May start energy_logger | **Runtime Supervisor only** | Child must not “win” singleton against Supervisor intent |
| May stop energy_logger | **Runtime Supervisor** (graceful) or Job Object (parent death) | Orphan policy: Supervisor reclaims on next start |
| Kernel singleton fence | **Named mutex** held by running instance | Acquire at child start; release on exit / process death |
| Advisory registry | Optional **instance record** file/DB: `{pid, generation, runtime_id, started_at, mutex_name}` | Written after mutex acquired; Supervisor reads on reconcile |
| Heartbeat emission | **energy_logger process** (keeps ingest path) | Payload must include instance identity |
| Authoritative “is energy_logger running for this API?” | **Runtime Supervisor** | Monitoring **effective** status must not contradict |
| API process recovery | Windows Service Manager (unchanged Phase 5) | Does not own energy_logger service |

### Component diagram

```
Windows Service Manager
 └─ recovers uvicorn/API only

uvicorn / API
 ├─ RuntimeSupervisor  ← SINGLETON AUTHORITY
 │    ├─ Job Object (children)
 │    ├─ ReconcileOrphans(energy_logger)
 │    └─ ChildManager(energy_logger, daemon=False)
 │         └─ Process
 │              ├─ NamedMutex("Global\\Lutron.EnergyLogger.<install_id>")  [held open]
 │              ├─ InstanceRecord {runtime_id, generation, pid, ...}
 │              ├─ Heartbeat thread → ingest (with identity)
 │              └─ AsyncIOScheduler(log_energy_stats)
 └─ MonitoringService
      └─ EffectiveHealth(energy_logger) = f(SupervisorSnapshot, HeartbeatIdentity)
```

### Design principles (justified by evidence)

1. **Supervisor authority** — Split-brain happened because Supervisor tracked Process A while producer was Process B (19284). Authority must include discovery of B.
2. **Kernel fence** — PID file closed after write; orphans and stale files both occurred. Mutex dies with process.
3. **Kill the tree** — `daemon=True` did not prevent orphan under `taskkill`. Job Object / explicit teardown required.
4. **Identity in heartbeat** — Component code alone cannot distinguish orphan vs supervised instance.
5. **Classify exits with context** — `exitcode==1` meant both true lock busy (22824) and force-kill (7236).

---

## Specific design answers (1–10)

### 1. Who owns singleton enforcement — Supervisor or energy_logger?

**Primary owner: Runtime Supervisor.**  
**Secondary fence: OS mutex acquired inside energy_logger at start.**

**Why:** Evidence shows the child can enforce “only one lock holder” while Supervisor still believes it has no running child (STOPPED). Product recovery, restart policy, and `/monitoring/runtime/supervisor` are Supervisor-centric. Child-only enforcement caused P2/P4. Child mutex remains as defense-in-depth against double-spawn races and accidental manual starts.

---

### 2. Should Monitoring trust heartbeat, Supervisor state, or both?

**Both, with a defined precedence for supervised components.**

| Layer | Meaning |
|-------|---------|
| Supervisor snapshot | **Ownership / intent**: RUNNING means “this Runtime generation owns a live child (or adopted instance)” |
| Heartbeat | **Liveness of the owned instance** (and only that instance) |
| Effective status (API read models) | For `energy_logger`, `listener`, `loadcontroller_listener`: |

**Effective health algorithm (normative):**

```
if Supervisor has no owned instance for component:
    if any heartbeat for component with foreign/missing instance_id:
        status = down          # or "orphaned" mapped to down for UI
        detail.source = "runtime_orphan_detected"  # optional
        (do not show UP)
    else:
        status = down / stopped per Supervisor
else:  # Supervisor owns instance_id I
    if heartbeat for I is fresh:
        status = up
    elif heartbeat stale:
        status = down (watchdog_stale) even if Supervisor briefly thinks RUNNING
    if heartbeat arrives for instance_id ≠ I:
        ignore for UP; emit runtime alert / orphan event; trigger reconcile
```

**Ownership model name:** *Runtime-authoritative supervised health; heartbeat-authenticated liveness.*

Unsupervised / no-producer components (scheduler, database, …) unchanged — out of scope except that energy_logger leaves that class.

---

### 3. How should heartbeat identity work?

**Include at least:**

| Field | Purpose |
|-------|---------|
| `component_code` | Existing (`energy_logger`) |
| `runtime_id` | ID of the API/Supervisor generation that spawned or adopted this instance (UUID at Supervisor start) |
| `generation` | ChildManager generation counter (already exists in Runtime) |
| `pid` | OS PID of producer |
| `instance_id` | Stable UUID created at successful mutex acquire (survives within process life) |

**Optional:** `started_at`, `parent_pid`.

**Why not name-only:** Proven orphan posted `source=daemon_heartbeat` with the same component code as a healthy child would. Without identity, Monitoring cannot reject foreign producers.

**Rule:** Ingest accepts the event for telemetry, but **read-model UP** only if `instance_id` matches Supervisor’s owned instance (or explicit adopt updated ownership).

---

### 4. How should Runtime determine LOCK_BUSY vs CRASH vs FORCE_KILL?

**Never use `exitcode==1` alone.**

| Class | Detection (ordered) |
|-------|---------------------|
| **LOCK_BUSY** | Child started, then exited **before** Supervisor observed “running ack”, **and** mutex probe shows **another holder**, **and**/or child stderr/log contains proven lock-fail message / dedicated exit code **≠ generic 1** (e.g. reserved `78` / `EX_CANTCREAT`-style). Prefer: child returns distinct exit code **only** for lock failure; Supervisor also verifies mutex held by other PID. |
| **FORCE_KILL / EXTERNAL_TERMINATION** | Process handle died; exitcode in `{1, None, STATUS_CONTROL_C_EXIT, …}` **without** prior intentional stop; **mutex free** (or held by no one); no competing energy_logger discovered. Treat as **CRASH** for `on_failure` restart policy. |
| **CRASH** | Non-zero exit (or abnormal), not intentional stop, not verified lock busy. |
| **CLEAN / INTENTIONAL** | Existing intentional_stop / exit 0 paths unchanged. |

**Spawn-phase distinction (critical):**

```
spawn → wait short "ready" (mutex held + optional pipe "READY" with instance_id)
  if child exits early AND mutex held by other → LOCK_BUSY (deny auto-restart; run orphan reconcile instead)
  if child exits early AND mutex free → CRASH/FORCE_KILL (allow restart)
  if child ready → RUNNING with owned instance_id
```

This separates proven cases: 7236 force-killed (mutex/lock free or stale) vs 22824 lost to 19284 (holder alive).

---

### 5. How should stale PID files / records be cleaned?

| Artifact | Owner of cleanup | When |
|----------|------------------|------|
| Legacy PID file | Supervisor reconcile + child start | Startup; after failed acquire; when PID dead or image mismatch |
| Instance record | Writer = child after mutex; cleaner = Supervisor | On adopt/stop; if pid dead; on successful new acquire |
| Named mutex | OS | Automatic on process death |

**Who owns cleanup:** **Runtime Supervisor** owns *deciding* cleanup; child owns *releasing* mutex/record on graceful exit. Force-kill relies on OS mutex release + Supervisor deleting stale advisory records.

Deprecate closed PID file as sole mechanism once mutex+record exist; during migration, Supervisor may delete PID file when mutex free and PID dead (matches 2026-08-05 stale `19284` file).

---

### 6. How should orphan processes be detected?

**All three layers (defense in depth):**

| When | Who | How |
|------|-----|-----|
| **API / Supervisor startup** | Runtime | Scan for processes matching energy_logger entrypoint signature / recorded PID; probe mutex; adopt or terminate |
| **Periodically** | Runtime HealthMonitor tick (existing ~2s loop) or slower orphan sweep (e.g. 15–30s) | If STOPPED but mutex held / foreign heartbeat identity → reconcile |
| **On spawn LOCK_BUSY** | Runtime | Immediately discover holder PID from mutex/record/process scan → terminate or adopt per policy |
| **Windows** | Job Object | Prevents most orphans; not a detector — a preventer |

**Adopt vs kill policy (normative):**

- If discovered instance heartbeats with **same install** and healthy → **Adopt** (set owned pid/generation/instance_id; do not spawn second).
- If discovered instance is foreign/corrupt/stuck → **Terminate**, wait for mutex free, then spawn.
- Never leave STOPPED while a live energy_logger exists without Adopt or Kill decision (fixes P4).

---

### 7. Should `daemon=True` remain?

**No for energy_logger (and preferably all Runtime children).**

| Option | Verdict |
|--------|---------|
| Keep `daemon=True` | **Rejected** for singleton children — proven insufficient under API `taskkill` |
| `daemon=False` + Job Object | **Required** — Supervisor owns normal children; Job Object kills tree on parent death |
| `daemon=False` alone | Better for join/terminate, **incomplete** vs force-kill of parent |

Runtime should own **non-daemon** children assigned to a Job Object created at Supervisor start. Shutdown path: intentional stop → terminate/join children → close job.

---

### 8. Should Monitoring ever report UP when Runtime reports STOPPED?

**Default: No**, for supervised components (`energy_logger`, and same class as listener / loadcontroller).

**Exception (only one):** Transient race ≤ one heartbeat interval during **Adopt in progress**, where Supervisor state is briefly STOPPED/RESTARTING but has already decided to adopt instance I and heartbeat for I is fresh. Cap with explicit Supervisor state `ADOPTING` rather than STOPPED, so UI never shows STOPPED+UP.

**Guarantee mechanism:** Effective health algorithm in Q2 — foreign heartbeats cannot produce UP; owned instance required.

---

### 9. Should heartbeat originate inside energy_logger or through Supervisor?

| Option | Pros | Cons |
|--------|------|------|
| **Inside energy_logger (keep)** | Proves worker + ingest path alive; already implemented; works if Supervisor briefly busy | Must carry identity; can lie if orphaned |
| **Only Supervisor** | Matches ownership | Does not prove scheduler/mutex/worker loop; Supervisor UP while child hung |
| **Both** | Supervisor “owned+alive”, child “work loop” | More signals to design |

**Decision:** Keep heartbeat **inside energy_logger**, but:

1. Require identity fields (Q3).  
2. Supervisor also publishes **runtime ownership events** (already partially via runtime bridge).  
3. Effective UP = owned ∧ fresh child heartbeat.

Do **not** move sole heartbeat to Supervisor — that would hide worker-loop death (scheduler stuck) behind a living parent.

---

### 10. How should API restart behave?

**Current (proven bad):**

```
API dies (taskkill) → child survives → lock+HB remain → new API spawn fails → STOPPED + UP
```

**Desired:**

```
API stopping/killed
  → Job Object terminates energy_logger (and siblings)
  → mutex released by OS
  → advisory record stale/cleaned on next start

New API starts
  → Supervisor creates Job Object
  → Reconcile: if unexpected energy_logger still alive → Adopt or Kill
  → Spawn if none
  → Child acquires mutex, registers instance_id, starts HB+scheduler
  → Supervisor RUNNING owns that instance_id
  → Monitoring UP only for that instance
```

**Console Ctrl+C / Service stop:** graceful Supervisor shutdown then Job Object cleanup.  
**Force kill of API:** Job Object still kills children (Windows kills job members when job handle closed with kill-on-close flag — design must set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).

---

## Sequence diagrams

### Startup flow

```
API process start
  → ServiceIntegration.prepare_startup (unchanged)
  → RuntimeSupervisor.start
  → Create Job Object (KILL_ON_JOB_CLOSE)
  → Reconcile energy_logger:
       | mutex free & no matching process → spawn
       | mutex held / process found → Adopt (preferred if healthy) OR Kill then spawn
  → Assign child to Job Object
  → Wait READY(instance_id, pid, generation)
  → Mark owned instance
  → Monitoring effective health may go UP when HB for instance_id arrives
```

### Shutdown flow (graceful)

```
API shutdown / ServiceStopping
  → Supervisor.shutdown
  → intentional_stop energy_logger
  → child releases mutex + record
  → join with timeout
  → if still alive → terminate
  → close Job Object
  → Monitoring: owned instance cleared → effective DOWN (not stale UP)
```

### Crash recovery flow (child crash / taskkill of child)

```
Child dies
  → Supervisor observes process exit
  → Classify with Q4 rules (not exitcode==1 alone)
  → If CRASH/FORCE_KILL → on_failure allow restart
  → Reconcile mutex (should be free)
  → Spawn → READY → new instance_id
  → Old HB identity ignored for UP
```

### API restart / force-kill flow

```
taskkill API
  → Job Object kill-on-close → energy_logger terminated
  → mutex auto-released

New API
  → Startup flow (Reconcile handles any survivor if Job Object failed)
```

### Machine reboot flow

```
All processes gone; mutex gone; PID file may remain stale
  → Startup Reconcile: PID dead → delete advisory/PID file
  → Spawn fresh instance
```

### Orphan encounter flow (Job Object failed / legacy)

```
Supervisor STOPPED or starting
  → Detect mutex held by PID X / HB identity foreign
  → Policy: Adopt X (wire ownership to X) OR Kill X
  → Never remain STOPPED while X lives without decision
  → Effective health follows ownership
```

---

## Risk analysis

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Job Object not created (permissions) | Low | Orphans return | Fallback: explicit child PID kill list on shutdown + aggressive startup reconcile; alert |
| Adopt wrong python process | Low–med | Kill unrelated work | Match command-line/entrypoint token + instance record + mutex |
| Distinct lock exit code unused by old binaries | Med during migration | Misclass | Dual detect: exit code **and** mutex probe |
| Ingest ignores new identity fields | Med | Split brain remains | Gate effective health on identity; reject UP without match |
| Double energy_logger during rolling deploy | Low | Duplicate schedulers | Mutex fence |
| Separate Windows Service (Alt D) accidental dual-start | High if mixed | Duplicate | Do not implement D in this phase |

---

## Migration plan (phased, no ambiguity)

### Phase M0 — Prep (safe, limited behavior change)

1. Add reserved exit code for true lock failure; stop classifying all `1` as LOCK_BUSY (treat unknown 1 as CRASH if mutex free).  
2. On Supervisor startup: if PID file PID dead → delete file (clears proven stale `19284` class).  
3. On LOCK_BUSY: run process discovery once; log holder PID (observability).

### Phase M1 — Orphan prevention

1. Set energy_logger (all Runtime children) `daemon=False`.  
2. Create Job Object with kill-on-close; assign children.  
3. Verify with `taskkill` of API: no surviving energy_logger (acceptance test from Phase 2 kill matrix).

### Phase M2 — Supervisor singleton + OS mutex

1. Replace PID-file-as-sole-lock with named mutex held open.  
2. Implement Reconcile/Adopt/Kill on startup and on LOCK_BUSY.  
3. Keep PID/instance record as advisory.

### Phase M3 — Monitoring consistency

1. Heartbeat detail/body includes `runtime_id`, `generation`, `pid`, `instance_id`.  
2. Effective health algorithm (Q2) in read models for supervised components.  
3. Acceptance: cannot observe Supervisor STOPPED + Monitoring UP for energy_logger under Phase 2 repro steps.

### Phase M4 — Remove legacy

1. Remove reliance on closed PID file.  
2. Remove `_LOCK_BUSY_EXITCODE = 1` coupling.  
3. Document ops runbook (mutex name, reconcile logs).

**Each phase has a go/no-go:** Phase 2-style repro (kill child, kill API, restart API) must not recreate STOPPED+UP.

---

## Rollback plan

| Phase | Rollback |
|-------|----------|
| M0 | Revert exit classification + stale file delete only |
| M1 | Re-enable daemon=True only if Job Object removed; accept known orphan risk (document regression) |
| M2 | Mutex optional behind flag; fall back to PID file with M0 fixes |
| M3 | Feature-flag effective health; raw heartbeat status available for debug |
| M4 | Keep advisory PID file writer behind flag for one release |

Rollback must not require Alternative D. Flags should allow “legacy PID file mode” for emergency only, with known split-brain risk acknowledged.

---

## Acceptance tests (implementation must satisfy)

Derived from proven scenarios — not optional:

1. **Force-kill child:** `taskkill` energy_logger PID → classify not permanent LOCK_BUSY → restart → single RUNNING → HB identity matches Supervisor.  
2. **Force-kill API:** `taskkill` uvicorn → no energy_logger process remains (Job Object).  
3. **API restart:** start API twice in succession → exactly one energy_logger; second start adopts or cleanly replaces.  
4. **Stale PID file:** file with dead PID present → startup succeeds.  
5. **Simulated orphan (test harness):** start energy_logger outside Supervisor → API start Adopts or Kills → never STOPPED+UP.  
6. **True lock busy:** hold mutex in test process → child exits dedicated lock code → Supervisor LOCK_BUSY → reconcile kills holder or adopts → recovers to one instance.  
7. **Reboot:** cold start with leftover files → one instance.  
8. **Monitoring:** Supervisor STOPPED ⇒ effective energy_logger not UP.

---

## Implementation decision log (frozen)

| Decision | Choice |
|----------|--------|
| Architecture | **Alternative E** (Supervisor singleton + OS mutex + Job Object + HB identity) |
| Singleton primary owner | Runtime Supervisor |
| Kernel fence | Windows named mutex, process-lifetime hold |
| Child daemon flag | `False` + Job Object kill-on-close |
| Heartbeat location | Inside energy_logger, with identity |
| Effective health | Runtime-authoritative for supervised components |
| LOCK_BUSY detection | Dedicated exit code + mutex probe; never bare exitcode 1 |
| Alt D separate service | Out of scope for this recovery fix |
| Alt A alone | Rejected as final state |

---

## Document control

| Item | Value |
|------|--------|
| Status | Design approved for implementation kickoff — **no code in this change** |

---

*End of architecture redesign. No code was written or modified.*
