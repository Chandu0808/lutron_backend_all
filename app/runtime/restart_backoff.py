"""
Restart backoff and crash-loop counters (Phase 3).

Uses monotonic time for intervals.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class BackoffStrategy(str, Enum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


# Defaults (env overrides applied in from_env)
DEFAULT_MAX_RESTARTS = 5
DEFAULT_RESTART_DELAY_SECONDS = 2.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0
DEFAULT_FAILURE_WINDOW_SECONDS = 600.0
DEFAULT_STABLE_RESET_SECONDS = 120.0
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_MULTIPLIER = 2.0


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class BackoffConfig:
    """Immutable backoff / retry configuration."""

    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    base_delay_seconds: float = DEFAULT_RESTART_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    multiplier: float = DEFAULT_MULTIPLIER
    max_restarts: int = DEFAULT_MAX_RESTARTS
    failure_window_seconds: float = DEFAULT_FAILURE_WINDOW_SECONDS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    reset_after_stable_seconds: float = DEFAULT_STABLE_RESET_SECONDS

    @classmethod
    def from_env(cls) -> "BackoffConfig":
        strategy_raw = (os.getenv("RUNTIME_BACKOFF") or "exponential").strip().lower()
        try:
            strategy = BackoffStrategy(strategy_raw)
        except ValueError:
            strategy = BackoffStrategy.EXPONENTIAL
        return cls(
            strategy=strategy,
            base_delay_seconds=_env_float(
                "RUNTIME_RESTART_DELAY_SECONDS", DEFAULT_RESTART_DELAY_SECONDS
            ),
            max_delay_seconds=_env_float(
                "RUNTIME_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF_SECONDS
            ),
            max_restarts=_env_int("RUNTIME_MAX_RESTARTS", DEFAULT_MAX_RESTARTS),
            failure_window_seconds=_env_float(
                "RUNTIME_FAILURE_WINDOW_SECONDS", DEFAULT_FAILURE_WINDOW_SECONDS
            ),
        )

    def next_delay(self, attempt_index: int) -> float:
        """
        Delay before the next restart.

        ``attempt_index`` is 0-based (0 = first restart after a failure).
        """
        if attempt_index < 0:
            attempt_index = 0
        base = max(0.0, float(self.base_delay_seconds))
        cap = max(base, float(self.max_delay_seconds))
        if self.strategy == BackoffStrategy.FIXED:
            delay = base
        elif self.strategy == BackoffStrategy.LINEAR:
            delay = base * (attempt_index + 1)
        else:
            # exponential
            delay = base * (self.multiplier ** attempt_index)
        return min(delay, cap)


@dataclass
class BackoffState:
    """Mutable per-child backoff / failure-window state."""

    consecutive_failures: int = 0
    failure_times: List[float] = field(default_factory=list)
    backoff_until: Optional[float] = None
    abandoned_until: Optional[float] = None
    running_since: Optional[float] = None
    last_delay: float = 0.0

    def failures_in_window(self, now: float, window_seconds: float) -> int:
        cutoff = now - max(0.0, window_seconds)
        self.failure_times = [t for t in self.failure_times if t >= cutoff]
        return len(self.failure_times)

    def record_failure(self, now: float, window_seconds: float) -> int:
        self.consecutive_failures += 1
        self.failure_times.append(now)
        self.running_since = None
        return self.failures_in_window(now, window_seconds)

    def schedule_backoff(self, now: float, delay: float) -> float:
        delay = max(0.0, float(delay))
        self.last_delay = delay
        self.backoff_until = now + delay
        return self.backoff_until

    def backoff_due(self, now: float) -> bool:
        if self.backoff_until is None:
            return True
        return now >= self.backoff_until

    def clear_backoff(self) -> None:
        self.backoff_until = None

    def mark_abandoned(self, now: float, cooldown_seconds: float) -> None:
        self.abandoned_until = now + max(0.0, cooldown_seconds)
        self.backoff_until = None

    def is_abandoned(self, now: float) -> bool:
        if self.abandoned_until is None:
            return False
        if now >= self.abandoned_until:
            # Cooldown elapsed — clear abandon flag (manual/policy may retry)
            self.abandoned_until = None
            self.failure_times.clear()
            self.consecutive_failures = 0
            return False
        return True

    def clear_abandon(self) -> None:
        self.abandoned_until = None

    def note_running(self, now: float) -> None:
        if self.running_since is None:
            self.running_since = now

    def maybe_reset_after_stable(
        self, now: float, stable_seconds: float
    ) -> bool:
        """Reset failure counters after stable RUNNING period."""
        if self.running_since is None:
            return False
        if (now - self.running_since) < max(0.0, stable_seconds):
            return False
        self.consecutive_failures = 0
        self.failure_times.clear()
        self.clear_abandon()
        self.clear_backoff()
        # Keep running_since so we do not thrash; caller may refresh
        return True

    def reset_all(self) -> None:
        self.consecutive_failures = 0
        self.failure_times.clear()
        self.backoff_until = None
        self.abandoned_until = None
        self.running_since = None
        self.last_delay = 0.0


def monotonic_now() -> float:
    return time.monotonic()
