"""
Restart policy evaluation (Phase 3).

Pure decisions — no Process I/O, no delays.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union


class RestartPolicyKind(str, Enum):
    NEVER = "never"
    ALWAYS = "always"
    ON_FAILURE = "on_failure"
    LIMITED_RETRIES = "limited_retries"
    MANUAL_ONLY = "manual_only"


class ExitClass(str, Enum):
    CRASH = "crash"
    CLEAN_IDLE = "clean_idle"
    LOCK_BUSY = "lock_busy"
    INTENTIONAL_STOP = "intentional_stop"
    UNKNOWN = "unknown"


class RestartAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ABANDON = "abandon"


@dataclass(frozen=True)
class RestartContext:
    """Inputs for policy evaluation."""

    exit_class: ExitClass
    enabled: bool
    intentional_stop: bool
    supervisor_shutting_down: bool
    failures_in_window: int
    max_restarts: int
    already_abandoned: bool = False


@dataclass(frozen=True)
class RestartDecision:
    action: RestartAction
    reason: str

    @property
    def allowed(self) -> bool:
        return self.action == RestartAction.ALLOW


_POLICY_ALIASES = {
    "none": RestartPolicyKind.ON_FAILURE,  # Phase 1 placeholder → default
    "never": RestartPolicyKind.NEVER,
    "always": RestartPolicyKind.ALWAYS,
    "on_failure": RestartPolicyKind.ON_FAILURE,
    "onfailure": RestartPolicyKind.ON_FAILURE,
    "limited_retries": RestartPolicyKind.LIMITED_RETRIES,
    "limitedretries": RestartPolicyKind.LIMITED_RETRIES,
    "manual_only": RestartPolicyKind.MANUAL_ONLY,
    "manualonly": RestartPolicyKind.MANUAL_ONLY,
}


def parse_restart_policy(
    value: Optional[Union[str, RestartPolicyKind]],
) -> RestartPolicyKind:
    """Parse descriptor policy; default OnFailure."""
    if value is None:
        return RestartPolicyKind.ON_FAILURE
    if isinstance(value, RestartPolicyKind):
        return value
    key = str(value).strip().lower()
    if not key:
        return RestartPolicyKind.ON_FAILURE
    if key in _POLICY_ALIASES:
        return _POLICY_ALIASES[key]
    raise ValueError(f"Unknown restart_policy: {value!r}")


def evaluate_restart_policy(
    kind: RestartPolicyKind,
    context: RestartContext,
) -> RestartDecision:
    """
    Decide whether an automatic restart may proceed.

    LimitedRetries / OnFailure / Always enforce max_restarts in the failure
    window (crash-loop protection). Never / ManualOnly never auto-restart.
    """
    if context.supervisor_shutting_down:
        return RestartDecision(RestartAction.DENY, "supervisor_shutting_down")
    if not context.enabled:
        return RestartDecision(RestartAction.DENY, "disabled")
    if context.intentional_stop:
        return RestartDecision(RestartAction.DENY, "intentional_stop")
    if context.already_abandoned:
        return RestartDecision(RestartAction.DENY, "already_abandoned")

    if kind == RestartPolicyKind.NEVER:
        return RestartDecision(RestartAction.DENY, "policy_never")
    if kind == RestartPolicyKind.MANUAL_ONLY:
        return RestartDecision(RestartAction.DENY, "policy_manual_only")

    # Exit-class gate
    if kind == RestartPolicyKind.ALWAYS:
        # Always: restart on crash and clean idle; never on intentional (above)
        # or lock_busy (avoid spawn storms against a live lock).
        if context.exit_class == ExitClass.LOCK_BUSY:
            return RestartDecision(RestartAction.DENY, "lock_busy")
        if context.exit_class == ExitClass.INTENTIONAL_STOP:
            return RestartDecision(RestartAction.DENY, "intentional_stop")
    elif kind in (
        RestartPolicyKind.ON_FAILURE,
        RestartPolicyKind.LIMITED_RETRIES,
    ):
        if context.exit_class != ExitClass.CRASH:
            if context.exit_class == ExitClass.UNKNOWN:
                # Conservative: treat unknown as failure
                pass
            else:
                return RestartDecision(
                    RestartAction.DENY,
                    f"exit_class_{context.exit_class.value}",
                )
    else:
        return RestartDecision(RestartAction.DENY, f"unsupported_policy_{kind}")

    # Crash-loop / limited retries gate (shared for Always / OnFailure / Limited)
    # Allow up to max_restarts automatic restarts; abandon on the next failure.
    if context.failures_in_window > context.max_restarts:
        return RestartDecision(
            RestartAction.ABANDON,
            f"max_restarts_exceeded:{context.failures_in_window}>{context.max_restarts}",
        )

    return RestartDecision(RestartAction.ALLOW, f"policy_{kind.value}")
