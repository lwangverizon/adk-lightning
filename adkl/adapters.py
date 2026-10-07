"""Incremental callback adoption for an existing host-owned native ADK agent.

This returns constructor kwargs; it never mutates an agent. Retrieval, typed
skill context and staged presentation use RuntimeBindings or the AgentRuntime factory.
"""
from __future__ import annotations

import inspect

from adkl import capabilities, features, runtime


CALLBACK_FEATURES = frozenset(
    name for name, spec in features.REGISTRY.items() if spec.callback_adapter
)


def build_callback_adapter(feature_config, *, before_model=(), before_tool=(),
                           after_tool_observers=(), after_tool_transforms=(),
                           root=None, on_finding=None):
    """Return explicit before-model/before-tool/after-tool constructor kwargs.

    Host before hooks keep native ordering and short-circuit semantics; shared
    model hooks follow host hooks. After-tool observers see the raw response
    and must return None. Host transforms then shared transforms see the latest
    replacement, including empty values. Errors/cancellation propagate.

    Keep other native hooks on the Agent constructor. Do not pass the original
    before/after-tool lists again or install this adapter twice. Domain guards,
    authorization, model selection, skill toolsets and event transport stay with
    the host. These callbacks do not certify any of those host mechanisms.
    """
    resolved = features.resolve(feature_config)
    capabilities.require_supported(resolved, "sdk")
    unsupported = set(resolved.active) - CALLBACK_FEATURES
    if unsupported:
        raise features.FeatureError(
            f"Callback adapter does not support {sorted(unsupported)}; "
            "use RuntimeBindings, AgentRuntime or retain the host implementation")
    groups = {
        "before_model": before_model, "before_tool": before_tool,
        "after_tool_observers": after_tool_observers,
        "after_tool_transforms": after_tool_transforms,
    }
    for name, group in groups.items():
        if not isinstance(group, (list, tuple)) or any(not callable(fn) for fn in group):
            raise TypeError(f"{name} must be a list or tuple of callables")
    shared, unhonoured = runtime.build_callbacks(
        resolved.active, resolved.config, on_finding, root=root)
    if unhonoured:
        raise features.FeatureError("; ".join(unhonoured))
    hooks = {}
    model_callbacks = [*before_model, *shared.get("before_model_callback", [])]
    if model_callbacks:
        hooks["before_model_callback"] = model_callbacks
    if before_tool:
        hooks["before_tool_callback"] = list(before_tool)

    observers = tuple(after_tool_observers)
    transforms = [*after_tool_transforms, *shared.get("after_tool_callback", [])]
    if observers or transforms:
        transform = runtime.compose_after_tool(transforms)

        async def after_tool(tool, args, tool_context, tool_response):
            for observer in observers:
                result = observer(tool=tool, args=args, tool_context=tool_context,
                                  tool_response=tool_response)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    raise TypeError("after_tool observer returned a replacement; register it as a transform")
            return await transform(tool=tool, args=args, tool_context=tool_context,
                                   tool_response=tool_response)

        hooks["after_tool_callback"] = [after_tool]
    return hooks
