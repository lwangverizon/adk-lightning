"""Native Runner proves description selection and mode-specific execution."""

import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import Workflow
from google.genai import types

from adkl.capability_bindings import CapabilityBindings
from tests.support.capabilities import ChoiceModel, fixtures


async def exercise(choice, *, deny=False):
    binding, agent, model, specialist_model, calls = fixtures(choice, deny=deny)
    runner = InMemoryRunner(agent=agent, app_name="capabilities")
    try:
        session = await runner.session_service.create_session(app_name="capabilities", user_id="u")
        events = [e async for e in runner.run_async(user_id="u", session_id=session.id,
                  new_message=types.Content(role="user", parts=[types.Part(text="Help me")]))]
        return binding, events, model, specialist_model, calls
    finally:
        await runner.close()


@pytest.mark.parametrize("choice", ["skill", "workflow", "agent"])
def test_description_first_then_native_execution(choice):
    binding, events, model, specialist, calls = asyncio.run(exercise(choice))
    first = str(model.requests[0].config.system_instruction)
    assert "PRIVATE_SKILL_BODY" not in first
    assert "PRIVATE_SPECIALIST_BODY" not in first
    for descriptor in binding.inspect()["capabilities"]:
        assert descriptor["description"] in first
    assert len(model.requests) == (1 if choice == "agent" else 2)
    assert len(specialist.requests) == (1 if choice == "agent" else 0)
    assert calls == (["camera phones"] if choice == "workflow" else [])
    if choice == "skill":
        assert "PRIVATE_SKILL_BODY" in str(model.requests[1].contents)
    elif choice == "agent":
        assert "PRIVATE_SPECIALIST_BODY" in str(specialist.requests[0].config.system_instruction)
        assert not any(e.author == "prospect" and e.content and any(p.text == "Parent answer" for p in e.content.parts or []) for e in events)
    else:
        assert "phone-a" in str(model.requests[1].contents)


def test_workflow_denial_runs_no_graph_nodes():
    _, _, model, _, calls = asyncio.run(exercise("workflow", deny=True))
    assert calls == []
    assert "denied" in str(model.requests[1].contents)


def test_no_mutation_duplicate_names_and_single_issue():
    binding, agent, _, _, _ = fixtures("skill")
    assert agent.sub_agents[0].instruction == "PRIVATE_SPECIALIST_BODY"
    with pytest.raises(ValueError, match="already issued"):
        binding.agent_kwargs()
    skill = Skill(frontmatter=Frontmatter(name="same", description="test"), instructions="body")
    with pytest.raises(ValueError, match="Duplicate"):
        CapabilityBindings(skills=[skill, skill])
    with pytest.raises(ValueError, match="input_schema"):
        CapabilityBindings(workflows=[Workflow(name="flow", description="test")])


def test_parallel_invocations_do_not_load_each_others_skill_body():
    async def run():
        return await asyncio.gather(exercise("skill"), exercise("workflow"), exercise("agent"))
    results = asyncio.run(run())
    for choice, result in zip(["skill", "workflow", "agent"], results):
        model = result[2]
        assert "PRIVATE_SKILL_BODY" not in str(model.requests[0])
        assert ("PRIVATE_SKILL_BODY" in str(model.requests[-1])) == (choice == "skill")


def test_unsupported_callbacks_rejected_before_binding_is_consumed():
    binding = CapabilityBindings()
    with pytest.raises(ValueError, match="callback fields"):
        binding.agent_kwargs(callbacks={"unknown_callback": lambda: None})
    assert binding.agent_kwargs()["tools"] == []


def test_missing_descriptions_and_orphan_skill_tools_rejected():
    with pytest.raises(ValueError, match="description"):
        CapabilityBindings(sub_agents=[LlmAgent(name="missing", model="test")])
    with pytest.raises(ValueError, match="skill_tools"):
        CapabilityBindings(skill_tools=[lambda: None])


def test_skill_owned_tool_is_hidden_until_native_load():
    async def exercise_scope():
        from google.adk.tools.tool_context import ToolContext
        from google.adk.agents.invocation_context import InvocationContext
        from google.adk.agents.run_config import RunConfig
        from google.adk.sessions import InMemorySessionService

        def lookup(query: str) -> str:
            """Read a fixture record."""
            return query
        skill = Skill(frontmatter=Frontmatter(name="records", description="Find records",
                      metadata={"adk_additional_tools": ["lookup"]}), instructions="Call lookup.")
        binding = CapabilityBindings(skills=[skill], skill_tools=[lookup])
        kwargs = binding.agent_kwargs()
        agent = LlmAgent(name="host", model="test", **kwargs)
        service = InMemorySessionService()
        session = await service.create_session(app_name="test", user_id="u")
        ctx = ToolContext(InvocationContext(invocation_id="turn", agent=agent,
                          session=session, session_service=service, run_config=RunConfig()))
        toolset = kwargs["tools"][0]
        initial = await toolset.get_tools(ctx)
        assert "lookup" not in [t.name for t in initial]
        loader = next(t for t in initial if t.name == "load_skill")
        await loader.run_async(args={"skill_name": "records"}, tool_context=ctx)
        assert "lookup" in [t.name for t in await toolset.get_tools(ctx)]
        assert binding.inspect()["standaloneTools"] == []
        await toolset.close()
    asyncio.run(exercise_scope())


def test_standalone_tools_require_explicit_registration_and_reject_collisions():
    def lookup(query: str) -> str:
        """Read a record."""
        return query
    binding = CapabilityBindings(tools=[lookup])
    assert binding.inspect()["standaloneTools"] == ["lookup"]
    assert binding.inspect()["capabilities"][0]["kind"] == "tool"
    assert binding.agent_kwargs()["tools"][0].name == "lookup"
    skill = Skill(frontmatter=Frontmatter(name="records", description="Records",
                  metadata={"adk_additional_tools": ["lookup"]}), instructions="Read records")
    with pytest.raises(ValueError, match="Missing tools"):
        CapabilityBindings(skills=[skill])
    with pytest.raises(ValueError, match="Duplicate/reserved scoped"):
        CapabilityBindings(skills=[skill], skill_tools=[lookup], tools=[lookup])


def test_explicit_standalone_tool_uses_native_guard_and_dispatch():
    async def run():
        called = []
        def device_discovery(query: str) -> dict:
            """Find devices matching a query."""
            called.append(query)
            return {"devices": ["phone-a"]}
        binding = CapabilityBindings(tools=[device_discovery])
        model = ChoiceModel(model="fixture", choice="workflow")
        agent = LlmAgent(name="host", model=model, **binding.agent_kwargs())
        runner = InMemoryRunner(agent=agent, app_name="standalone")
        try:
            session = await runner.session_service.create_session(app_name="standalone", user_id="u")
            events = [e async for e in runner.run_async(user_id="u", session_id=session.id,
                      new_message=types.Content(role="user", parts=[types.Part(text="Find a phone")]))]
            assert called == ["camera phones"]
            assert len(model.requests) == 2
            assert any(e.get_function_responses() for e in events)
        finally:
            await runner.close()
    asyncio.run(run())
