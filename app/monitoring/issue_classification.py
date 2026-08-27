"""
Issue classification helpers (Phase 2/3).

Maps alert rule_type / context → product source_type and error labels.
Does not invent recovery or exception file/line data.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set


# Product-facing source types for Application Issues.
SOURCE_PROCESSOR = "PROCESSOR"
SOURCE_APPLICATION = "APPLICATION"
SOURCE_RUNTIME = "RUNTIME"
SOURCE_DATABASE = "DATABASE"
SOURCE_NETWORK = "NETWORK"
SOURCE_DEPENDENCY = "DEPENDENCY"
SOURCE_UNKNOWN = "UNKNOWN"

# Seeded / known alert rule_types (classification coverage).
KNOWN_RULE_TYPES: Set[str] = frozenset(
    {
        "heartbeat_stale",
        "connectivity_down",
        "job_failures",
        "http_error_rate",
        "ping_success_rate",
        "component_status",
        "metric_threshold",
        "runtime_event",
        "leap_connection_saturation",
        "leap_stream_health",
    }
)

# Supervised runtime child component codes (monitoring seeds).
_RUNTIME_COMPONENT_CODES = frozenset(
    {
        "listener",
        "energy_logger",
        "loadcontroller_listener",
    }
)

_APPLICATION_COMPONENT_CODES = frozenset(
    {
        "api",
        "scheduler",
        "monitoring_pipeline",
        "alert_engine",
        "analytics_engine",
    }
)


def classify_source_type(
    *,
    rule_type: Optional[str],
    rule_code: Optional[str] = None,
    component_code: Optional[str] = None,
    processor_id: Optional[int] = None,
) -> str:
    """
    Derive a conservative source_type for Superadmin Issues.

    Prefer UNKNOWN over a false diagnosis when evidence is weak.
    """
    rt = (rule_type or "").strip().lower()
    code = (rule_code or "").strip().lower()
    comp = (component_code or "").strip().lower()

    if rt == "connectivity_down" or code == "processor_leap_down":
        return SOURCE_PROCESSOR

    if rt == "leap_connection_saturation" or code in (
        "processor_leap_connection_saturation",
        "processor_leap_connection_pressure",
    ):
        return SOURCE_PROCESSOR

    if rt == "leap_stream_health" or code == "leap_stream_health":
        return SOURCE_PROCESSOR

    if rt == "ping_success_rate" or code == "ping_success_rate_low":
        return SOURCE_PROCESSOR

    if rt == "runtime_event" or code.startswith("runtime_"):
        return SOURCE_RUNTIME

    if rt == "heartbeat_stale":
        if comp in _RUNTIME_COMPONENT_CODES:
            return SOURCE_RUNTIME
        if comp in _APPLICATION_COMPONENT_CODES:
            return SOURCE_APPLICATION
        if comp == "database":
            return SOURCE_DATABASE
        if comp == "certificates":
            return SOURCE_DEPENDENCY
        return SOURCE_UNKNOWN

    if rt == "job_failures":
        if comp == "database":
            return SOURCE_DATABASE
        return SOURCE_APPLICATION

    if rt == "http_error_rate":
        return SOURCE_APPLICATION

    if rt == "component_status":
        if comp in _RUNTIME_COMPONENT_CODES:
            return SOURCE_RUNTIME
        if comp in _APPLICATION_COMPONENT_CODES or code == "pipeline_degraded":
            return SOURCE_APPLICATION
        if comp == "database":
            return SOURCE_DATABASE
        return SOURCE_UNKNOWN

    if rt == "metric_threshold":
        if code == "db_pool_pressure" or code.startswith("db_") or "db.pool" in code:
            return SOURCE_DATABASE
        if code.startswith("cert") or "certs." in code:
            return SOURCE_DEPENDENCY
        if "unmapped" in code or code.startswith("alerts_"):
            return SOURCE_APPLICATION
        return SOURCE_UNKNOWN

    # Weak fallback: processor_id alone without a known rule is not enough.
    if rt and rt not in KNOWN_RULE_TYPES:
        return SOURCE_UNKNOWN

    return SOURCE_UNKNOWN


def classify_error_type(
    *,
    rule_type: Optional[str],
    source_type: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Optional product error type label. Returns None when unknown.
    Does not invent Python exception types.
    """
    detail = detail or {}
    for key in ("error_type", "exception_type", "error_class"):
        raw = detail.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    rt = (rule_type or "").strip().lower()
    if rt == "connectivity_down":
        return "Processor Connection"
    if rt == "leap_connection_saturation":
        return "Processor Connection Saturation"
    if rt == "leap_stream_health":
        return "LEAP Stream"
    if rt == "ping_success_rate":
        return "Processor Ping"
    if rt == "heartbeat_stale":
        return "Component Heartbeat Stale"
    if rt == "job_failures":
        return "Job Failure"
    if rt == "runtime_event":
        return "Process Runtime"
    if rt == "http_error_rate":
        return "HTTP Error Rate"
    if rt == "component_status":
        return "Component Status"
    if rt == "metric_threshold":
        if source_type == SOURCE_DATABASE:
            return "Database Metric"
        if source_type == SOURCE_DEPENDENCY:
            return "Certificate Expiry"
        if source_type == SOURCE_APPLICATION:
            return "Application Metric"
        return "Metric Threshold"
    if source_type == SOURCE_PROCESSOR:
        return "Processor Connection"
    if source_type == SOURCE_RUNTIME:
        return "Process Runtime"
    if source_type == SOURCE_DATABASE:
        return "Database"
    return None


def source_id_for(
    *,
    source_type: str,
    processor_id: Optional[int],
    component_code: Optional[str],
) -> Optional[str]:
    if source_type == SOURCE_PROCESSOR and processor_id is not None:
        return str(processor_id)
    if component_code:
        return component_code
    if processor_id is not None:
        return str(processor_id)
    return None
