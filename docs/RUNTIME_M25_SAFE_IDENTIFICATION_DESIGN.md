# Runtime M2.5A — Safe Process Identification Design

**Date:** 2026-08-05  
**Type:** Design investigation only — **no implementation**  
**Authority inputs:** `docs/RUNTIME_PHASE_M2B_RECONCILIATION.md`, `docs/ENERGY_LOGGER_RUNTIME_REDESIGN.md`  
**Stance:** Replace unsafe M2B discovery. Do not defend cmdline / diagnostic-PID / historical `assigned_pids` kill paths.

---

## Executive summary

| Question | Answer (documented Windows capability) |
|----------|----------------------------------------|
| Can Win32 reveal the PID that **owns** a named mutex? | **No documented Win32 API.** Ownership is a kernel/debug concern; apps must track identity themselves. |
| Can Job membership be queried live? | **Yes.** `IsProcessInJob` + `QueryInformationJobObject(JobObjectBasicProcessIdList)`. |
| Can Runtime prove “this PID belongs to **this** Supervisor” without heartbeat identity? | **Yes, for processes it assigned to its Job** — using the Supervisor’s **job handle** + live membership (+ spawn-time process handle / create-time to defeat PID reuse). **Not** via mutex owner query. |
| Safest reconcile strategy | **Wait-first for foreign holders; terminate only Job-proven members; never kill from TEMP PID / cmdline heuristics.** |
| Nested Jobs + Service | **Supported on Windows 8 / Server 2012+** (LMS targets). **Not** on Win7-era single-job model. Architecture must detect parent-already-in-job and nest or fail clearly. |
| Mutex ACLs | **Yes — use `SECURITY_ATTRIBUTES` at create.** Documented; recommended for service-created sync objects. |
| `install_id` | **Installer-generated UUID** persisted to registry/config; inject via `LUTRON_INSTALL_ID`. |
| 100% confidence kill of **external** holders | **Not achievable with documented APIs.** Therefore Runtime **must not kill** processes it cannot prove are its Job members. |

**Production recommendation:** Implement **M2.5B** (safe identity + wait-based foreign reconcile + live Job queries + mutex ACL + install_id) **before M3**. Do not ship M2B kill-by-discovery.

---

## Mapping to M2 Production Review P0s

| P0 ID | Finding | How this design closes it |
|-------|---------|---------------------------|
| R1 | Diagnostic PID → terminate arbitrary alive PID | **Forbid** terminate based on TEMP file. File remains diagnostic-only forever. |
| R2 | Broad cmdline `energy_logger` → false terminate | **Remove** cmdline-based kill entirely from reconcile. |
| R3 | Historical `assigned_pids` → false adopt | Replace with **live** `IsProcessInJob` / `JobObjectBasicProcessIdList`; retain spawn **process handle** or `(pid, create_time)`. |
| R4 | Parent already in Job → assign fails (Service) | Detect membership; on Win8+ **nest** Runtime job under parent rules; soak Session 0; clear failure if nesting impossible. |

---

## 1. Can Windows reveal the PID that owns a named mutex?

### Documented Win32 surface

