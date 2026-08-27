"""
Runtime child lifecycle states.

Phase 3 adds BACKOFF and ABANDONED for policy-gated restarts.
"""

from __future__ import annotations

from enum import Enum


class ChildState(str, Enum):
    """Lifecycle state for a supervised child."""

    CREATED = "CREATED"
    REGISTERED = "REGISTERED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RESTARTING = "RESTARTING"
    BACKOFF = "BACKOFF"
    ABANDONED = "ABANDONED"
    # Phase M2B — ownership investigation after LOCK_BUSY (exit 78)
    RECONCILING = "RECONCILING"
    ADOPTING = "ADOPTING"
