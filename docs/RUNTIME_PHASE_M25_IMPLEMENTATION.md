# Runtime Recovery — Phase M2.5 Implementation

**Date:** 2026-08-05  
**Status:** Implemented (Service Session 0 soak **NOT VERIFIED**)  
**Authority:** `docs/RUNTIME_M25_SAFE_IDENTIFICATION_DESIGN.md`

---

## Scope

| In M2.5 | Out of scope |
|---------|----------------|
| Live Job membership for adopt/kill | M3 heartbeat / runtime identity |
| PID-reuse defense (`pid + create_time`) | Heuristic mutex-owner discovery |
| Foreign mutex → wait + alert + ABANDON | Changing M2A acquire order |
| Terminate only Job-proven + identity | Undocumented NtQuery handle walks |
| Mutex custom SECURITY_ATTRIBUTES | Win7 single-job design |
| install_id env → registry → fallback | |
| Nested-job parent diagnostics | Fabricated Service PASS |
| Supervisor API reconcile diagnostics | |

**M1 / M2A acquire semantics unchanged.** M2B cmdline / TEMP / historical-PID kill paths **removed**.

---

## Architecture (normative)

```
exit 78 → RECONCILING
  │
  ├─ mutex absent → spawn (holder_gone)
  │
  └─ mutex present
       ├─ live_members = JobObjectBasicProcessIdList / IsProcessInJob
       ├─ prove each member: live Job + (create_time match if known)
       ├─ proven → ADOPT (prefer)
       │            adopt fail → terminate ONLY that proven member
       │                         (log why / proof / job / create_time)
       └─ no proven member → FOREIGN
              → emit ForeignMutexDetected
              → Monitoring alert (runtime_foreign_mutex)
              → wait / retry
              → ABANDON foreign_mutex_holder_timeout
              → NEVER terminate from TEMP / cmdline / history
```

**Identity key:** live Job membership **and** `(pid, create_time)` when spawn identity exists.  
**Never:** diagnostic PID, TEMP file, cmdline substring, executable name, historical `assigned_pids`.

---

## Files

| File | Change |
|------|--------|
| `app/runtime/job_object.py` | `list_live_member_pids`, `is_pid_in_job`, `detect_parent_job_state`, `assign_history` (diagnostic only) |
| `app/runtime/process_identity.py` | **NEW** — create_time + verify |
| `app/runtime/install_id.py` | **NEW** — env → registry → development_fallback |
| `app/runtime/ownership_reconcile.py` | **REWRITE** — safe decision tree |
| `app/runtime/energy_logger_mutex.py` | Custom SD ACL; install_id via resolver |
| `app/runtime/child_manager.py` | Spawn identity; adopt only if live Job member |
| `app/runtime/supervisor.py` | Live Job reconcile; rich `SupervisorStatus`; parent job diag |
| `app/runtime/events.py` | `ForeignMutexDetected`; richer `ReconcileCompleted` |
| `app/monitoring/runtime_bridge/mapper.py` | Persist reconcile + foreign events |
| `app/monitoring/runtime_bridge/subscriber.py` | Open Monitoring alert on foreign mutex |
| `app/monitoring/seeds.py` | `runtime_foreign_mutex` rule |
| `tests/runtime/test_ownership_reconcile.py` | M2.5 unit coverage |
| `docs/_m25_artifacts/m25_live_validation.py` | Tests A–G harness |

---

## Required capabilities (checklist)

| # | Requirement | Implementation |
|---|-------------|----------------|
| 1 | Live Job membership | `IsProcessInJob` + `QueryInformationJobObject(JobObjectBasicProcessIdList)` |
| 2 | PID reuse protection | `ProcessIdentity(pid, create_time)` at spawn; verify before adopt/terminate |
| 3 | Foreign mutex policy | Wait + `ForeignMutexDetected` + alert → ABANDON; no heuristic kill |
| 4 | Terminate policy | Proven Job members only; `TerminateDecision` logged |
| 5 | Mutex ACL | `ConvertStringSecurityDescriptorToSecurityDescriptorW` → SY/BA/current user; no default DACL |
| 6 | install_id | `LUTRON_INSTALL_ID` → registry → `default` (logged) |
| 7 | Service compatibility diagnostics | `IsProcessInJob(GetCurrentProcess(), NULL)` at start; assign failure terminates child (never unmanaged) |
| 8 | Supervisor API | `reconcile_state`, `mutex_present`, `live_job_members`, `foreign_mutex_holder`, `reconcile_reason`, `terminate_reason`, `proof_method`, `parent_job` |

---

## P0 closure map

| P0 | Issue | M2.5 status |
|----|-------|-------------|
| **R1** | Diagnostic PID → terminate | **CLOSED** — hint action=`ignore_diagnostic_pid` only (evidence D) |
| **R2** | Cmdline token → terminate | **CLOSED** — discover/cmdline removed from reconcile (evidence E) |
| **R3** | Historical `assigned_pids` → false adopt | **CLOSED** — live Job only; kwarg ignored (evidence B/C + unit) |
| **R4** | Nested Job / Service assign | **PARTIAL** — diagnostics + nested Win8+ child assign proven in-process (**F** PASS); Session 0 Service soak **NOT VERIFIED** (**G**) |

Related P1 addressed in M2.5 (not P0): mutex ACL + install_id loading (R5/R10 direction).

---

## Explicit non-goals

- Do **not** start M3.
- Do **not** query mutex owner PID (no documented Win32 API).
- Do **not** mark Service PASS without Session 0 evidence.

---

## Success criteria

Every P0 is **CLOSED** or **NOT VERIFIED**.  
Nothing marked PASS without runtime evidence in `docs/_m25_artifacts/`.
