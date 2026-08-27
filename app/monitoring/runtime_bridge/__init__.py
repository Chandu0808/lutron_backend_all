"""
Runtime → Monitoring observer bridge.

Subscribes to the Runtime Event Bus and records LifecycleEvents via
MonitoringService. Runtime never imports this package.
"""

from app.monitoring.runtime_bridge.subscriber import (
    RuntimeMonitoringSubscriber,
    get_runtime_bridge,
    start_runtime_bridge,
    stop_runtime_bridge,
)

__all__ = [
    "RuntimeMonitoringSubscriber",
    "get_runtime_bridge",
    "start_runtime_bridge",
    "stop_runtime_bridge",
]
