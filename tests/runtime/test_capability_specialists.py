"""Scoped single-turn specialists retain native execution and parent ownership."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_policy import CapabilityDecision


class Model(BaseLlm):
    child: bool = False
    force_unavailable: bool = False
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        if self.child:
            part = types.Part(text="Specialist result")
        elif len(self.requests) == 1 and (
                self.force_unavailable or "specialist" in request.tools_dict):
            part = types.Part(function_call=types.FunctionCall(
                name="specialist", args={"request": "Check the evidence"}))
        else:
            part = types.Part(text="Parent answer")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class Policy:
    def __init__(self, expose=True, authorize=True):
        self.exposed, self.authorized = expose, authorize

    def expose(self, context, catalog):
        return [d.name for d in catalog] if self.exposed else []

    def authorize(self, context, request):
        assert request.kind == "agent"
        return CapabilityDecision(self.authorized, "specialist_policy")


async def run_agent(agent):
    runner = InMemoryRunner(agent=agent, app_name="specialists")
    try:
        session = await runner.session_service.create_session(app_name="specialists", user_id="u")
        return [event async for event in runner.run_async(user_id="u", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Help")]))]
    finally:
        await runner.close()


@pytest.mark.parametrize("expose,authorize", [(True, True), (False, True), (True, False)])
def test_single_turn_policy_checks_exposure_and_dispatch(expose, authorize):
    parent, child = Model(model="fixture"), Model(model="fixture", child=True)
    specialist = LlmAgent(name="specialist", description="Check evidence",
        instruction="PRIVATE_SPECIALIST_PROCEDURE", mode="single_turn", model=child)
    outcomes = []
    binding = CapabilityBindings(sub_agents=[specialist], policy=Policy(expose, authorize),
                                 on_event=outcomes.append)
    agent = LlmAgent(name="parent", model=parent, **binding.agent_kwargs())
    native_tools = tuple(agent.tools)

    events = asyncio.run(run_agent(agent))
    assert bool(child.requests) is (expose and authorize)
    assert ("specialist" in parent.requests[0].tools_dict) is expose
    assert "PRIVATE_SPECIALIST_PROCEDURE" not in str(parent.requests[0])
    assert tuple(agent.tools) == native_tools
    assert len(parent.requests) == (2 if expose else 1)
    assert any(event.author == "parent" and event.content and any(
        part.text == "Parent answer" for part in event.content.parts or []) for event in events)
    if expose:
        assert outcomes[-1].status == ("completed" if authorize else "denied")
    else:
        assert not outcomes  # No operation was attempted; do not fabricate events.


def test_hallucinated_hidden_specialist_is_rejected_by_native_dispatch():
    parent = Model(model="fixture", force_unavailable=True)
    child = Model(model="fixture", child=True)
    specialist = LlmAgent(name="specialist", description="Check evidence",
                          mode="single_turn", model=child)
    outcomes = []
    binding = CapabilityBindings(sub_agents=[specialist], policy=Policy(expose=False),
                                 on_event=outcomes.append)
    agent = LlmAgent(name="parent", model=parent, **binding.agent_kwargs())
    try:
        asyncio.run(run_agent(agent))
    except ValueError as exc:
        assert "Tool 'specialist' not found" in str(exc)
    else:
        # Newer native ADK returns a recoverable missing-tool response instead.
        responses = [p.function_response for c in parent.requests[-1].contents
                     for p in c.parts or [] if p.function_response]
        assert any(r.name == "specialist" and r.response.get("error") for r in responses)
    assert "specialist" not in parent.requests[0].tools_dict
    assert not child.requests
    assert not outcomes  # ADK rejects before the ADKL adapter is reached.


def test_previously_exposed_specialist_is_rechecked_at_dispatch():
    parent, child = Model(model="fixture"), Model(model="fixture", child=True)
    specialist = LlmAgent(name="specialist", description="Check evidence",
                          mode="single_turn", model=child)
    policy = Policy()
    outcomes = []

    def revoke(tool, args, tool_context):
        policy.exposed = False

    binding = CapabilityBindings(sub_agents=[specialist], policy=policy,
                                 on_event=outcomes.append)
    agent = LlmAgent(name="parent", model=parent, **binding.agent_kwargs(
        callbacks={"before_tool_callback": revoke}))
    asyncio.run(run_agent(agent))
    assert "specialist" in parent.requests[0].tools_dict
    assert not child.requests
    assert [(event.status, event.reason) for event in outcomes] == [
        ("requested", "model_request"), ("denied", "not_eligible")]
    assert len(parent.requests) == 2


@pytest.mark.parametrize("mode", [None, "chat", "task"])
def test_dynamic_handoff_modes_fail_before_parenting(mode):
    child = LlmAgent(name="specialist", description="Check evidence", mode=mode,
                     model=Model(model="fixture", child=True))
    with pytest.raises(ValueError, match="single_turn"):
        CapabilityBindings(sub_agents=[child], policy=Policy())
    assert child.parent_agent is None
