# Monitoring Phase 12 — Dashboard REST API

## Delivered

- `app/api/routes/monitoring_dashboard.py`
- `app/monitoring/dashboard_read_models.py`
- `app/schemas/monitoring_dashboard.py`
- Wired in `api_router.py` (dashboard router before Phase 5 for `/jobs` `/pipeline`)

## Endpoints (JWT)

| Method | Path |
|--------|------|
| GET | `/monitoring/summary` |
| GET | `/monitoring/components` |
| GET | `/monitoring/processors` |
| GET | `/monitoring/alerts` |
| POST | `/monitoring/alerts/{id}/acknowledge` |
| GET | `/monitoring/jobs` |
| GET | `/monitoring/http` |
| GET | `/monitoring/analytics` |
| GET | `/monitoring/pipeline` |

## Auth

LMS JWT via `get_current_user`. No ingest token.

## Writes

Alert acknowledge only.

## Rollback

Remove `monitoring_dashboard` router include (Phase 5 routes remain).

## Not delivered

Frontend, charts, websockets/SSE, notifications, rule editing, retention.
