"""Native Runner persistence, not just hand-built contexts, for skill expiration."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.tools import skill_toolset
from google.genai import types
from pydantic import Field

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection


class Model(BaseLlm):
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        index = len(self.requests)
        if index in (1, 4):
            part = types.Part(function_call=types.FunctionCall(
                name="load_skill", args={"skill_name": "records"}))
        elif index in (2, 5):
            assert "lookup" in request.tools_dict
            part = types.Part(function_call=types.FunctionCall(name="lookup", args={}))
        else:
            part = types.Part(text="Done")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def test_ephemeral_reloads_each_turn_without_rewriting_session(monkeypatch):
    if not hasattr(skill_toolset, "SkillLifecycleConfig"):
        pytest.skip("Requires native ADK lifecycle evaluation lane")
    monkeypatch.setenv("ADK_ENABLE_SKILL_LIFECYCLE", "1")
    calls = []

    def lookup():
        """Read record."""
        calls.append(True)
        return {"ok": True}

    skill = Skill(frontmatter=Frontmatter(name="records", description="Read records",
        metadata={"adk_additional_tools": ["lookup"]}), instructions="PRIVATE_BODY")
    binding = CapabilityBindings(skills=[skill], skill_tools=[lookup],
        skill_lifecycle=skill_toolset.SkillLifecycleConfig(
            default_mode=skill_toolset.SkillLifecycleMode.EPHEMERAL),
        retrieval=CapabilityRetrieval(lambda ctx, cat: CapabilitySelection(("records",))),
        retain_active_skills=True)
    model = Model(model="fixture")
    agent = LlmAgent(name="host", model=model, **binding.agent_kwargs())

    async def exercise():
        runner = InMemoryRunner(agent=agent, app_name="lifecycle")
        try:
            session = await runner.session_service.create_session(app_name="lifecycle", user_id="u")
            for _ in range(2):
                async for _event in runner.run_async(user_id="u", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="read")])):
                    pass
            saved = await runner.session_service.get_session(app_name="lifecycle", user_id="u", session_id=session.id)
            loaded = [p.function_response for event in saved.events if event.content
                      for p in event.content.parts or [] if p.function_response and p.function_response.name == "load_skill"]
            assert len(loaded) == 2
            assert all(r.response["instructions"] == "PRIVATE_BODY" for r in loaded)
        finally:
            await runner.close()
    asyncio.run(exercise())
    assert calls == [True, True]
    assert len(model.requests) == 6
    assert "lookup" not in model.requests[3].tools_dict
    assert "PRIVATE_BODY" not in str(model.requests[3].contents)
    assert "unloaded" in str(model.requests[3].contents)
    assert "PRIVATE_BODY" in str(model.requests[4].contents)
