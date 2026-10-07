"""Native callback composition with explicit replacement semantics."""
from __future__ import annotations



def compose_after_tool(callbacks):
    """Run transforms on the latest result, returning one native replacement.

    Unlike ADK's outer callback chain, every transform here runs. Exceptions
    propagate; a broken required transform must not silently release its input.
    """
    import inspect

    async def after_tool(tool, args, tool_context, tool_response):
        current = tool_response
        changed = False
        for callback in callbacks:
            result = callback(tool=tool, args=args, tool_context=tool_context,
                              tool_response=current)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                current, changed = result, True
        return current if changed else None

    # The composed hook is opaque to callers, so keep the contributing
    # callbacks reachable. Ordering verification reads through this.
    after_tool.adkl_composed = list(callbacks)
    return after_tool
