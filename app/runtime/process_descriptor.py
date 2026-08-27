"""
Immutable process descriptor.

Metadata only — never holds a multiprocessing.Process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple, Union

from app.runtime.restart_policy import RestartPolicyKind, parse_restart_policy


@dataclass(frozen=True)
class ProcessDescriptor:
    """
    Immutable specification for one supervisable child.

    ``restart_policy`` defaults to OnFailure (Phase 3).
    ``lock_aware`` is metadata — the child entrypoint still owns lock logic.
    """

    name: str
    entrypoint: Callable[..., Any]
    args: Tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    startup_timeout: float = 30.0
    shutdown_timeout: float = 5.0
    restart_policy: Union[str, RestartPolicyKind] = RestartPolicyKind.ON_FAILURE
    dependencies: Tuple[str, ...] = ()
    lock_aware: bool = False
    display_name: Optional[str] = None
    # Match historic main.py unless overridden.
    # Phase M1: default False — Job Object owns tree teardown on API death.
    daemon: bool = False

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("ProcessDescriptor.name is required")
        if self.entrypoint is None or not callable(self.entrypoint):
            raise ValueError("ProcessDescriptor.entrypoint must be callable")
        if self.startup_timeout <= 0:
            raise ValueError("startup_timeout must be > 0")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be > 0")
        policy = parse_restart_policy(self.restart_policy)
        object.__setattr__(self, "restart_policy", policy)
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        object.__setattr__(self, "dependencies", tuple(self.dependencies))

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def policy_kind(self) -> RestartPolicyKind:
        return self.restart_policy  # type: ignore[return-value]
