"""Public, dependency-light policy and outcome contracts for native capabilities.

Policies decide eligibility, not which capability the model should choose.
Observers never authorize execution or carry private inputs/results.
"""
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CapabilityRequest:
    """A requested operation; arguments are a detached copy for policy inspection."""

    capability: str
    kind: str
    operation: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str = "policy"

    def __post_init__(self):
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be bool")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 100:
            raise ValueError("reason must be a nonempty bounded code")
        if not all(c.isalnum() or c in "_-." for c in self.reason):
            raise ValueError("reason must be a code, not private text")


@dataclass(frozen=True)
class CapabilityOutcome:
    """Business outcomes require an explicit adapter; do not infer from prose."""

    status: str = "completed"
    reason: str = "returned"
    continuation: str = "none"

    def __post_init__(self):
        if self.status not in {"completed", "failed", "denied", "suppressed"}:
            raise ValueError("Invalid capability outcome")
        if self.continuation not in {"none", "waiting"}:
            raise ValueError("Invalid capability continuation")
        if self.continuation != "none" and self.status != "completed":
            raise ValueError("Only completed operations may retain continuation")
        CapabilityDecision(True, self.reason)


@dataclass(frozen=True)
class CapabilityEvent:
    schema_version: int
    invocation_id: str
    agent: str
    call_id: str
    capability: str
    kind: str
    operation: str
    status: str
    reason: str


class CapabilityPolicy(Protocol):
    """Methods may return their result directly or as an awaitable.

    expose returns registered capability names only. It runs at discovery and
    again at dispatch, so implementations must be read-only and inexpensive.
    authorize supplements, never replaces, business-tool authorization.
    """

    def expose(self, context: Any, catalog: tuple) -> Any: ...

    def authorize(self, context: Any, request: CapabilityRequest) -> Any: ...
