"""Explicit callback boundary for tools called inside native workflow nodes.

Native ToolNodes call run_async directly and do not inherit parent callbacks.
This helper executes one tool with an explicit host chain, not a nested Runner.
Runner plugins, confirmation/auth negotiation and per-tool ADK tracing are not
emulated; tools needing them should execute in a native child agent instead.
"""
import inspect


async def invoke_tool(tool, args, context, *, before=(), observers=(), transforms=()):
    async def call(callback, **kwargs):
        result = callback(**kwargs)
        return await result if inspect.isawaitable(result) else result

    for callback in before:
        result = await call(callback, tool=tool, args=args, tool_context=context)
        if result is not None:
            return result
    response = await tool.run_async(args=args, tool_context=context)
    for observer in observers:
        result = await call(observer, tool=tool, args=args,
                            tool_context=context, tool_response=response)
        if result is not None:
            raise TypeError("Raw observers must return None")
    for transform in transforms:
        result = await call(transform, tool=tool, args=args,
                            tool_context=context, tool_response=response)
        if result is not None:
            response = result
    return response
