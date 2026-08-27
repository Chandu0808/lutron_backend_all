"""
API-process lifecycle emitters (Phase 4).

Only ``startup`` and ``shutdown`` LifecycleEvent types are emitted.
Uses Instrumentation only — never Storage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.monitoring import instrumentation
from app.monitoring.service import AcceptResult

logger = logging.getLogger("lutron_monitoring.lifecycle")

COMPONENT_API = "api"
EVENT_STARTUP = "startup"
EVENT_SHUTDOWN = "shutdown"


def emit_startup(
    *,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[AcceptResult]:
    """Emit API process startup lifecycle event."""
    try:
        return instrumentation.lifecycle(
            COMPONENT_API,
            EVENT_STARTUP,
            severity="info",
            detail=detail or {"source": "lifecycle_hooks"},
            fingerprint="api:startup",
        )
    except Exception as exc:
        logger.warning("[monitoring][lifecycle] startup emit failed: %s", exc)
        return None


def emit_shutdown(
    *,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[AcceptResult]:
    """Emit API process shutdown lifecycle event."""
    try:
        return instrumentation.lifecycle(
            COMPONENT_API,
            EVENT_SHUTDOWN,
            severity="info",
            detail=detail or {"source": "lifecycle_hooks"},
            fingerprint="api:shutdown",
        )
    except Exception as exc:
        logger.warning("[monitoring][lifecycle] shutdown emit failed: %s", exc)
        return None
