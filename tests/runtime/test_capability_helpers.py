"""Private structured generation and explicit child-tool hooks are bounded."""
import asyncio
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

from adkl.structured_call import structured_call
from adkl.tool_execution import invoke_tool


class Result(BaseModel):
    value: str


class Model(BaseLlm):
    closed: bool = False
    error: bool = False

    async def generate_content_async(self, request, stream=False):
        try:
            assert not stream
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text='{"value":"private"}')]),
                              error_code="blocked" if self.error else None)
        finally:
            self.closed = True


@pytest.mark.parametrize("error", [False, True])
def test_private_call_closes_and_observes_errors_without_publication(error):
    model = Model(model="fixture", error=error)
    observed = []

    async def run():
        return await structured_call(model=model, schema=Result, instruction="Select",
            payload={"input": "data"}, context=None, observe=lambda ctx, response: observed.append(response))

    if error:
        with pytest.raises(ValueError, match="failed"):
            asyncio.run(run())
    else:
        assert asyncio.run(run()).value == "private"
    assert model.closed and len(observed) == 1


def test_child_tool_guard_and_raw_transform_order():
    calls = []

    async def execute(**kwargs):
        calls.append("tool")
        return {"private": "raw"}

    def observe(**kwargs):
        assert kwargs["tool_response"] == {"private": "raw"}
        calls.append("observe")

    def trim(**kwargs):
        calls.append("trim")
        return {}

    tool = SimpleNamespace(run_async=execute)
    assert asyncio.run(invoke_tool(tool, {}, None, observers=[observe], transforms=[trim])) == {}
    assert calls == ["tool", "observe", "trim"]
    calls.clear()
    assert asyncio.run(invoke_tool(tool, {}, None, before=[lambda **kwargs: {"denied": True}])) == {"denied": True}
    assert calls == []


def test_private_call_reserves_native_budget_before_provider():
    calls = []
    model = Model(model="fixture")

    def reserve():
        calls.append("reserve")
        raise RuntimeError("budget exhausted")

    ctx = SimpleNamespace(get_invocation_context=lambda: SimpleNamespace(
        increment_llm_call_count=reserve))
    with pytest.raises(RuntimeError, match="budget exhausted"):
        asyncio.run(structured_call(model=model, schema=Result, instruction="Select",
                                    payload={}, context=ctx))
    assert calls == ["reserve"]
    assert not model.closed  # provider was never entered


def test_private_call_rejects_tools_added_by_preparation():
    model = Model(model="fixture")

    def prepare(ctx, request):
        request.config.tools = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name="unsafe")])]

    with pytest.raises(ValueError, match="cannot expose tools"):
        asyncio.run(structured_call(model=model, schema=Result, instruction="Select",
                                    payload={}, context=None, prepare=prepare))
    assert not model.closed
