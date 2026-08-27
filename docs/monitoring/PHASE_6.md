# Monitoring Phase 6 notes
#
# Delivered:
#   RemoteClient (HTTP ingest)
#   daemon_heartbeat helper
#   Instrumentation remote routing
#   Heartbeats from listener / energy_logger / loadcontroller_listener
#
# Not delivered: connectivity, LEAP ping, job instrumentation, Phase 7+.
#
# Required env on API + daemons:
#   MONITORING_ENABLED=true
#   MONITORING_INGEST_ENABLED=true
#   MONITORING_INGEST_TOKEN=<secret>
# Optional:
#   MONITORING_INGEST_URL=http://127.0.0.1:8000/monitoring/ingest
#   MONITORING_REMOTE_TIMEOUT=2
#   MONITORING_REMOTE_RETRIES=2
#   MONITORING_REMOTE_BACKOFF=0.5
#   MONITORING_HEARTBEAT_INTERVAL_SECONDS=30
#
# Rollback: unset MONITORING_INGEST_ENABLED (or MONITORING_ENABLED).
