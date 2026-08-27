# Monitoring Platform — Implementation Standards

**Status:** Binding for all monitoring implementation phases  
**Scope:** `lutron_backend` monitoring subsystem (and related frontend phases when applicable)  
**Authority:** Architecture Freeze · Runtime Ownership · Component Architecture · Implementation Roadmap  

This document governs every future monitoring phase. Deviations require explicit written approval before merge.

---

## 1. GENERAL RULES

### 1.1 Mandatory

1. **No shortcuts.** Do not bypass Storage, Instrumentation, feature flags, or tests to “get it working.”
2. **No duplicate logic.** Shared validation, emit helpers, and repository behavior live in one place. Do not copy-paste SQL, event shaping, or alert evaluation across modules.
3. **No direct SQL / ORM writes to `monitoring.*` outside the Storage layer.** Bootstrap, Monitoring Service, Alert Engine, and Analytics Engine persist only through Storage public interfaces.
4. **No monitoring code inside business logic.** Controllers/CRUD/LEAP handlers must not contain monitoring persistence, alert evaluation, or rollup logic. They may call **Instrumentation** only (one-liner / decorator / context manager).
5. **Producers only emit.** `main`, `listener`, `energy_logger`, `loadcontroller_listener`, `scheduler`, and HTTP middleware emit via Instrumentation. They never open monitoring sessions or call repositories.
6. **Writer ownership (freeze).**
   - **Monitoring Service** → telemetry facts (`health_current`, connectivity, ping, events, job_run, http_agg, metric_sample)
   - **Bootstrap / Admin** → dimensions (`component`, `job_definition`, `metric_definition`, `alert_rule`)
   - **Alert Engine** → `alert_instance` only (among alert tables in v1)
   - **Analytics Engine** → `mon_metric_rollup` only (among rollup tables)
7. **Storage is the only module that executes database operations against `monitoring.*`.**
8. **Every new monitoring module must have unit tests** before merge.
9. **Every feature ships behind feature flags until explicitly approved** for default-on (see §1.3).
10. **Emit failures must not break LMS.** Instrumentation and remote ingest errors are swallowed or degraded; LEAP loops and HTTP business responses must continue.
11. **No domain telemetry duplication.** Do not copy `processor_*_events`, energy/occupancy stats, device alerts, or FOFP layout payloads into monitoring tables.
12. **No execution traces in v1** unless a later roadmap phase explicitly revises the freeze.
13. **Single pipeline leader.** v1 assumes single uvicorn worker or an approved leader-lease implementation. Do not start multiple writers “temporarily.”
14. **Listener is source of truth for LEAP session connectivity.** Load Controller Listener must not write `mon_processor_connectivity_*`.
15. **Do not register all user schedules as monitored jobs by default.**

### 1.2 Prohibited patterns

- Daemon processes calling `SessionLocal()` for monitoring writes
- Middleware writing HTTP rows per request
- Alert Engine updating `mon_component_health_current`
- Analytics writing raw samples
- Importing `app.monitoring.storage` from `listener.py` / `energy_logger.py` / `loadcontroller_listener.py`
- Hardcoding secrets in source (ingest secret, JWT, etc.)
- Cross-cutting `print()` as the primary observability for monitoring internals (use logging)

### 1.3 Feature flags

| Flag | Purpose | Default until approved |
|------|---------|------------------------|
| `MONITORING_ENABLED` | Master: schema ensure/bootstrap/service/API | off |
| `MONITORING_INGEST_ENABLED` | Daemon → ingest | off |
| `MONITORING_LEAP_TELEMETRY` | Connectivity + ping emit | off |
| `MONITORING_HTTP_METRICS_ENABLED` | HTTP middleware agg | off |
| `MONITORING_JOBS_ENABLED` | System job wrappers | off |
| `MONITORING_ALERTS_ENABLED` | Alert engine loop | off |
| `MONITORING_ANALYTICS_ENABLED` | Rollups + retention | off |

