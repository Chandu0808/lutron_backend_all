# Monitoring Phase 1 notes
#
# Schema is applied manually (or via migrations/apply_monitoring_schema.py).
# MONITORING_* flags default OFF. ensure_monitoring_schema is NOT called from
# app/main.py in Phase 1 (startup wiring is a later phase behind MONITORING_ENABLED).
#
# Apply:
#   python migrations/apply_monitoring_schema.py
#
# Rollback (non-production, approved only):
#   DROP SCHEMA monitoring CASCADE;
#
# MVMS / runtime writers begin in later roadmap phases.
