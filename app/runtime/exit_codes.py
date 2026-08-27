"""
Explicit Runtime child exit codes (Phase M1).

LOCK_BUSY must never be inferred from generic exitcode 1 (force-kill collision).
Children that fail singleton/lock acquire must exit with EXPLICIT_LOCK_BUSY_EXITCODE.
"""

from __future__ import annotations

# Mutex-based lock reporting (M2A); children exit with this on mutex denial.
EXPLICIT_LOCK_BUSY_EXITCODE = 78
