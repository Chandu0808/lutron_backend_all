"""Idempotent seed catalogs for Monitoring Bootstrap (Phase 2)."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class ComponentSeed(TypedDict):
    code: str
    kind: str
    display_name: str


class JobSeed(TypedDict):
    job_key: str
    component_code: str
    display_name: str


class MetricSeed(TypedDict):
    metric_key: str
    value_type: str
    description: str


class AlertRuleSeed(TypedDict):
    code: str
    display_name: str
    severity: str
    rule_type: str
    description: str
    config_json: Dict[str, Any]


COMPONENT_SEEDS: List[ComponentSeed] = [
    {"code": "api", "kind": "process", "display_name": "FastAPI API Process"},
    {"code": "listener", "kind": "process", "display_name": "LEAP Listener"},
    {
        "code": "energy_logger",
        "kind": "process",
        "display_name": "Energy Logger",
    },
    {
        "code": "loadcontroller_listener",
        "kind": "process",
        "display_name": "Load Controller Listener",
    },
    {"code": "scheduler", "kind": "process", "display_name": "APScheduler"},
    {
        "code": "monitoring_pipeline",
        "kind": "process",
        "display_name": "Monitoring Pipeline",
    },
    {"code": "alert_engine", "kind": "process", "display_name": "Alert Engine"},
    {
        "code": "analytics_engine",
        "kind": "process",
        "display_name": "Analytics Engine",
    },
    {"code": "database", "kind": "dependency", "display_name": "PostgreSQL"},
    {
        "code": "certificates",
        "kind": "dependency",
        "display_name": "LAP/LEAP Certificates",
    },
]

JOB_SEEDS: List[JobSeed] = [
    {
        "job_key": "device_refresh",
        "component_code": "scheduler",
        "display_name": "Device Refresh",
    },
    {
        "job_key": "alert_reconcile",
        "component_code": "scheduler",
        "display_name": "Alert Reconcile (clear + area_path)",
    },
    {
        "job_key": "daily_data_backfill",
        "component_code": "scheduler",
        "display_name": "Daily Data Backfill",
    },
    {
        "job_key": "occupancy_reconciliation",
        "component_code": "scheduler",
        "display_name": "Occupancy Logs Reconciliation",
    },
    {
        "job_key": "log_energy_stats",
        "component_code": "energy_logger",
        "display_name": "Energy Logger 15-minute Stats",
    },
    {
        "job_key": "monitoring_analytics_rollup",
        "component_code": "analytics_engine",
        "display_name": "Monitoring Analytics Rollup",
    },
    {
        "job_key": "monitoring_retention",
        "component_code": "analytics_engine",
        "display_name": "Monitoring Retention",
    },
    {
        "job_key": "monitoring_alert_engine",
        "component_code": "alert_engine",
        "display_name": "Monitoring Alert Engine",
    },
]

METRIC_SEEDS: List[MetricSeed] = [
    {
        "metric_key": "alerts.unmapped_count",
        "value_type": "gauge",
        "description": "Active driver/device alerts missing area_path (hidden from LMS UI)",
    },
    {
        "metric_key": "leap.max_connections",
        "value_type": "gauge",
        "description": "Processor LEAP max concurrent certificate clients (Lutron=10)",
    },
    {
        "metric_key": "leap.active_estimated",
        "value_type": "gauge",
        "description": "LMS-estimated active LEAP connections to a processor",
    },
    {
        "metric_key": "leap.connection_pressure_active",
        "value_type": "gauge",
        "description": "Same as active_estimated; used for ratio-of-capacity alerts",
    },
    {
        "metric_key": "leap.connection_saturated",
        "value_type": "gauge",
        "description": "1 when max_clients reject or active at cap; else 0",
    },
    {
        "metric_key": "leap.max_clients_reject_total",
        "value_type": "counter",
        "description": "Cumulative LEAP max-clients / 503 rejects observed by LMS",
    },
    {
        "metric_key": "leap.connect_success_total",
        "value_type": "counter",
        "description": "Successful LEAP TLS connects",
    },
    {
        "metric_key": "leap.connect_fail_total",
        "value_type": "counter",
        "description": "Failed LEAP TLS connects",
    },
    {
        "metric_key": "leap.connect_time_ms",
        "value_type": "gauge",
        "description": "LEAP TLS connect duration (ms)",
    },
    {
        "metric_key": "pipeline.queue_length",
        "value_type": "gauge",
        "description": "Monitoring ingress queue depth",
    },
    {
        "metric_key": "pipeline.dropped_total",
        "value_type": "counter",
        "description": "Events dropped by pipeline backpressure/validation",
    },
    {
        "metric_key": "pipeline.writer_error_total",
        "value_type": "counter",
        "description": "Pipeline storage write failures",
    },
    {
        "metric_key": "pipeline.write_retry_total",
        "value_type": "counter",
        "description": "Pipeline transient write retries",
    },
    {
        "metric_key": "pipeline.last_flush_epoch",
        "value_type": "gauge",
        "description": "Unix epoch of last successful HTTP agg flush",
    },
    {
        "metric_key": "analytics.lag_seconds",
        "value_type": "gauge",
        "description": "Analytics rollup lag in seconds",
    },
    {
        "metric_key": "alert.eval_lag_seconds",
        "value_type": "gauge",
        "description": "Seconds since last alert evaluation completed",
    },
    {
        "metric_key": "alert.notify_fail_total",
        "value_type": "counter",
        "description": "Alert notification delivery failures",
    },
    {
        "metric_key": "db.pool.checked_out",
        "value_type": "gauge",
        "description": "SQLAlchemy pool connections checked out",
    },
    {
        "metric_key": "db.pool.size",
        "value_type": "gauge",
        "description": "Configured SQLAlchemy pool size",
    },
    {
        "metric_key": "listener.reconnect_total",
        "value_type": "counter",
        "description": "LEAP listener reconnect attempts",
    },
    {
        "metric_key": "energy_logger.gap_fill_rows",
        "value_type": "counter",
        "description": "Rows written by energy gap fill",
    },
    {
        "metric_key": "energy_logger.lock_skip_total",
        "value_type": "counter",
        "description": "Energy logger ticks skipped due to process lock",
    },
    {
        "metric_key": "certs.days_to_expiry",
        "value_type": "gauge",
        "description": "Days until LEAP client certificate expiry",
    },
    {
        "metric_key": "leap.stream.area.last_event_epoch",
        "value_type": "gauge",
        "description": "Unix epoch of last /area/status event per processor",
    },
    {
        "metric_key": "leap.stream.zone.last_event_epoch",
        "value_type": "gauge",
        "description": "Unix epoch of last /zone/status event per processor",
    },
    {
        "metric_key": "leap.stream.lc.last_event_epoch",
        "value_type": "gauge",
        "description": "Unix epoch of last /loadcontroller/status event per processor",
    },
    {
        "metric_key": "leap.stream.area.subscribed",
        "value_type": "gauge",
        "description": "1 if area status stream is subscribed",
    },
    {
        "metric_key": "leap.stream.zone.subscribed",
        "value_type": "gauge",
        "description": "1 if zone status stream is subscribed",
    },
    {
        "metric_key": "leap.stream.lc.subscribed",
        "value_type": "gauge",
        "description": "1 if loadcontroller status stream is subscribed",
    },
    {
        "metric_key": "component.up",
        "value_type": "gauge",
        "description": "Component up indicator (1=up, 0=down)",
    },
    # Phase 11 — analytics rollup target metrics (written only by Analytics Engine)
    {
        "metric_key": "analytics.http.request_count",
        "value_type": "counter",
        "description": "HTTP request count rollup",
    },
    {
        "metric_key": "analytics.http.avg_latency_ms",
        "value_type": "gauge",
        "description": "HTTP average latency rollup (ms)",
    },
    {
        "metric_key": "analytics.http.max_latency_ms",
        "value_type": "gauge",
        "description": "HTTP max latency rollup (ms)",
    },
    {
        "metric_key": "analytics.http.error_rate",
        "value_type": "gauge",
        "description": "HTTP 5xx error rate rollup",
    },
    {
        "metric_key": "analytics.job.success_count",
        "value_type": "counter",
        "description": "Job success count rollup",
    },
    {
        "metric_key": "analytics.job.failure_count",
        "value_type": "counter",
        "description": "Job failure count rollup",
    },
    {
        "metric_key": "analytics.job.avg_duration_ms",
        "value_type": "gauge",
        "description": "Average job duration rollup (ms)",
    },
    {
        "metric_key": "analytics.leap.avg_rtt_ms",
        "value_type": "gauge",
        "description": "Average LEAP ping RTT rollup (ms)",
    },
    {
        "metric_key": "analytics.leap.max_rtt_ms",
        "value_type": "gauge",
        "description": "Max LEAP ping RTT rollup (ms)",
    },
    {
        "metric_key": "analytics.alert.count",
        "value_type": "counter",
        "description": "Alert instances opened in bucket",
    },
]

# Product catalog rules: INSERT enabled=true. Re-seed does not overwrite enabled.
ALERT_RULE_SEEDS: List[AlertRuleSeed] = [
    {
        "code": "component_heartbeat_stale",
        "display_name": "Component Heartbeat Stale",
        "severity": "critical",
        "rule_type": "heartbeat_stale",
        "description": "Component health heartbeat older than threshold",
        "config_json": {"stale_seconds": 90},
    },
    {
        "code": "processor_leap_down",
        "display_name": "Processor LEAP Down",
        "severity": "critical",
        "rule_type": "connectivity_down",
        "description": "Processor LEAP connectivity status is down",
        "config_json": {"min_down_seconds": 60},
    },
    {
        "code": "processor_leap_connection_saturation",
        "display_name": "Processor LEAP Connection Saturation",
        "severity": "critical",
        "rule_type": "leap_connection_saturation",
        "description": (
            "Processor rejected LEAP clients (max concurrent connections / 503). "
            "Lutron certificate client limit is 10."
        ),
        "config_json": {
            "max_connections": 10,
            "lookback_minutes": 15,
            "metric_key": "leap.connection_saturated",
            "threshold": 1,
            "operator": "gte",
        },
    },
    {
        "code": "processor_leap_connection_pressure",
        "display_name": "Processor LEAP Connection Pressure",
        "severity": "warning",
        "rule_type": "metric_threshold",
        "description": (
            "LMS-estimated active LEAP connections are at or above 80% of the "
            "processor max (10)."
        ),
        "config_json": {
            "metric_key": "leap.connection_pressure_active",
            "capacity_metric_key": "leap.max_connections",
            "max_ratio_of_capacity": 0.8,
        },
    },
    {
        "code": "job_consecutive_failures",
        "display_name": "Job Consecutive Failures",
        "severity": "warning",
        "rule_type": "job_failures",
        "description": "Monitored system job failed N consecutive times",
        "config_json": {"consecutive_failures": 3},
    },
    {
        "code": "http_5xx_rate",
        "display_name": "HTTP 5xx Rate High",
        "severity": "warning",
        "rule_type": "http_error_rate",
        "description": "HTTP 5xx rate exceeds threshold in window",
        "config_json": {
            "window_minutes": 5,
            "max_error_rate": 0.05,
            "exclude_route_prefixes": [
                "/docs",
                "/redoc",
                "/openapi.json",
                "/monitoring",
            ],
        },
    },
    {
        "code": "http_5xx_per_route",
        "display_name": "HTTP 5xx Per Route",
        "severity": "warning",
        "rule_type": "http_error_rate",
        "description": "Per-route HTTP 5xx rate (excludes docs/monitoring; not 4xx)",
        "config_json": {
            "window_minutes": 5,
            "max_error_rate": 0.05,
            "min_errors": 3,
            "per_route": True,
            "exclude_route_prefixes": [
                "/docs",
                "/redoc",
                "/openapi.json",
                "/monitoring",
            ],
        },
    },
    {
        "code": "ping_success_rate_low",
        "display_name": "LEAP Ping Success Rate Low",
        "severity": "warning",
        "rule_type": "ping_success_rate",
        "description": "LEAP ping success rate below threshold",
        "config_json": {"window_minutes": 15, "min_success_rate": 0.8},
    },
    {
        "code": "pipeline_degraded",
        "display_name": "Monitoring Pipeline Degraded",
        "severity": "critical",
        "rule_type": "component_status",
        "description": "monitoring_pipeline component not healthy",
        "config_json": {"component_code": "monitoring_pipeline"},
    },
    {
        "code": "db_pool_pressure",
        "display_name": "Database Pool Pressure",
        "severity": "warning",
        "rule_type": "metric_threshold",
        "description": "DB pool checked_out above pressure threshold",
        "config_json": {
            "metric_key": "db.pool.checked_out",
            "max_ratio_of_capacity": 0.9,
        },
    },
    {
        "code": "runtime_foreign_mutex",
        "display_name": "Runtime Foreign Mutex Holder",
        "severity": "warning",
        "rule_type": "runtime_event",
        "description": (
            "Energy logger mutex held by a process that is not a proven "
            "Runtime Job member (M2.5 wait/alert path)"
        ),
        "config_json": {
            "runtime_event": "ForeignMutexDetected",
            "window_minutes": 15,
            "count_threshold": 1,
            "fingerprint_prefix": "runtime_foreign_mutex",
        },
    },
    {
        "code": "leap_stream_health",
        "display_name": "LEAP Stream Health",
        "severity": "critical",
        "rule_type": "leap_stream_health",
        "description": (
            "Area, zone, or loadcontroller subscribe stream down or silent "
            "while processor connectivity is up"
        ),
        "config_json": {
            "streams": ["area", "zone", "loadcontroller"],
            "stale_seconds": 180,
        },
    },
    {
        "code": "certs_expiry_soon",
        "display_name": "LEAP Certificate Expiry Soon",
        "severity": "warning",
        "rule_type": "metric_threshold",
        "description": "LEAP client certificate expires within 30 days",
        "config_json": {
            "metric_key": "certs.days_to_expiry",
            "threshold": 30,
            "operator": "lt",
        },
    },
    {
        "code": "alerts_unmapped",
        "display_name": "Unmapped Driver Alerts",
        "severity": "warning",
        "rule_type": "metric_threshold",
        "description": "Driver/device alerts missing area_path",
        "config_json": {
            "metric_key": "alerts.unmapped_count",
            "threshold": 0,
            "operator": "gt",
        },
    },
    {
        "code": "runtime_child_failed",
        "display_name": "Runtime Child Failed",
        "severity": "critical",
        "rule_type": "runtime_event",
        "description": "Supervised child process crashed recently",
        "config_json": {
            "runtime_event": "ChildFailed",
            "window_minutes": 10,
            "count_threshold": 1,
        },
    },
    {
        "code": "runtime_restart_storm",
        "display_name": "Runtime Restart Storm",
        "severity": "critical",
        "rule_type": "runtime_event",
        "description": "Child failed repeatedly in a short window",
        "config_json": {
            "runtime_event": "ChildFailed",
            "window_minutes": 10,
            "count_threshold": 3,
        },
    },
    {
        "code": "runtime_abandoned",
        "display_name": "Runtime Child Abandoned",
        "severity": "critical",
        "rule_type": "runtime_event",
        "description": "Supervisor abandoned a child after max restarts",
        "config_json": {
            "runtime_event": "Abandoned",
            "window_minutes": 30,
            "count_threshold": 1,
        },
    },
]