Microsoft Learn [Mutex Objects](https://learn.microsoft.com/en-us/windows/win32/sync/mutex-objects) documents:

- `CreateMutex` / `CreateMutexEx`
- `OpenMutex`
- wait functions / `ReleaseMutex`
- abandoned ownership (`WAIT_ABANDONED`)

It does **not** document any API such as “get owning process/thread of mutex.”

`CreateMutexW` / `OpenMutexW` return a **handle**. Handles do not expose owner PID through documented query APIs.

### What exists outside documented app APIs

- **Debuggers** (e.g. WinDbg `!handle`) can show mutant owner thread for live analysis. That is not a supported application contract.
- **Undocumented** `NtQuerySystemInformation` handle-table walks appear in community samples to find processes with an open handle to a named object. That:
  - is **not** a documented Win32 ownership API,
  - finds **handle holders**, not necessarily the thread that last successfully waited (though for Lutron’s “hold handle for process life” pattern they often coincide),
  - is fragile across Windows versions and privilege levels.

### Explicit conclusion

**Impossible to rely on a documented Win32 API to answer “which PID currently owns `Global\Lutron.EnergyLogger.<id>`.”**

Runtime **must not** design production reconcile around mutex-owner PID discovery from the kernel.

**Feasible alternative (application-level):** the process that acquires the mutex **publishes** identity through a channel Runtime trusts (see §3 / recommended architecture). That is bookkeeping, not a Win32 mutex query.

---

## 2. Can Job Object membership be queried live?

### Yes — documented APIs

| API | Documented role | Source |
|-----|-----------------|--------|
| [`IsProcessInJob`](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob) | `TRUE`/`FALSE` whether a process runs in a **specified** job (`JobHandle`), or in **any** job if `JobHandle` is `NULL` | Microsoft Learn |
| [`QueryInformationJobObject`](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject) with **`JobObjectBasicProcessIdList` (3)** | Returns current process ID list for the job | Microsoft Learn |
| [`JOBOBJECT_BASIC_PROCESS_ID_LIST`](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list) | Structure for that list; if nested, list includes processes in the job **and its child jobs** | Microsoft Learn |

### What Runtime can prove with these APIs

Given Supervisor holds `hJob` created at `RuntimeSupervisor.start()`:

| Claim | Provable? |
|-------|-----------|
| PID *P* is currently associated with **this** Job | **Yes** — `IsProcessInJob(hProcess, hJob, &r)` or membership in `JobObjectBasicProcessIdList` |
| PID *P* is in **some** job | **Yes** — `IsProcessInJob(hProcess, NULL, &r)` (weaker; not “ours”) |
| PID *P* **owns** the named mutex | **No** (see §1) |
| Historical “we once assigned P” | **Not** what Job APIs return; live list replaces append-only `assigned_pids` |

### Requirements / limits

- Process handle needs `PROCESS_QUERY_INFORMATION` or `PROCESS_QUERY_LIMITED_INFORMATION`.
- Job handle needs `JOB_OBJECT_QUERY` for queries.
- List buffer may need resizing when `NumberOfProcessIdsInList < NumberOfAssignedProcesses` (documented).

**Conclusion:** Live Job membership is the **strongest documented** OS proof Runtime can use for “belongs to this Supervisor’s process tree fence.”

---

## 3. Prove “this PID belongs to THIS Supervisor” without heartbeat identity?

### Yes — for Job members

**Definition of proof (M2.5):**

A PID *P* belongs to this Supervisor generation iff **all** of:

1. Supervisor’s `hJob` is valid and owned by this API process.
2. **Live** `IsProcessInJob(OpenProcess(P), hJob) == TRUE`  
   **or** *P* appears in a fresh `JobObjectBasicProcessIdList` for `hJob`.
3. **PID-reuse defense** (pick at least one):
   - Runtime retained an open `HANDLE` to the process from spawn/assign time (strongest), **or**
   - Recorded `(pid, create_time)` at spawn and `GetProcessTimes` / `psutil.create_time` still matches.

Heartbeat / `runtime_id` is **not required** for this claim.

### What cannot be proven without additional identity

| Claim | Without HB identity |
|-------|---------------------|
| Foreign process holding the mutex is “our orphan energy_logger” | **Cannot** prove via documented mutex APIs |
| Arbitrary Python with similar cmdline is ours | **Must not** be treated as proof |
| TEMP diagnostic PID is ours | **Must not** be treated as proof |

### Design for proof without M3

```
Spawn path (authoritative enrollment):
  CreateProcess / multiprocessing start
  → OpenProcess / keep handle H  (or record create_time)
  → AssignProcessToJobObject(hJob, H)
  → ChildManager stores {pid, handle_or_create_time, generation}

Prove later:
  IsProcessInJob(H or re-open pid, hJob) && create_time matches
  → PROVEN_RUNTIME_MEMBER

Not in live job list:
  → NOT our member (even if TEMP file or cmdline says otherwise)
```

Optional **advisory** channel (still not HB identity): child writes `{pid, create_time, job_cookie}` to a private ACL’d file **after** mutex acquire — Supervisor verifies against live Job. Cookie = opaque value Supervisor passed via env at spawn. This is enrollment confirmation, not Monitoring identity.

---

## 4. Safest reconciliation strategy

### Options

| Option | Description |
|--------|-------------|
| **A** Terminate immediately | Kill discovered “holders” (current M2B) |
| **B** Wait only | Poll until mutex object absent; then spawn |
| **C** Operator intervention | Alert; wait for human/SCM action |
| **D** Kernel-backed ownership | Kill/adopt only with documented OS proof |
| **E** Hybrid (recommended) | D for own Job members + B/C for foreign |

### Decision matrix

| Criterion | A Terminate now | B Wait only | C Operator | D Kernel-backed | **E Hybrid** |
|-----------|-----------------|-------------|------------|-----------------|--------------|
| Correctness (right process) | Poor (heuristics) | High (no wrong kill) | High | High for Job members | **High** |
| Safety (no false kill) | Fail | Pass | Pass | Pass | **Pass** |
| Recovery latency | Fast if lucky | Depends on holder death | Slow | Fast for own members | **Good** |
| Complexity | Low | Low | Low | Medium | **Medium** |
| Works when foreign holds mutex | Dangerous | Yes (eventually) | Yes | N/A (can’t prove) | **Wait/alert** |
| Production suitability | **Reject** | Acceptable alone | Ops-heavy alone | Incomplete alone | **Recommended** |

### Recommended policy (normative for M2.5)

```
On exit 78 → RECONCILING

1. Probe mutex present? (OpenMutex — existence only)
   NO  → spawn (holder_gone)
   YES → continue

2. Live Job snapshot:
   members = JobObjectBasicProcessIdList(hJob)
   If any member looks like a stale energy_logger still alive while ChildManager
   thinks child is dead → ADOPT only if IsProcessInJob + create_time/handle proof
   (rare; Job kill-on-close normally prevents this)

3. Foreign holder (mutex present, no proven Job member explaining it):
   DO NOT terminate by PID file / cmdline
   → WAIT up to T1 for mutex absence
   → emit operator alert / runtime event (mutex_busy_foreign)
   → retry bounded times
   → escalate: stay RECONCILING / ABANDON with clear reason
      (optional: configurable “force kill only if allowlist path + IsProcessInJob”
       — default OFF)

4. Never call terminate_pid unless:
   target ∈ live Job membership AND spawn-handle/create_time proof
   (i.e. killing our own supervised process tree member intentionally)
```

**Terminate immediately (A) is rejected** for discovery-based targets.

---

## 5. Windows Service + nested Job Objects

### Documented version split

| OS | Nested jobs | Assign when process already in a job |
|----|-------------|--------------------------------------|
| Windows 7 / Server 2008 R2 and older | **Not supported** — one job per process | Fails with **`ERROR_ACCESS_DENIED`** ([AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)) |
| **Windows 8 / Server 2012+** (Win10/11/Server 2016+) | **Supported** ([Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)) | Allowed if target job is **empty** or in the **existing nested hierarchy**, and **no UI limits** on the job |

### Practical Service implications

- SCM may place the service process in a job, or other system components (historically PCA on older SKUs) may associate jobs.
- If API process is already in a job and Runtime creates a **new empty** job then assigns **child** processes:
  - On Win8+: children can often be assigned to Runtime’s job; nesting rules apply to hierarchy validity.
  - If API itself must also join Runtime’s job, assignment order must follow Microsoft’s nesting construction rules (parent/root first).
- **`KILL_ON_JOB_CLOSE` on Runtime’s job:** closing Runtime’s job handle kills members of **that** job (and nested children per termination rules). Parent SCM job behaviour is separate.
- **Terminal Services:** all processes in a job must be in the **same session** as the job (documented on AssignProcessToJobObject). Session 0 service children must stay in Session 0.

### Recommended architecture for LMS (Win10/11/Server)

```
SCM
 └─ API process (may already be in SCM/compat job)
     └─ Runtime creates unnamed Job J_runtime (KILL_ON_JOB_CLOSE)
         └─ Assign each child to J_runtime
            If Assign fails:
              - Log GetLastError
              - If ERROR_ACCESS_DENIED / nesting violation:
                  detect IsProcessInJob(child), IsProcessInJob(self, NULL)
                  attempt documented nested association OR fail start loudly
              - Do not silently run unmanaged children
```

**Required validation (still outstanding from M1 soak Group K):**

1. Install as Windows Service Session 0.  
2. Confirm child assign succeeds.  
3. `taskkill` / SCM stop ⇒ no orphan energy_logger.  
4. Record whether API was already in a job at start (`IsProcessInJob(GetCurrentProcess(), NULL)`).

**Unsupported / out of support for LMS:** designing for Win7 single-job-only without breakaway strategy.

---

## 6. Mutex ACLs

### Documented behaviour

From [`CreateMutexW`](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw) / Ex:

- `lpMutexAttributes` → `SECURITY_ATTRIBUTES.lpSecurityDescriptor`.
- If `NULL`, default DACL comes from the **creator’s token**.
- Microsoft explicitly notes that when creating from a **service** or impersonating thread, you can **apply a security descriptor at create** or adjust the process default DACL ([same docs](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)).

[Kernel Object Namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces):

- `Global\` = machine-wide across sessions (appropriate for single LMS instance per install).
- `Local\` = per-session.
- `SeCreateGlobalPrivilege` is documented for creating **file-mapping / symbolic link** objects in Global from non-session-0 — **not** stated as required for Global mutex creation.

### Can another user/session interfere?

| Actor | With default DACL | With tight ACL (service SID + LOCAL SYSTEM only) |
|-------|-------------------|--------------------------------------------------|
| Same user / same integrity | Can often open/contend | Denied if not on ACL |
| Other interactive user | Often denied by default token ACLs; not guaranteed for all deployments | Denied unless granted |
| Admin | Can usually override | Can still override (admin) |
| Malicious same-service-account process | Can contend | Still can (same SID) |

**Mutex is an availability fence, not a full security boundary against same-account code.**

### Recommendation

1. Create mutex with **custom SD**: allow `SYNCHRONIZE | MUTEX_MODIFY_STATE` (as needed) only to:
   - service account SID running LMS,
   - `LOCAL SYSTEM` if applicable,
   - optionally Admin for support tools.
2. Deny Everyone / broad Users if service-hosted.
3. Keep `Global\Lutron.EnergyLogger.<install_id>` for cross-session singleton **per install**.
4. Treat remaining same-account DoS as acceptable residual risk; mitigate with unique install_id + ops monitoring on reconcile wait loops.

---

## 7. Multi-install `install_id`

### Options

| Source | Pros | Cons |
|--------|------|------|
| Free-text config / env only | Simple | Operators forget; collisions |
| Machine GUID alone | Stable | **Collides** if two LMS installs on one machine |
| Database | Available after DB up | Circular: logger may need mutex before DB |
| **Installer-generated UUID** | Unique per install; offline | Requires installer/registry discipline |
| Registry (per install key) | Standard Windows pattern | Must choose hive/path carefully |

### Recommendation (one)

**Installer generates a UUID at install time**, persists it under a **per-install registry key** (e.g. `HKLM\SOFTWARE\Lutron\LMS\<InstallName>\InstallId`) and/or `appsettings` / `.env`, and sets:

`LUTRON_INSTALL_ID=<uuid>`

Runtime reads env first, then registry fallback. **Refuse to start energy_logger** (or warn + use explicit override) if still `"default"` on production Service installs.

Mutex name remains: `Global\Lutron.EnergyLogger.<install_id>` (sanitize as today).

---

## 8. PID reuse — design that cannot be fooled

Windows reuses PIDs. Any logic of the form “PID from file == alive ⇒ kill/adopt” is unsafe.

### Defenses (documented / standard)

| Technique | Strength | Notes |
|-----------|----------|-------|
| **Retain process `HANDLE` from spawn** | Strongest | Handle refers to the **specific process object**; after exit, waits/signaled; reuse gets a different object |
| **`(pid, create_time)` pair** | Strong | Verify with `GetProcessTimes` / equivalent before act |
| **Live `IsProcessInJob(hJob)`** | Strong for membership | Reused PID almost never randomly in **our** job |
| PID alone | **Useless** | Forbidden as sole key |
| TEMP diagnostic PID | **Useless** as authority | Diagnostic only |

### Normative rule

```
identity_key = process_handle XOR (pid + create_time) + live_job_membership
never identity_key = pid_from_temp_file
```

---

## 9. False termination — 100% confidence?

### Claim

**Runtime can never kill a process unless ownership confidence is effectively 100%.**

### Achievable with documented APIs?

| Target class | 100% confidence kill? | Basis |
|--------------|----------------------|--------|
| Process Runtime spawned and still holds spawn `HANDLE` | **Yes** | Same process object |
| Process in live `JobObjectBasicProcessIdList` for Supervisor `hJob` + create_time match | **Yes (effective)** | Documented Job membership |
| Process inferred from TEMP PID | **No** | — |
| Process inferred from cmdline token | **No** | — |
| Process inferred as “mutex owner” via Win32 | **No API** | §1 |
| External holder of mutex | **No documented proof it is “ours”** | Must **not** kill |

### Explicit statement

**It is impossible, using only documented Win32 mutex APIs, to reach 100% confidence that an arbitrary external PID is the mutex owner and is safe to kill.**

Therefore the **only** production-safe rule is:

> **Never terminate a process that is not a proven member of this Supervisor’s Job (with PID-reuse defense). Foreign mutex contention ⇒ wait + alert, not kill.**

That satisfies “never false-terminate” by **narrowing the kill set**, not by inventing mutex-owner queries.

---

## Feasible vs impossible approaches

### Impossible / reject for production

| Approach | Why |
|----------|-----|
| Query mutex owner PID via documented Win32 | **API does not exist** |
| Kill by TEMP diagnostic PID | P0 false kill / reuse |
| Kill by broad cmdline substring | P0 false kill |
| Treat historical `assigned_pids` as live membership | P0 false adopt |
| Undocumented handle-table walk as sole authority | Unsupported contract; privilege/version fragile |

### Feasible

| Approach | Role |
|----------|------|
| Live Job membership APIs | Prove Supervisor ownership |
| Spawn-time process handle / create_time | Defeat PID reuse |
| Wait for mutex absence | Safe foreign reconcile |
| Custom mutex security descriptor | Reduce cross-principal contention |
| Installer UUID `install_id` | Multi-install isolation |
| Optional env cookie written by child after acquire | Advisory confirmation (not Monitoring HB identity) |

---

## Recommended architecture (M2.5 target)

```
┌─────────────────────────────────────────────────────────┐
│ API / RuntimeSupervisor                                 │
│  hJob (unnamed) + KILL_ON_JOB_CLOSE                     │
│  ChildManager: {handle|create_time, pid, generation}    │
│                                                         │
│  On LOCK_BUSY (78):                                     │
│    if mutex absent → spawn                              │
│    if live Job has proven orphan member → adopt/stop    │
│    else → WAIT + alert (no heuristic kill)              │
└─────────────────────────────────────────────────────────┘
          │ AssignProcessToJobObject (live proof)
          ▼
┌─────────────────────────────────────────────────────────┐
│ energy_logger                                           │
│  CreateMutexW(Global\…\<install_id>, custom SD)         │
│  hold handle for process life                           │
│  diagnostic PID file = telemetry only                   │
└─────────────────────────────────────────────────────────┘
```

**Replace M2B discovery stack entirely.** Keep mutex + Job Object + exit `78` + `RECONCILING` state machine.

---

## Remaining risks (after M2.5 design)

| Risk | Residual? | Notes |
|------|-----------|-------|
| Foreign process holds mutex indefinitely | Yes | Wait/alert; ops kill; ACL reduces likelihood |
| Same service account malicious code | Yes | Same SID bypasses ACL |
| Nested-job assign edge cases under exotic parents | Yes | Needs Service soak |
| Undocumented mutex-owner query temptation | N/A | Explicitly out of policy |
| Monitoring UP during wait | Yes until M3 | Accept or surface RECONCILING |

---

## Production recommendation

1. **Do not implement M3 yet.**  
2. **Implement M2.5** per this document (safe identity + wait-based foreign reconcile + live Job queries + mutex ACL + install_id).  
3. **Gate release** on Windows Service Session 0 soak (closes R4).  
4. Only then proceed to M3 heartbeat identity for Monitoring consistency.

**Success criterion for closing P0s:** Runtime has **zero** code paths that call process terminate based on TEMP PID or cmdline heuristics; adopt/kill only on live Job proof with PID-reuse defense.

---

## References (Microsoft Learn)

- [Mutex Objects](https://learn.microsoft.com/en-us/windows/win32/sync/mutex-objects)  
- [CreateMutexW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexw)  
- [Kernel Object Namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)  
- [IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob)  
- [QueryInformationJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-queryinformationjobobject)  
- [JOBOBJECT_BASIC_PROCESS_ID_LIST](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_process_id_list)  
- [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)  
- [Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)  
- [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