Flags are read at startup / safe intervals. Changing a flag must not require code edits. Until product approval, merged code may exist but must remain inert when flags are off.

### 1.4 LMS safety

- Prefer try/except isolation around monitoring startup hooks (same spirit as existing daemon start blocks in `main.py`).
- Monitoring API returning 503 when disabled/not ready must not affect non-monitoring routes.
- Regression smoke after every phase: login, one floor/home read, one LEAP-backed read when applicable.

---

## 2. CODING STANDARDS

### 2.1 Naming conventions

| Kind | Convention | Examples |
|------|------------|----------|
| Package | `app.monitoring` | — |
| Modules | `snake_case.py` | `service.py`, `remote_client.py` |
| Classes | `PascalCase` | `MonitoringService`, `AlertEngine` |
| Functions | `snake_case` | `upsert_component_health` |
| Event types | `PascalCase` nouns | `HeartbeatEvent`, `LeapPingEvent` |
| Metric keys | dotted `snake` | `pipeline.queue_length`, `alert.eval_lag_seconds` |
| Component codes | stable lowercase snake | `api`, `listener`, `monitoring_pipeline` |
| Job keys | stable snake | `device_refresh`, `log_energy_stats` |
| Feature flags | `MONITORING_*` env | see §1.3 |
| Tables | freeze names only | `mon_component_health_current` |
| Tests | `test_<module>_<behavior>.py` | `test_service_backpressure.py` |

Do not invent alternate table or component names that diverge from the freeze.

### 2.2 Folder organization

Target layout (create only in the roadmap phase that needs it):

```
app/monitoring/
  __init__.py
  bootstrap.py
  registry.py
  service.py
  instrumentation.py
  events.py              # Event Builder (internal to instrumentation package OK)
  drop_policy.py
  remote_client.py
  watchdog.py
  job_wrapper.py
  http_middleware.py
  api_read_models.py
  storage/               # only DB access to monitoring.*
    __init__.py
    session.py           # if dedicated helpers needed
    components.py
    health.py
    connectivity.py
    ping.py
    events.py
    jobs.py
    http_agg.py
    metrics.py
    alerts.py
  alerts/
    engine.py
    evaluators.py
    notify_email.py
  analytics/
    engine.py
  retention.py
app/api/routes/monitoring.py
app/schemas/monitoring.py
migrations/apply_monitoring_schema.py
tests/monitoring/
docs/monitoring/         # standards & phase notes
```

Frontend monitoring UI (later phase) lives under `lutron_frontend` in a dedicated monitoring feature area; it consumes Monitoring API only.

### 2.3 Class responsibilities

| Unit | Responsibility | Must not |
|------|----------------|----------|
| Instrumentation | Emit/SDK, buffers, remote client | Persist, evaluate alerts, rollup |
| Event Builder | Validate/normalize events | I/O |
| Monitoring Service | Queue, pipeline, leader, flush | Alert instance writes, rollups |
| Storage | All `monitoring.*` SQL/ORM | Business LEAP, alert policy |
| Bootstrap | Seed dimensions, registry | Runtime telemetry loops |
| Alert Engine | Evaluate rules, alert instances, best-effort notify | Telemetry inserts |
| Analytics Engine | Rollups | Raw sample inserts, alerts |
| API routes | HTTP adapt | Business rules beyond authz/DTO |
| Read models | Compose dashboard/API DTOs | Writes |

Prefer small classes with clear owners over “god” monitoring managers.

### 2.4 Dependency rules

Allowed dependency direction:

```
routes/schemas → api_read_models → storage
routes → service (ingest) / alerts (ack) / bootstrap (admin)
producers → instrumentation → events → service | remote_client
service → events, storage, drop_policy
alerts → storage, instrumentation (emit only)
analytics → storage, instrumentation (emit only)
bootstrap → storage, registry
```

Forbidden:

```
storage → service | alerts | analytics | instrumentation
listener|energy|lc|scheduler → storage
instrumentation → storage
alerts → service (writes)
analytics → alerts
```

