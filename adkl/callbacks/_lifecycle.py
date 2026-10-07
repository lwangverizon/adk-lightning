"""Internal callback-chain mechanics; native phase placement stays in bindings.

No ADK imports, host policy, selection or request state is owned here. Keep the
public callback factories in composition.py and the binding APIs unchanged.
"""
from __future__ import annotations

import inspect

from .composition import compose_after_tool


def require_callbacks(value, name):
    """Validate sequence-only extension hooks without changing their lifetime."""
    if not isinstance(value, (list, tuple)) or any(not callable(fn) for fn in value):
        raise TypeError(f"{name} must be a sequence of callables")


def normalize_callbacks(callbacks, allowed):
    """Copy native callback lists; a single native callback is also accepted."""
    if set(callbacks) - allowed:
        raise ValueError(f"Unknown callback kinds: {sorted(set(callbacks) - allowed)}")
    hooks = {}
    for kind, fns in callbacks.items():
        if fns is None:
            continue
        if callable(fns):
            fns = [fns]
        require_callbacks(fns, kind)
        hooks[kind] = list(fns)
    return hooks


def compose_output_transforms(chain):
    """Feed each non-None response replacement to the following transform."""
    async def after_model(callback_context, llm_response):
        response, replaced = llm_response, False
        for transform in chain:
            result = transform(callback_context=callback_context, llm_response=response)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                response, replaced = result, True
        return response if replaced else None
    return after_model


def compose_tool_callbacks(observers, callbacks):
    """Observe the raw result before sequential replacement transforms."""
    transforms = compose_after_tool(callbacks)

    async def after_tool(tool, args, tool_context, tool_response):
        for observer in observers:
            result = observer(tool=tool, args=args, tool_context=tool_context,
                              tool_response=tool_response)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                raise TypeError("after_tool observer must return None")
        return await transforms(tool=tool, args=args, tool_context=tool_context,
                                tool_response=tool_response)
    return after_tool


def compose_completion(emitter, completion, get_reporter):
    """Emit before host completion, retaining ordinary-error cleanup/precedence.

    Reporter lookup stays at failure time. A successful synchronous reporter
    handles delivery failure; otherwise re-raise after host completion. Native
    cancellation and exceptions from host completion still propagate directly.
    This is not a finally boundary or an exactly-once delivery guarantee.
    """
    async def after_agent(callback_context):
        companion, failure, reporting_failure = None, None, None
        try:
            companion = await emitter(callback_context)
        except Exception as exc:
            failure = exc
            # Exception messages may contain host data; retain only the type.
            key = "adkl:widgets:" + callback_context.agent_name
            diagnostic = dict(callback_context.state.get(key, {}))
            diagnostic["delivery_error"] = type(exc).__name__
            callback_context.state[key] = diagnostic
            reporter = get_reporter()
            if reporter is not None:
                try:
                    reporter([f"staged widget emission failed: {type(exc).__name__}"])
                    failure = None
                except Exception as report_exc:
                    reporting_failure = report_exc
        response = companion
        for callback in completion:
            result = callback(callback_context=callback_context)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                response = result
                break
        if failure is not None:
            raise failure from reporting_failure
        return response
    return after_agent
