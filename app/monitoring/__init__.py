"""
Monitoring package (Phase 4: API watchdog + lifecycle hooks).

Daemon producers / Monitoring API are later phases.
"""

from app.monitoring.bootstrap import (
    BootstrapReport,
    bootstrap_monitoring,
    ensure_dimensions,
    reload_registry,
)
from app.monitoring.flags import is_monitoring_enabled
from app.monitoring.registry import RegistrySnapshot, clear_registry, get_registry
from app.monitoring.service import (
    AcceptResult,
    MonitoringService,
    PipelineStatus,
    get_monitoring_service,
    set_monitoring_service,
)
from app.monitoring.storage import MonitoringStorage
from app.monitoring.watchdog import (
    MonitoringWatchdog,
    get_monitoring_watchdog,
    set_monitoring_watchdog,
)

__all__ = [
    "AcceptResult",
    "BootstrapReport",
    "MonitoringService",
    "MonitoringStorage",
    "MonitoringWatchdog",
    "PipelineStatus",
    "RegistrySnapshot",
    "bootstrap_monitoring",
    "clear_registry",
    "ensure_dimensions",
    "get_monitoring_service",
    "get_monitoring_watchdog",
    "get_registry",
    "is_monitoring_enabled",
    "reload_registry",
    "set_monitoring_service",
    "set_monitoring_watchdog",
]