### 2.5 Import rules

1. Daemons may import `app.monitoring.instrumentation` and `app.monitoring.remote_client` only (plus types if needed).
2. Do not import Storage from producers.
3. Avoid circular imports: keep event DTOs free of Storage/Service imports.
4. Prefer absolute imports consistent with existing LMS style (`from app.monitoring...`).
5. Do not import monitoring from unrelated CRUD modules except a single Instrumentation call site.

### 2.6 Logging rules

1. Use stdlib `logging` with a dedicated logger name, e.g. `lutron_monitoring` (or child loggers `lutron_monitoring.pipeline`, `.alerts`, `.analytics`).
2. Levels: ERROR for write/eval failures; WARNING for drops/backpressure; INFO for start/stop/leader; DEBUG for per-event (off by default in prod).
3. Do not log secrets, full certs, or entire LEAP payloads.
4. Prefer structured message prefixes: `[monitoring][pipeline]`, `[monitoring][ingest]`.
5. Emit path must not log at INFO per ping at scale (use DEBUG or sampled).

### 2.7 Exception handling

1. **Instrumentation public API:** never raise into LMS business code; catch, log at most WARNING, optional metric increment.
2. **Storage:** raise typed errors to Service/Alert/Analytics; do not swallow.
3. **Service pipeline:** retry policy then drop/requeue per `drop_policy`; mark degraded.
4. **Alert notify failure:** must not roll back successful `alert_instance` open.
5. **API:** map not-ready to 503; auth failures to 401/403; validation to 422; never 500 for expected backpressure (use 503/429 as designed).

### 2.8 Async / thread safety

1. Pipeline worker: **single consumer** of the ingress queue (thread or asyncio task — pick one model per phase and document it; do not mix dual consumers).
2. HTTP aggregation buffer: concurrent request threads may update counters; use a lock or thread-safe structure.
3. Remote client from daemons: timeouts mandatory; never block the LEAP recv loop without a timeout budget.
4. Shared RegistrySnapshot: treat as immutable after load/reload; reload replaces reference atomically.
5. SQLAlchemy sessions: create per operation/unit-of-work in Storage; never share a session across threads.
6. Do not call blocking DB from the asyncio LEAP loop without `asyncio.to_thread` / executor if Instrumentation is invoked on that loop — prefer scheduling emit on a short non-blocking path (queue put_nowait).

---

## 3. DATABASE RULES

### 3.1 Migration strategy

1. Follow LMS precedent: **idempotent** `ensure_*` / `migrations/apply_*.py` (no Alembic required unless the project later standardizes on it).
2. Monitoring objects live in schema **`monitoring`** (not mixed into ad-hoc public names).
3. DDL phases ship before runtime writers.
4. Seeds are idempotent upserts by natural key (`code`, `job_key`, `metric_key`, rule `code`).
5. Do not destructive-drop freeze tables in forward migrations without an approved rollback plan.
6. Cross-schema FKs to `public.processor` / `public.users` only where the freeze requires them.

### 3.2 Repository rules

1. One repository module per aggregate (or clear grouping); no raw SQL in routes/engines.
2. All writes go through explicit Storage methods named by intent (`upsert_*`, `insert_*`).
3. Read models call Storage read methods; they do not craft ad-hoc queries in route handlers when a read-model exists.
4. Repositories accept/return plain DTOs or mapped rows — keep FastAPI schemas at the API edge.

### 3.3 Transaction rules

1. Default: **one short transaction per Storage write command** (or small batch flush).
2. Alert open + related instance fields: single transaction per alert mutation.
3. Do not hold transactions open across LEAP I/O or network ingest waits.
4. Batch HTTP agg upserts may be one transaction per flush batch.
5. Explicit `commit`/`rollback` ownership stays inside Storage (or a documented UoW helper used only by Storage).

### 3.4 Retry rules

