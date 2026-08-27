# Monitoring Phase 4 notes
#
# Delivered: MonitoringWatchdog (api + monitoring_pipeline heartbeats),
#            lifecycle_hooks (startup/shutdown), main.py wiring.
# Not delivered: daemon heartbeats, ingest API, middleware, alerts, analytics.
#
# Flags (default OFF):
#   MONITORING_ENABLED=true
# Optional:
#   MONITORING_HEARTBEAT_INTERVAL_SECONDS=30
#
# Startup order: bootstrap → Service.start → emit_startup → Watchdog.start
# Shutdown order: Watchdog.stop → emit_shutdown → Service.stop → detach
#
# Rollback: unset MONITORING_ENABLED.
