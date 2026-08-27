# Monitoring API Reference

**Date:** 2026-07-31  
**Base path:** `/monitoring`  
**Routers:**
- `app/api/routes/monitoring_dashboard.py` — tag **Monitoring Dashboard** (mounted first)
- `app/api/routes/monitoring.py` — tag **Monitoring** (Phase 5)

**Auth (unless noted):** LMS JWT via `Authorization: Bearer <access_token>` (`get_current_user`).  
**Master gate:** `MONITORING_ENABLED=true|1|yes` → otherwise **503** `{ "detail": "Monitoring is disabled" }`.

### Routing note

Dashboard is registered **before** Phase 5 on the same prefix. Therefore:

| Path | Active handler |
|------|----------------|
| `GET /monitoring/jobs` | **Dashboard** (`dashboard_read_models.get_jobs`) |
| `GET /monitoring/pipeline` | **Dashboard** (`dashboard_read_models.get_pipeline`) |
| `GET /monitoring/health` | Phase 5 (`api_read_models.get_health_board`) |
| `GET /monitoring/processors/connectivity` | Phase 5 (`api_read_models.get_connectivity_map`) |
| `POST /monitoring/ingest` | Phase 5 |

Phase 5 also defines `GET /pipeline` and `GET /jobs`, but those FastAPI routes are **shadowed** and not reachable.

### Functional status (this workspace)

| Check | Result |
|-------|--------|
| Code paths implemented | Yes |
| `environment.env` contains `MONITORING_*` | **No** (as of this document) |
| `is_monitoring_enabled()` via dotenv | **False** unless set in the process / service environment |

If Monitoring was enabled only in the **running** uvicorn/service env (not written to `environment.env`), live calls may succeed while a cold import of flags still reads false. Per-endpoint “functional” below assumes: `MONITORING_ENABLED=true`, API restarted, schema/bootstrap succeeded, `MonitoringService` running. Empty arrays are still **valid** when telemetry has not been ingested yet.

---

## A. Phase 5 — Monitoring

### A1. `GET /monitoring/health`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Component registry + current health board + in-process pipeline snapshot |
| **Request parameters** | None (JWT required) |
| **Response schema** | JSON object (untyped `Dict`) |
| **Backend service** | `api_read_models.get_health_board` → `MonitoringService.get_runtime_status`, `get_registry`, `MonitoringStorage.health` |
| **Functional** | Yes when master flag on (health rows may be empty pre-heartbeat) |

**Sample response:**

```json
{
  "monitoring_enabled": true,
  "pipeline": {
    "running": true,
    "degraded": false,
    "health_status": "up",
    "queue_length": 0,
    "max_queue": 1000,
    "last_success_at": "2026-07-31T10:00:00",
    "last_error": null,
    "counters": { "accepted_total": 12 }
  },
  "components": [
    {
      "id": "…uuid…",
      "code": "api",
      "kind": "process",
      "display_name": "API",
      "is_active": true
    }
  ],
  "health": [
    {
      "component_id": "…uuid…",
      "component_code": "api",
      "status": "up",
      "last_heartbeat_at": "2026-07-31T10:00:00",
      "updated_at": "2026-07-31T10:00:00",
      "detail": {}
    }
  ]
}
```

---

### A2. `GET /monitoring/processors/connectivity`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Current LEAP processor connectivity map |
| **Request parameters** | None (JWT) |
| **Response schema** | `{ "processors": [ … ] }` |
| **Backend service** | `api_read_models.get_connectivity_map` → `MonitoringStorage.connectivity` |
| **Functional** | Yes; often empty until LEAP telemetry / connectivity events are ingested |

**Sample response:**

```json
{
  "processors": [
    {
      "processor_id": 7,
      "status": "up",
      "observer_component_id": "…uuid…",
      "last_ok_at": "2026-07-31T10:00:00",
      "last_error_at": null,
      "updated_at": "2026-07-31T10:00:00",
      "detail": {}
    }
  ]
}
```

---

### A3. `POST /monitoring/ingest`

| Field | Detail |
|-------|--------|
| **HTTP method** | `POST` |
| **Purpose** | Authenticated event ingest into `MonitoringService` queue |
| **Auth** | **Not JWT** — `X-Monitoring-Ingest-Token: <secret>` or `Authorization: Bearer <MONITORING_INGEST_TOKEN>` |
| **Request parameters** | JSON body: single event object **or** `{ "events": [ … ] }` |
| **Response schema** | `MonitoringIngestResponse` |
| **Backend service** | `parse_ingest_payload` + `MonitoringService.submit` |
| **Functional** | Requires `MONITORING_ENABLED`, `MONITORING_INGEST_ENABLED`, configured `MONITORING_INGEST_TOKEN`, and service ready |

