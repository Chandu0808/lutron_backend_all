# Monitoring Phase 3 notes
#
# Delivered: MonitoringService, events (Event Builder), Instrumentation SDK, DropPolicy.
# Not delivered: daemon hooks, API/ingest, heartbeat timers, middleware, alerts, analytics.
#
# Flags (default OFF):
#   MONITORING_ENABLED=true → schema ensure + bootstrap + MonitoringService start/stop
#
# Synthetic harness (example):
#   bootstrap_monitoring()
#   svc = MonitoringService(); svc.start()
#   instrumentation.attach(svc)
#   instrumentation.heartbeat("api", "up")
#   ... wait for queue drain ...
#   svc.stop(); instrumentation.detach()
#
# Rollback: unset MONITORING_ENABLED; service is not started.