1. Retry only **transient** DB errors (connection reset, deadlock, serialization) with capped attempts and jitter.
2. Do not infinite-retry in the pipeline; escalate to drop + `pipeline.writer_error_total` + degraded health.
3. Remote ingest client: capped retries with backoff; then drop emit.
4. Alert notify: capped retries; then metric `alert.notify_fail_total`.

### 3.5 Current-state updates

1. Tables `mon_component_health_current` and `mon_processor_connectivity_current` are **upsert-only** (overwrite in place).
2. Connectivity: insert `mon_event` on **state transition**; optional periodic refresh upsert without event spam.
3. Health: upsert on each heartbeat; do not insert a historical heartbeat table in v1 (eliminated in freeze).

### 3.6 Event inserts

1. `mon_event` and `mon_leap_ping_sample` and `mon_job_run` are **append-oriented** (job_run: prefer single completion insert unless start/end was explicitly designed).
2. Payloads must be size-capped; truncate in Event Builder.
3. Unknown metric keys / invalid events: **drop** with metric/log; never insert partial corrupt rows.

### 3.7 Retention handling

1. Retention/partition drops run only from the approved retention job when `MONITORING_ANALYTICS_ENABLED` (or a dedicated retention flag) is on.
2. Retention must be idempotent and monitored via `mon_job_run`.
3. Never run unbounded `DELETE` without a time predicate and batching.
4. Raw retention targets (freeze): ping ~7d, http 1m ~14d, events ~90d, job_run ~30d, samples ~3–7d — implement as configured constants, not magic numbers scattered in code.

---

## 4. TESTING STANDARDS

Every implementation phase must satisfy the following before merge (scope scaled to what the phase delivers).

### 4.1 Unit tests

- Required for every new monitoring module.
- Cover: Event Builder validation, drop policy, evaluators, buffer aggregation, fingerprint/dedup, registry resolution.
- No live LEAP or real processors required.
- Mock Storage at engine boundaries where appropriate; Storage unit tests may use SQLite only if dialect-safe — prefer Postgres test DB for JSON/FK-sensitive paths.

### 4.2 Integration tests

- Pipeline: emit → queue → Storage → row visible.
- Ingest API: auth success/failure; flag off behavior.
- Bootstrap idempotency.
- Alert: condition → open → resolve/ack (with controlled clock/heartbeats).
- Use isolated test database; never point at production.

### 4.3 Regression tests

- With **all monitoring flags off**: existing LMS smoke paths still pass (auth login, representative GET that hit DB).
- Listener/scheduler modules still import and start entrypoints without monitoring enabled.
- No change to device alert table semantics.

### 4.4 Smoke tests

- Flag-on: API starts; `/monitoring/health` reachable when Phase 5+.
- MVMS gate (roadmap): daemons appear; connectivity matches a known processor state.
- Document manual smoke steps in the phase PR description.

### 4.5 Rollback verification

- Demonstrate flags off restores prior behavior.
- For DDL phases: document rollback (`DROP SCHEMA` on non-prod only with approval).
- For hooks in `main`/daemons: reverting flag sufficient without hotfix unless a hard crash bug exists.

### 4.6 Performance checks (when phase touches hot paths)

- Phases 7–8: emit/middleware overhead measured or bounded (timeout budgets documented).
- Fail the phase if LEAP loop or p95 HTTP regresses unacceptably under agreed thresholds (record thresholds in the phase PR).

### 4.7 Test placement

```
tests/monitoring/
  unit/
  integration/
```

Mirror existing pytest practices in the repo when present.

---

## 5. CODE REVIEW CHECKLIST

Reviewers **must** verify before approving a monitoring PR:

### Architecture / ownership

- [ ] Single-writer rules preserved for every touched table
- [ ] Producers only call Instrumentation (or remote client), not Storage
- [ ] No monitoring persistence inside CRUD/LEAP business logic beyond emit hooks
- [ ] No dual connectivity writers (LC not writing LEAP SoT tables)
- [ ] No new tables outside freeze without architecture amendment
- [ ] No trace/APM schema introduced in v1 accidentally

