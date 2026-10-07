"""Exercise failure precedence, pin retention and generic sizing via native ADK."""
import asyncio
import json

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.retrieval import Match, MatchResult
from adkl.widgets import WidgetSpec, stage
from test_bindings import ScriptModel, plain_skill, run


@pytest.mark.parametrize("reporter", [None, "broken", "working"])
def test_sink_failure_cannot_disappear_behind_completion_content(reporter):
    async def scenario():
        cleaned, findings = [], []
        async def build(ctx, items, rid):
            return [{"type": "fact"}]
        async def sink(*args):
            raise OSError("sensitive synthetic detail")
        def report(notes):
            if reporter == "broken":
                raise RuntimeError("reporter failed")
            findings.extend(notes)
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact", build=build)
        def begin(callback_context):
            stage(callback_context.state, spec, ["value"])
        def complete(callback_context):
            cleaned.append(callback_context.state["adkl:widgets:host"]["delivery_error"])
            return types.Content(role="model", parts=[types.Part(text="Host completion")])
        binding = RuntimeBindings(skills=[], widget_specs=[spec], widget_sink=sink,
            on_finding=report if reporter else None, feature_config={"pipeline": ["staged_widgets"]})
        agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs(callbacks={
            "before_agent_callback": [begin], "after_agent_callback": [complete]}))
        if reporter == "working":
            events, _ = await run(agent)
            assert events[-1].content.parts[0].text == "Host completion"
            assert findings and "sensitive" not in str(findings)
        else:
            with pytest.raises(OSError, match="synthetic"):
                await run(agent)
        assert cleaned == ["OSError"]
    asyncio.run(scenario())


def test_duplicate_pin_preserves_active_skill_without_mutating_selector_result():
    result = MatchResult([Match("next", 1., "embedding"), Match("flow", .2, "embedding"),
                          Match("flow", 0., "pinned")], auto_activated="next")
    class Selector:
        async def match(self, text, state):
            return result
    async def scenario():
        binding = RuntimeBindings(skills=[plain_skill("next"), plain_skill("flow")], selector=Selector(),
            feature_config={"pipeline": ["skill_retrieval"], "skill_retrieval": {
                "auto_activate": True, "continue_active": True, "min_skills_for_retrieval": 0}})
        _, session = await run(LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs()),
                               {"_adk_activated_skill_host": ["flow"]})
        assert session.state["_adk_activated_skill_host"] == ["flow", "next"]
        assert len(result.matches) == 3 and not result.pinned
    asyncio.run(scenario())


@pytest.mark.parametrize("reduce_in_host", [False, True])
def test_native_tool_observer_host_reducer_then_generic_guard(reduce_in_host):
    raw = {"status": "success", "items": [{"id": 1, "detail": "x" * 4000}]}
    seen = []
    def lookup() -> dict:
        """Return synthetic records."""
        return raw
    def observer(**kwargs):
        seen.append(kwargs["tool_response"])
    def host_reduce(**kwargs):
        if reduce_in_host:
            return {"status": "success", "items": [{"id": 1}]}
    class Model(ScriptModel):
        async def generate_content_async(self, request, stream=False):
            if not self.requests:
                self.requests.append("called")
                part = types.Part(function_call=types.FunctionCall(name="lookup", args={}, id="lookup-1"))
            else:
                responses = [p.function_response.response for content in request.contents
                             for p in content.parts if p.function_response]
                result = responses[-1]
                if reduce_in_host:
                    assert result == {"status": "success", "items": [{"id": 1}]}
                else:
                    assert "_adkl_trimmed" in result and "items" not in result
                    assert len(json.dumps(result)) <= 300
                part = types.Part(text="Finished")
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
    async def scenario():
        binding = RuntimeBindings(skills=[], feature_config={
            "pipeline": ["tool_result_trimming"], "tool_result_trimming": {"max_chars": 300}})
        agent = LlmAgent(**binding.merge_into({"name": "host", "model": Model(), "tools": [lookup],
            "after_tool_callback": [host_reduce]}, after_tool_observers=[observer]))
        await run(agent)
        assert seen == [raw] and len(raw["items"][0]["detail"]) == 4000
    asyncio.run(scenario())
