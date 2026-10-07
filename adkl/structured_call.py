"""Explicit private structured generation for native workflow FunctionNodes.

This is NOT an ADK agent invocation: no parent callbacks are inherited, no text
is streamed, and no usage is fabricated. Host hooks supply configuration and
accounting. Calls use the host's BaseLlm; ADKL supplies no model or credentials.
"""
import asyncio
from contextlib import aclosing
import inspect
import json


async def structured_call(*, model, schema, instruction, payload, context,
                          config=None, prepare=None, observe=None, timeout=30):
    """Return schema-validated data; never publish private generation content.

    prepare(context, request) and observe(context, response) are explicit hooks,
    may be async, must return None, and fail the operation if they raise.
    observe receives usage even on model errors. No automatic model repair.
    Cancellation closes the provider iterator. Domain validation is the caller's.
    When context exposes native get_invocation_context(), reserve one call from
    its shared ADK budget before invoking the provider. Usage is still delivered
    only to observe, not fabricated as an agent event.
    """
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    async def hook(fn, value):
        if fn is not None:
            result = fn(context, value)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                raise TypeError("Structured call hooks must return None")

    options = config.model_copy(deep=True) if config is not None else types.GenerateContentConfig()
    options.system_instruction = instruction
    options.response_mime_type = "application/json"
    options.response_schema = schema
    options.tools = None
    options.tool_config = None
    request = LlmRequest(model=model.model,
        contents=[types.Content(role="user", parts=[types.Part(text=json.dumps(payload))])], config=options)
    await hook(prepare, request)
    # A preparation hook may normalize provider configuration, but this boundary
    # is data-only: it must not expose executable tools to a private generation.
    if request.tools_dict or request.config.tools:
        raise ValueError("Structured calls cannot expose tools")
    get_context = getattr(context, "get_invocation_context", None)
    if get_context is not None:
        get_context().increment_llm_call_count()
    text = None
    async with asyncio.timeout(timeout):
        async with aclosing(model.generate_content_async(request, stream=False)) as responses:
            async for response in responses:
                await hook(observe, response)
                if response.error_code:
                    raise ValueError("Structured model call failed")
                if response.partial or not response.content:
                    continue
                if any(p.function_call for p in response.content.parts or []):
                    raise ValueError("Structured model call returned a tool call")
                text = "".join(p.text or "" for p in response.content.parts or [] if not p.thought)
    if text is None:
        raise ValueError("Structured model call returned no final data")
    return schema.model_validate_json(text)
