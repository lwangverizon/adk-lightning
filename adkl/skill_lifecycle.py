"""Native skill lifetime access without a second activation store.

Older ADK distributions have no public activity reader. The compatibility read
is isolated here; lifecycle configuration itself is never emulated.
"""
from typing import Any


def active_skill_names(toolset: Any, context: Any) -> tuple[str, ...]:
    """Read native activity, including invocation-aware ephemeral expiration."""
    reader = getattr(toolset, "list_active_skills", None)
    if callable(reader):
        return tuple(reader(context))
    # Compatibility with ADK 2.8 and older host wheels. These distributions do
    # not implement ephemeral lifetime; never use this for a configured lifecycle.
    return tuple(context.state.get(f"_adk_activated_skill_{context.agent_name}") or ())


def validate_lifecycle_config(config: Any) -> None:
    """Require native configuration and request pruning; never enable global flags."""
    from google.adk.tools import skill_toolset

    native_type = getattr(skill_toolset, "SkillLifecycleConfig", None)
    if native_type is None:
        raise ValueError("skill_lifecycle requires ADK native SkillLifecycleConfig support")
    if not isinstance(config, native_type):
        raise TypeError("skill_lifecycle must be native SkillLifecycleConfig")
    from google.adk.features import FeatureName, is_feature_enabled

    if not is_feature_enabled(FeatureName.SKILL_LIFECYCLE):
        raise ValueError("Enable ADK SKILL_LIFECYCLE before constructing the toolset; "
                         "native unloaded-instruction pruning is required")
