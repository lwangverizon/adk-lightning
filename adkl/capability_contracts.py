"""Description-only discovery contracts, independent of native ADK execution.

A descriptor identifies a capability; it never contains its implementation or
private instructions. Hosts rank these records, and the parent model chooses an
operation. Discovery does not activate skills or authorize business operations.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from google.adk.tools.base_tool import BaseTool


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    kind: str
    description: str
    operation: str


@dataclass(frozen=True)
class CapabilitySelection:
    """A relevance shortlist, not the parent model's execution decision.

    ``fallback`` is diagnostic only: names must still explicitly identify the
    eligible candidates to expose. It never means "allow the entire catalog".
    """

    names: tuple[str, ...]
    fallback: bool = False


CapabilityCatalog = tuple[CapabilityDescriptor, ...]
CapabilityNames = Collection[str]
CapabilityRanker = Callable[
    [Any, CapabilityCatalog], CapabilitySelection | Awaitable[CapabilitySelection]
]
CapabilityRetention = Callable[
    [Any, CapabilityCatalog], CapabilityNames | Awaitable[CapabilityNames]
]
CapabilityDiscovery = Callable[[Any], Awaitable[set[str]]]
CapabilityToolGuard = Callable[["BaseTool", Collection[str]], "BaseTool"]


class CapabilityDiscoveryConsumer(Protocol):
    """Handshake for a host-owned native SkillToolset using shared retrieval.

    Bind once before execution. Consume ``discover(context)`` for both loading
    and tool exposure, and wrap returned tools with ``guard(tool, owners)``.
    By default the binding emits the catalog. Consumers using native EAGER
    disclosure may set ``emits_capability_skill_catalog = True`` and emit only
    shortlisted skill descriptions; the binding then emits non-skill peers.
    Do not independently rank or auto-load. Host-native resource loading and
    business guards remain intact.
    """

    def bind_capability_discovery(
        self, discover: CapabilityDiscovery, guard: CapabilityToolGuard
    ) -> None: ...