**Event object (common fields):**

| Field | Required | Notes |
|-------|----------|-------|
| `event_type` | Yes | `heartbeat` \| `connectivity` \| `leap_ping` \| `job_run` \| `http_aggregate` \| `metric` \| `lifecycle` |
| `component_code` | depends | heartbeat / lifecycle / etc. |
| `processor_id` | depends | connectivity / leap_ping |
| `status`, `detail`, `observed_at`, … | optional | per event type |

**Sample request:**

```json
{
  "event_type": "heartbeat",
  "component_code": "api",
  "status": "up",
  "observed_at": "2026-07-31T10:00:00"
}
```

**Sample response:**

```json
{
  "ok": true,
  "results": [
    {
      "accepted": true,
      "dropped": false,
      "rejected": false,
      "reason": null,
      "event_type": "HeartbeatEvent"
    }
  ],
  "accepted_count": 1,
  "dropped_count": 0,
  "rejected_count": 0
}
```

**Error cases:** 503 disabled / ingest disabled / service not ready / backpressure; 401 bad token; 422 invalid JSON or event.

---

### A4. Shadowed Phase 5 routes (not reachable)

| Defined in code | Shadowed by |
|-----------------|-------------|
| `GET /monitoring/pipeline` → `api_read_models.get_pipeline_status` | Dashboard `get_pipeline` |
| `GET /monitoring/jobs` → `api_read_models.get_jobs_panel` | Dashboard `get_jobs` |

---

## B. Monitoring Dashboard (Phase 12)

All require JWT + `MONITORING_ENABLED`.

### B1. `GET /monitoring/summary`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Top-level dashboard KPIs |
| **Request parameters** | None |
| **Response schema** | `MonitoringSummaryResponse` |
| **Backend service** | `dashboard_read_models.get_summary` → Storage health/connectivity/alerts/jobs/metrics + `MonitoringService` |
| **Functional** | Yes when enabled |

**Sample response:**

```json
{
  "monitoring_enabled": true,
  "pipeline": {
    "running": true,
    "degraded": false,
    "health_status": "up",
    "queue_length": 0,
    "max_queue": 1000,
    "last_success_at": "2026-07-31T10:00:00",
    "last_error": null,
    "counters": {},
    "worker_state": "running",
    "dropped_events": 0
  },
  "component_counts": { "total": 3, "up": 2, "down": 0, "degraded": 0, "unknown": 1, "registered": 5 },
  "active_alert_count": 0,
  "processor_status_summary": { "total": 0, "up": 0, "down": 0, "degraded": 0, "other": 0 },
  "job_summary": { "definitions": 4, "recent_success": 1, "recent_failure": 0 },
  "latest_analytics_timestamp": null
}
```

---

### B2. `GET /monitoring/components`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Per-component current health list |
| **Request parameters** | None |
| **Response schema** | `{ "components": [...], "count": N }` |
| **Backend service** | `dashboard_read_models.get_components` → `storage.health` + registry |
| **Functional** | Yes |

**Sample response:**

```json
{
  "components": [
    {
      "component_id": "…",
      "component_code": "listener",
      "display_name": "Listener",
      "status": "up",
      "last_heartbeat_at": "2026-07-31T10:00:00",
      "updated_at": "2026-07-31T10:00:00",
      "detail": null
    }
  ],
  "count": 1
}
```

---

### B3. `GET /monitoring/processors`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Processor connectivity (dashboard shape + count) |
| **Request parameters** | None |
| **Response schema** | `{ "processors": [...], "count": N }` |
| **Backend service** | `dashboard_read_models.get_processors` → `storage.connectivity` |
| **Functional** | Yes (may be empty without LEAP telemetry) |

---

### B4. `GET /monitoring/alerts`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Paginated alert instances |
| **Request parameters** | Query: `status` (`open`\|`acknowledged`\|`resolved`), `severity`, `component` (component_code), `limit` (1–500, default 50), `offset` (≥0) |
| **Response schema** | `PaginatedAlertsResponse` |
| **Backend service** | `dashboard_read_models.get_alerts` → `storage.alerts` |
| **Functional** | Yes; typically empty until alert engine opens instances (`MONITORING_ALERTS_ENABLED`) |

**Sample response:**

