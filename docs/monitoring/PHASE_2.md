# Monitoring Phase 2 notes
#
# Delivered: Storage layer, Bootstrap, Registry, seed catalogs.
# Not delivered: Service, Instrumentation, API, Alert/Analytics engines, daemon hooks.
#
# Flags (default OFF):
#   MONITORING_ENABLED=true  → main.py ensures schema + bootstrap_monitoring() only
#
# Ops (without enabling the app flag):
#   python migrations/apply_monitoring_schema.py
#   python migrations/apply_monitoring_bootstrap.py
#
# Rollback:
#   - Disable MONITORING_ENABLED (app inert again)
#   - Optional non-prod: TRUNCATE monitoring dimension tables or DROP SCHEMA monitoring CASCADE
