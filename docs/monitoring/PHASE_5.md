# Monitoring Phase 5 notes
#
# Delivered:
#   POST /monitoring/ingest (shared secret)
#   GET  /monitoring/health|pipeline|processors/connectivity|jobs (JWT)
#   api_read_models, ingest parser, monitoring_auth dependency
#
# Not delivered: daemon remote_client, listener/energy/LC hooks, middleware,
#                alerts, analytics, dashboard UI.
#
# Required env for ingest demo:
#   MONITORING_ENABLED=true
#   MONITORING_INGEST_ENABLED=true
#   MONITORING_INGEST_TOKEN=<secret>
#
# Synthetic heartbeat example:
#   curl -X POST http://localhost:8000/monitoring/ingest \
#     -H "Content-Type: application/json" \
#     -H "X-Monitoring-Ingest-Token: <secret>" \
#     -d "{\"event_type\":\"heartbeat\",\"component_code\":\"listener\",\"status\":\"up\"}"
#
# Then (with LMS JWT):
#   GET /monitoring/health
#
# Rollback: unset MONITORING_ENABLED / MONITORING_INGEST_ENABLED.
