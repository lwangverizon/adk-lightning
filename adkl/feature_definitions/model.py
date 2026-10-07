"""Lightweight declaration types shared by built-in feature families."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class Stage(Enum):
    """Mirrors ADK's own FeatureStage so the idiom is familiar.

    Only STABLE features may default on -- see `Spec.default_on`.
    """

    WIP = "wip"
    EXPERIMENTAL = "experimental"
    STABLE = "stable"


class Kind(Enum):
    """Where the compiler attaches this feature."""

    TOOLSET = "toolset"            # constructor args on the skill toolset
    BEFORE_MODEL = "before_model"  # shapes the request/history
    AFTER_MODEL = "after_model"    # shapes the response
    TOOL_CALLBACK = "tool_callback"
    AGENT_CALLBACK = "agent_callback"
    RUNNER_PLUGIN = "runner_plugin"


class FeatureError(ValueError):
    """Config is wrong in a way that would fail silently at runtime."""


@dataclass(frozen=True)
class Spec:
    name: str
    kind: Kind
    summary: str
    stage: Stage = Stage.EXPERIMENTAL
    default_on: bool = False

    # Fields on the request/response this feature reads or mutates. Used for
    # conflict detection; the strings are conventional, not introspected.
    reads: frozenset = frozenset()
    writes: frozenset = frozenset()

    # True if returning non-None halts the remaining callbacks in its hook.
    may_short_circuit: bool = False

    must_run_after: frozenset = frozenset()

    # Option keys this feature refuses to take from the registry default. Use it
    # only where an unconfigured default changes DATA rather than tuning it: a
    # `pipeline` entry means "activate on defaults", which is right for a token
    # trim and wrong for a row filter. Named keys rather than a bare flag, so the
    # error can say which number the host has to write down.
    requires_explicit_config: frozenset = frozenset()

    # Config keys -> (type NAME, default, one-line help). Drives the console form
    # and rejects unknown keys, so a typo is an error rather than a silent no-op.
    #
    # The type is a STRING ("bool", not bool). It is metadata for the UI, not a
    # constructor, and `describe()` serializes it to JSON.
    options: dict = field(default_factory=dict)

    # Authoring metadata only; runtime.py and native contract tests establish wiring.
    support_status: str = "unsupported"
    support_detail: str = "No preview implementation has been registered."
    callback_adapter: bool = False
    validate: Callable[[dict], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.support_status not in {"implemented", "partial", "unsupported"}:
            raise FeatureError(f"{self.name}: invalid support status {self.support_status!r}")
        if not self.support_detail.strip():
            raise FeatureError(f"{self.name}: support detail must explain the boundary")
        if self.callback_adapter and self.support_status == "unsupported":
            raise FeatureError(f"{self.name}: an unsupported feature cannot enable callback adoption")
        if self.validate is not None and not callable(self.validate):
            raise FeatureError(f"{self.name}: validate must be callable")

        if self.default_on and self.stage is not Stage.STABLE:
            raise FeatureError(
                f"{self.name}: only STABLE features may default on "
                f"(stage={self.stage.value})")

        # Enforced at registration, because the failure is remote from the cause
        # and disproportionate: a Python type here serializes fine everywhere
        # until `describe()` reaches the console, then /api/agent 500s and the
        # whole UI shows "cannot reach the adkl server" -- it loads the agent
        # before it renders anything. A comment did not stop me writing `bool`;
        # this does, at import, naming the fix.
        for key, entry in self.options.items():
            if not (isinstance(entry, tuple) and len(entry) == 3):
                raise FeatureError(
                    f"{self.name}.{key}: options entries are "
                    f"(type_name, default, help) triples, got {entry!r}")
            type_name = entry[0]
            if not isinstance(type_name, str):
                got = getattr(type_name, "__name__", repr(type_name))
                raise FeatureError(
                    f"{self.name}.{key}: option type must be the type NAME as a "
                    f"string, not the type itself. Write \"{got}\" instead of "
                    f"{got}. A type object is not JSON serializable and breaks "
                    f"/api/agent, which takes the whole console down.")

        # Enforced here for the same reason as the type names above: a typo would
        # disarm the guard SILENTLY rather than loudly. An option key that does
        # not exist can never be missing from a declaration, so the feature would
        # quietly go back to activating on registry defaults with nothing to show
        # for it -- which is precisely the failure requires_explicit_config exists
        # to stop.
        phantom = sorted(set(self.requires_explicit_config) - set(self.options))
        if phantom:
            raise FeatureError(
                f"{self.name}: requires_explicit_config names option(s) that do "
                f"not exist: {', '.join(phantom)}")