### LMS safety

- [ ] Business logic regression risk assessed; hot-path changes isolated
- [ ] Emit/ingest failures cannot crash listener/energy/LC/API business flow
- [ ] Feature flags gate new behavior; defaults remain off unless approved
- [ ] Startup/shutdown hooks isolated with error handling

### Code quality

- [ ] No duplicated logic (search for copy-paste SQL/event shaping)
- [ ] Folder and naming match §2
- [ ] Dependency/import rules respected
- [ ] Logging appropriate (no secret leakage, no INFO flood)
- [ ] Exceptions handled per §2.7

### Data / performance

- [ ] Transactions short; no DB work on LEAP wait paths
- [ ] HTTP cardinality controlled (templates, not raw IDs)
- [ ] Retention/deletes safe if present
- [ ] Performance impact acceptable for the phase

### Tests & docs

- [ ] Unit tests added/updated
- [ ] Integration/regression/smoke evidence in PR
- [ ] Rollback verification described
- [ ] `docs/monitoring` or PR notes updated for phase deliverables
- [ ] Roadmap phase ID cited in PR title/description

### Security

- [ ] Ingest endpoint authenticated/authorized
- [ ] Monitoring admin/ack routes enforce LMS roles
- [ ] No hardcoded secrets

---

## 6. DEFINITION OF DONE

A roadmap phase is **Done** only when all of the following are true:

1. **Code implemented** per phase scope (no drive-by refactors outside scope).
2. **Reviewed** with §5 checklist completed (PR approval).
3. **Tested** per §4 (unit + required integration/regression/smoke for that phase).
4. **Documented** — phase notes: flags, how to enable, smoke steps, known limits.
5. **Feature flag verified** — behavior correct when **on** and inert when **off**.
6. **Rollback verified** — flag-off (and DDL rollback plan if applicable) confirmed.
7. **LMS still functional** — agreed smoke paths pass after the phase.
8. **Acceptance criteria** from the Implementation Roadmap for that phase are met.
9. **No open P0/P1** defects introduced by the phase without explicit waiver.

Partial merges behind flags are allowed only if the phase’s acceptance criteria are still met for the merged subset and the PR declares remaining work.

---

## 7. IMPLEMENTATION ORDER

Implementation **must** follow the approved Implementation Roadmap order:

| Order | Phase |
|------:|-------|
| 0 | Program setup & contracts |
| 1 | Monitoring schema (DDL) |
| 2 | Storage layer + Bootstrap |
| 3 | Monitoring Service + Instrumentation core |
| 4 | API heartbeats + lifecycle |
| 5 | Monitoring read + ingest API |
| 6 | Daemon remote heartbeats |
| 7 | Connectivity + LEAP ping + events (**MVMS**) |
| 8 | HTTP request aggregates |
| 9 | System job run instrumentation |
| 10 | Alert Engine |
| 11 | Analytics rollups + retention |
| 12 | Operator Monitoring UI |
| 13 | Hardening & multi-worker readiness |

### Order rules

1. Do not start Phase N+1 implementation in the same PR as Phase N unless N is already Done.
2. Do not skip ahead to UI (12) or Alerts (10) before MVMS (through Phase 7) without architecture waiver.
3. MVMS demonstration gate remains: Phases 1–7 complete and accepted before declaring Minimum Viable Monitoring System.
4. Any change to phase order requires updating the roadmap doc and this section together.

---

## Document control

| Item | Value |
|------|-------|
| Location | `lutron_backend/docs/monitoring/IMPLEMENTATION_STANDARDS.md` |
| Applies to | All monitoring PRs and phases |
| Conflict resolution | Architecture Freeze > this Standards doc > local judgment |
| Amendments | Update this file in the same PR as the process change |

**No feature implementation is authorized by this document alone; it only constrains how implementation must be done.**