```json
{
  "items": [
    {
      "id": 1,
      "rule_id": "…",
      "status": "open",
      "severity": "warning",
      "fingerprint": "…",
      "title": "Component down",
      "message": "listener heartbeat missing",
      "opened_at": "2026-07-31T09:00:00",
      "acknowledged_at": null,
      "resolved_at": null,
      "acknowledged_by_user_id": null,
      "component_id": "…",
      "processor_id": null,
      "job_definition_id": null,
      "detail": {}
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

---

### B5. `POST /monitoring/alerts/{alert_id}/acknowledge`

| Field | Detail |
|-------|--------|
| **HTTP method** | `POST` |
| **Purpose** | Acknowledge an open alert (only dashboard write besides ingest) |
| **Request parameters** | Path: `alert_id` (int). Body: none. JWT user id recorded |
| **Response schema** | `AlertAcknowledgeResponse` |
| **Backend service** | `dashboard_read_models.acknowledge_alert` → `AlertStateManager` |
| **Functional** | Yes when enabled; **404** if not found / not open; **409** on conflict |

**Sample response:**

```json
{
  "id": 1,
  "status": "acknowledged",
  "acknowledged_at": "2026-07-31T10:05:00",
  "acknowledged_by_user_id": 42
}
```

---

### B6. `GET /monitoring/jobs`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Job definitions + recent runs (dashboard) |
| **Request parameters** | Query: `limit` (1–500, default 50) |
| **Response schema** | `{ "recent_runs": [...], "definitions": [...], "registry_job_count": N }` |
| **Backend service** | `dashboard_read_models.get_jobs` → `storage.jobs` |
| **Functional** | Yes; definitions seeded at bootstrap; runs need `MONITORING_JOBS_ENABLED` / job wrappers |

**Sample response:**

```json
{
  "recent_runs": [
    {
      "id": 10,
      "job_key": "monitoring_alert_engine",
      "display_name": "Alert engine",
      "outcome": "success",
      "started_at": "2026-07-31T10:00:00",
      "finished_at": "2026-07-31T10:00:01",
      "duration_ms": 120,
      "error_class": null,
      "trigger_source": "scheduler"
    }
  ],
  "definitions": [
    {
      "job_key": "monitoring_alert_engine",
      "display_name": "Alert engine",
      "is_active": true,
      "component_id": "…"
    }
  ],
  "registry_job_count": 4
}
```

---

### B7. `GET /monitoring/http`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | HTTP request aggregates + related metric rollups |
| **Request parameters** | None |
| **Response schema** | `{ "aggregates": [...], "rollups": [...] }` |
| **Backend service** | `dashboard_read_models.get_http` → `storage.http_agg` + `storage.metrics` |
| **Functional** | Yes; data when `MONITORING_HTTP_METRICS_ENABLED` |

**Sample response:**

```json
{
  "aggregates": [
    {
      "bucket_start": "2026-07-31T10:00:00",
      "route_template": "/monitoring/summary",
      "method": "GET",
      "status_class": "2xx",
      "request_count": 5,
      "error_count": 0,
      "sum_duration_ms": 40,
      "max_duration_ms": 12
    }
  ],
  "rollups": []
}
```

---

### B8. `GET /monitoring/analytics`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Metric rollup series |
| **Request parameters** | Query: `bucket_size` (`1h`\|`1d`), `limit` (1–2000, default 200) |
| **Response schema** | `{ "bucket_size": …, "rollups": [...], "count": N }` |
| **Backend service** | `dashboard_read_models.get_analytics` → `storage.metrics.query_metric_rollups` |
| **Functional** | Yes; populated when `MONITORING_ANALYTICS_ENABLED` rollup job has run |

---

### B9. `GET /monitoring/pipeline`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | In-process monitoring pipeline / worker status (dashboard) |
| **Request parameters** | None |
| **Response schema** | Pipeline dict (`running`, `degraded`, `health_status`, `queue_length`, `max_queue`, `last_success_at`, `last_error`, `counters`, `worker_state`, `dropped_events`) |
| **Backend service** | `dashboard_read_models.get_pipeline` → `MonitoringService.get_runtime_status` |
| **Functional** | Yes when service started with master flag |

**Sample response:**

```json
{
  "running": true,
  "degraded": false,
  "health_status": "up",
  "queue_length": 0,
  "max_queue": 1000,
  "last_success_at": "2026-07-31T10:00:00",
  "last_error": null,
  "counters": {},
  "worker_state": "running",
  "dropped_events": 0
}
```

---

## C. Runtime Recovery (dashboard observer)

Data from `mon_event` (prefix `runtime.*`) and live bridge/supervisor when attached.

### C1. `GET /monitoring/runtime/events`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Recent Runtime Recovery events |
| **Request parameters** | `limit` (1–500, default 100); `event_type` (e.g. `ChildFailed` or `runtime.ChildFailed`) |
| **Response schema** | `{ "events": [...], "count": N }` |
| **Backend service** | `dashboard_read_models.get_runtime_events` → `storage.events` |
| **Functional** | Yes; events appear after Runtime Bridge attaches and children emit |

---

### C2. `GET /monitoring/runtime/restarts`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Restart-related runtime events only |
| **Request parameters** | `limit` (1–500, default 100) |
| **Response schema** | `{ "restarts": [...], "count": N }` |
| **Backend service** | `get_runtime_restart_history` |
| **Functional** | Yes |

---

### C3. `GET /monitoring/runtime/abandoned`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | `runtime.Abandoned` events |
| **Request parameters** | `limit` (1–500, default 100) |
| **Response schema** | `{ "abandoned": [...], "count": N }` |
| **Backend service** | `get_runtime_abandoned` |
| **Functional** | Yes |

---

### C4. `GET /monitoring/runtime/supervisor`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Live supervisor snapshot + bridge stats |
| **Request parameters** | None |
| **Response schema** | `{ "bridge": …, "supervisor": …, "monitoring_enabled": bool }` |
| **Backend service** | `get_runtime_supervisor_status` → `get_runtime_bridge()`, `RuntimeSupervisor.status()` |
| **Functional** | Yes when bridge started inside Monitoring startup; `supervisor` may be null if unbound |

**Sample response:**

```json
{
  "bridge": { "running": true, "events_forwarded": 10 },
  "supervisor": { "children": {}, "started": true },
  "monitoring_enabled": true
}
```

*(Exact `bridge` / `supervisor` keys depend on `bridge.stats()` and `status().to_dict()`.)*

---

### C5. `GET /monitoring/runtime/restart-counts`

| Field | Detail |
|-------|--------|
| **HTTP method** | `GET` |
| **Purpose** | Aggregated counts of runtime event types (totals + by child) |
| **Request parameters** | `limit` (1–2000, default 500) — sample window of recent events |
| **Response schema** | `{ "totals", "by_child", "sample_size", "restart_succeeded", "restart_failed", "abandoned" }` |
| **Backend service** | `get_runtime_restart_counts` |
| **Functional** | Yes |

**Sample response:**

```json
{
  "totals": { "RestartSucceeded": 2, "ChildFailed": 1 },
  "by_child": { "listener": { "RestartSucceeded": 1 } },
  "sample_size": 50,
  "restart_succeeded": 2,
  "restart_failed": 0,
  "abandoned": 0
}
```

---

## Endpoint index

| Endpoint | Method | Auth | Provider module |
|----------|--------|------|-----------------|
| `/monitoring/health` | GET | JWT | `api_read_models` |
| `/monitoring/processors/connectivity` | GET | JWT | `api_read_models` |
| `/monitoring/ingest` | POST | Ingest token | `MonitoringService` |
| `/monitoring/summary` | GET | JWT | `dashboard_read_models` |
| `/monitoring/components` | GET | JWT | `dashboard_read_models` |
| `/monitoring/processors` | GET | JWT | `dashboard_read_models` |
| `/monitoring/alerts` | GET | JWT | `dashboard_read_models` |
| `/monitoring/alerts/{id}/acknowledge` | POST | JWT | `AlertStateManager` |
| `/monitoring/jobs` | GET | JWT | `dashboard_read_models` |
| `/monitoring/http` | GET | JWT | `dashboard_read_models` |
| `/monitoring/analytics` | GET | JWT | `dashboard_read_models` |
| `/monitoring/pipeline` | GET | JWT | `dashboard_read_models` / `MonitoringService` |
| `/monitoring/runtime/events` | GET | JWT | `dashboard_read_models` |
| `/monitoring/runtime/restarts` | GET | JWT | `dashboard_read_models` |
| `/monitoring/runtime/abandoned` | GET | JWT | `dashboard_read_models` |
| `/monitoring/runtime/supervisor` | GET | JWT | Bridge + Supervisor |
| `/monitoring/runtime/restart-counts` | GET | JWT | `dashboard_read_models` |

---

## Enablement checklist (for endpoints to be functional)

1. `MONITORING_ENABLED=true` on API process → restart  
2. Schema/bootstrap (automatic on startup when enabled, or `migrations/apply_monitoring_*.py`)  
3. Optional: `MONITORING_INGEST_ENABLED` + `MONITORING_INGEST_TOKEN` for ingest/daemons  
4. Optional: `MONITORING_HTTP_METRICS_ENABLED`, `MONITORING_JOBS_ENABLED`, `MONITORING_ALERTS_ENABLED`, `MONITORING_ANALYTICS_ENABLED`, `MONITORING_LEAP_TELEMETRY` for richer data  
5. Confirm logs: `[Startup] Monitoring service and watchdog started`

OpenAPI: available under the FastAPI app’s `/docs` when the server is running (tags **Monitoring** and **Monitoring Dashboard**).
