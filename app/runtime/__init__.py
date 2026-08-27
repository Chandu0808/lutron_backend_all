"""
Runtime Recovery Framework package.

Phase 1: ownership
Phase 2: health monitor detection + restart
Phase 3: restart policies + backoff / crash-loop protection
Phase 4: internal Runtime Event Bus
Phase 5: Windows Service integration (API recovery via SCM)
Phase M1: Job Object + non-daemon children + exit classification
Phase M2A: energy_logger Windows named mutex singleton
Phase M2B: ownership reconciliation on LOCK_BUSY
Phase M2.5: safe Job identity + foreign wait (no heuristic kill)
"""

from app.runtime.child_manager import ChildManager, ChildStatus
from app.runtime.events import (
    Abandoned,
    BackoffEntered,
    ChildFailed,
    ChildStarted,
    ChildStopped,
    DiagnosticsSubscriber,
    ForeignMutexDetected,
    LoggingSubscriber,
    ReconcileCompleted,
    ReconcileStarted,
    RecordingSubscriber,
    RestartFailed,
    RestartRequested,
    RestartScheduled,
    RestartStarted,
    RestartSucceeded,
    RuntimeEvent,
    RuntimeEventBus,
    ServiceStarted,
    ServiceStopped,
    ServiceStopping,
    SupervisorStarted,
    SupervisorStopped,
)
from app.runtime.exit_codes import EXPLICIT_LOCK_BUSY_EXITCODE
from app.runtime.energy_logger_mutex import (
    MutexAcquireError,
    acquire_energy_logger_mutex,
    is_mutex_object_present,
    mutex_name,
)
from app.runtime.health_monitor import HealthMonitor
from app.runtime.install_id import resolve_install_id
from app.runtime.job_object import (
    JobObjectBase,
    JobObjectError,
    NullJobObject,
    WindowsJobObject,
    create_runtime_job_object,
    detect_parent_job_state,
)
from app.runtime.lifecycle import ChildState
from app.runtime.ownership_reconcile import run_ownership_reconcile
from app.runtime.process_descriptor import ProcessDescriptor
from app.runtime.restart_backoff import BackoffConfig, BackoffStrategy
from app.runtime.restart_policy import RestartPolicyKind
from app.runtime.service_integration import (
    ExecutionMode,
    ServiceIntegration,
    is_windows_service_enabled,
)
from app.runtime.supervisor import RuntimeSupervisor, SupervisorStatus

__all__ = [
    "Abandoned",
    "BackoffConfig",
    "BackoffEntered",
    "BackoffStrategy",
    "ChildFailed",
    "ChildManager",
    "ChildStarted",
    "ChildState",
    "ChildStatus",
    "ChildStopped",
    "DiagnosticsSubscriber",
    "EXPLICIT_LOCK_BUSY_EXITCODE",
    "ExecutionMode",
    "ForeignMutexDetected",
    "HealthMonitor",
    "JobObjectBase",
    "JobObjectError",
    "LoggingSubscriber",
    "MutexAcquireError",
    "NullJobObject",
    "ProcessDescriptor",
    "ReconcileCompleted",
    "ReconcileStarted",
    "RecordingSubscriber",
    "RestartFailed",
    "RestartPolicyKind",
    "RestartRequested",
    "RestartScheduled",
    "RestartStarted",
    "RestartSucceeded",
    "RuntimeEvent",
    "RuntimeEventBus",
    "RuntimeSupervisor",
    "ServiceIntegration",
    "ServiceStarted",
    "ServiceStopped",
    "ServiceStopping",
    "SupervisorStarted",
    "SupervisorStatus",
    "SupervisorStopped",
    "WindowsJobObject",
    "acquire_energy_logger_mutex",
    "create_runtime_job_object",
    "detect_parent_job_state",
    "is_mutex_object_present",
    "is_windows_service_enabled",
    "mutex_name",
    "resolve_install_id",
    "run_ownership_reconcile",
]
