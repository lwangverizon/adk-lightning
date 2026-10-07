"""Real Runner refreshes scoped tools after loading, including parallel sessions."""
import asyncio
import inspect

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types
from pydantic import Field

from adkl.capability_bindings import CapabilityBindings


class ScopedModel(BaseLlm):
    exposed: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.exposed.append(set(request.tools_dict))
        responses = [p.function_response for c in request.contents
                     for p in c.parts or [] if p.function_response]
        if not any(r.name == "load_skill" for r in responses):
            assert "lookup" not in request.tools_dict
            part = types.Part(function_call=types.FunctionCall(
                name="load_skill", args={"skill_name": "records"}))
        elif not any(r.name == "lookup" for r in responses):
            assert "lookup" in request.tools_dict
            part = types.Part(function_call=types.FunctionCall(name="lookup", args={}))
        else:
            part = types.Part(text="Done")
        await asyncio.sleep(0)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.mark.parametrize("controlled", [False, True])
@pytest.mark.parametrize("parallel", [False, True])
def test_parallel_sessions_load_then_execute_scoped_tool_without_cross_session_state(controlled, parallel):
    from google.adk.tools.tool_context import ToolContext

    calls = []

    def lookup(tool_context: ToolContext) -> dict:
        """Read the current session fixture."""
        value = tool_context.state["fixture"]
        calls.append(value)
        tool_context.state["visited"] = value
        return {"value": value}

    skill = Skill(frontmatter=Frontmatter(name="records", description="Read records",
                  metadata={"adk_additional_tools": ["lookup"]}), instructions="Call lookup.")
    from adkl.capability_policy import CapabilityDecision

    class Policy:
        def expose(self, context, catalog):
            return [d.name for d in catalog]

        def authorize(self, context, request):
            return CapabilityDecision(True)

    binding = CapabilityBindings(skills=[skill], skill_tools=[lookup],
                                 policy=Policy() if controlled else None)
    model = ScopedModel(model="fixture")
    observed = []

    def before_model(callback_context, llm_request):
        # Host hoisting/preparation consumes tools before ADKL final transforms.
        active = callback_context.state.get("_adk_activated_skill_host", [])
        if active:
            assert "lookup" in llm_request.tools_dict
            observed.append(True)

    agent = LlmAgent(name="host", model=model, **binding.merge_into(
        {"before_model_callback": before_model}))

    async def exercise():
        runner = InMemoryRunner(agent=agent, app_name="scoped")
        try:
            sessions = [await runner.session_service.create_session(
                app_name="scoped", user_id="u", state={"fixture": value})
                for value in ["a", "b"]]

            async def turn(session):
                async for _ in runner.run_async(
                        user_id="u", session_id=session.id,
                        new_message=types.Content(role="user", parts=[types.Part(text="read")])):
                    pass
                current = await runner.session_service.get_session(
                    app_name="scoped", user_id="u", session_id=session.id)
                assert current.state["visited"] == current.state["fixture"]

            if parallel:
                await asyncio.gather(*(turn(session) for session in sessions))
            else:
                for session in sessions:
                    await turn(session)
        finally:
            await runner.close()

    asyncio.run(exercise())
    assert sorted(calls) == ["a", "b"]
    assert len(model.exposed) == 6
    assert len(observed) == 4


def test_requested_activation_cap_is_supported_or_fails_explicitly():
    skill = Skill(frontmatter=Frontmatter(name="records", description="Records"),
                  instructions="Read records.")
    if "max_activated_skills" not in inspect.signature(SkillToolset).parameters:
        with pytest.raises(TypeError, match="max_activated_skills"):
            CapabilityBindings(skills=[skill], max_activated_skills=3)
    else:
        binding = CapabilityBindings(skills=[skill], max_activated_skills=3)
        assert len(binding.agent_kwargs()["tools"]) == 1
