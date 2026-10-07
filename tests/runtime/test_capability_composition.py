"""One capability binding composes native execution and shared host lifecycle."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import Frontmatter, Skill
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.capability_bindings import CapabilityBindings
from adkl.host import describe_agent
from adkl.inspection import bindings
from adkl.widgets import Role, WidgetSpec, stage
from tests.support.native import run
from tests.support.capabilities import ChoiceModel, Inputs, workflow


def test_merge_preserves_constructor_and_composes_request_and_result_phases():
    seen = []

    def lookup(ctx, node_input: Inputs):
        seen.append("execute")
        return {"devices": ["phone-a"], "internal": "raw"}

    def prepare(callback_context, llm_request):
        seen.append("prepare")
        llm_request.append_instructions(["HOST_PREPARED"])

    def final(callback_context, llm_request):
        assert "<capabilities>" in str(llm_request.config.system_instruction)
        seen.append("final")
        llm_request.append_instructions(["HOST_FINAL"])

    def observe_request(callback_context, llm_request):
        assert "HOST_FINAL" in str(llm_request.config.system_instruction)
        seen.append("request_observed")

    def observe_result(tool, args, tool_context, tool_response):
        assert tool_response["internal"] == "raw"
        seen.append("raw_observed")

    def trim(tool, args, tool_context, tool_response):
        seen.append("trimmed")
        return {"devices": tool_response["devices"]}

    model = ChoiceModel(model="fixture", choice="workflow")
    generation = types.GenerateContentConfig(temperature=0.2)
    original = dict(name="host", model=model, instruction="Host policy",
                    generate_content_config=generation, tools=[], sub_agents=[],
                    before_model_callback=[prepare], after_tool_callback=trim)
    binding = CapabilityBindings(workflows=[workflow(lookup)])
    merged = binding.merge_into(
        original, after_tool_observers=[observe_result],
        final_model_transforms=[final], before_model_observers=[observe_request])
    assert merged["model"] is model
    assert merged["generate_content_config"] is generation
    assert original["tools"] == []
    assert original["before_model_callback"] == [prepare]
    agent = LlmAgent(**merged)
    assert bindings(agent) == [binding]
    assert not any(isinstance(t, SkillToolset) for t in agent.tools)
    asyncio.run(run(agent))
    assert seen == ["prepare", "final", "request_observed", "execute",
                    "raw_observed", "trimmed", "prepare", "final", "request_observed"]
    assert "internal" not in str(model.requests[-1].contents)
    assert len(model.requests) == 2


def test_request_short_circuit_skips_model_and_final_transforms():
    def cached(callback_context, llm_request):
        return LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Cached")]))

    def unreachable(callback_context, llm_request):
        pytest.fail("Final transforms must not run after a native short circuit")

    model = ChoiceModel(model="fixture", choice="workflow")
    binding = CapabilityBindings()
    agent = LlmAgent(**binding.merge_into(
        {"name": "host", "model": model, "before_model_callback": cached},
        final_model_transforms=[unreachable]))
    events, _ = asyncio.run(run(agent))
    assert model.requests == []
    assert any("Cached" in str(e.content) for e in events)


@pytest.mark.parametrize("failure", [False, True])
def test_workflow_staging_emits_once_before_host_completion_and_cleans_next_turn(failure):
    calls = []
    findings = []
    async def build(ctx, items, request_id):
        return [(items[0], "Result ready")]

    spec = WidgetSpec(key="result", register="result_register", role=Role.DATA_PRIMARY,
                      widget_id="result", payload_type="fixture", build=build)

    def lookup(ctx, node_input: Inputs):
        stage(ctx.state, spec, [{"title": "Fixture result"}])
        return {"devices": ["phone-a"]}

    def sink(ctx, widget_id, payload):
        calls.append("sink")
        if failure:
            raise RuntimeError("private transport detail")

    def complete(callback_context):
        calls.append("complete")
        return types.Content(role="model", parts=[types.Part(text="Host completion")])

    binding = CapabilityBindings(
        workflows=[workflow(lookup)], widget_specs=[spec], widget_sink=sink,
        on_finding=findings.extend, feature_config={"pipeline": ["staged_widgets"]})
    model = ChoiceModel(model="fixture", choice="workflow")
    agent = LlmAgent(name="host", model=model,
                    **binding.agent_kwargs(callbacks={"after_agent_callback": complete}))

    async def exercise():
        from google.adk.runners import InMemoryRunner
        runner = InMemoryRunner(agent=agent, app_name="capability_staging")
        try:
            session = await runner.session_service.create_session(
                app_name="capability_staging", user_id="u")
            for _ in range(2):
                async for _event in runner.run_async(
                        user_id="u", session_id=session.id,
                        new_message=types.Content(role="user", parts=[types.Part(text="help")])):
                    pass
        finally:
            await runner.close()

    asyncio.run(exercise())
    assert calls == ["sink", "complete", "complete"]
    assert bool(findings) is failure
    assert "private transport detail" not in str(findings)
    assert bindings(agent) == [binding]


def test_binding_inspection_and_skill_scope_remain_native():
    def lookup(query: str) -> str:
        """Find a fixture record."""
        return query

    skill = Skill(frontmatter=Frontmatter(name="policy-help", description="Policy help",
                  metadata={"adk_additional_tools": ["lookup"]}), instructions="Read policy.")
    binding = CapabilityBindings(skills=[skill], skill_tools=[lookup])
    agent = LlmAgent(name="host", model=ChoiceModel(model="fixture", choice="skill"),
                    **binding.agent_kwargs())
    assert len(agent.tools) == 1
    assert isinstance(agent.tools[0], SkillToolset)
    report = describe_agent(agent)["adkl"]
    assert len(report) == 1
    assert report[0]["selection"] == "parent_model"
    assert report[0]["features"] == []
    assert report[0]["skillTools"] == {"policy-help": ["lookup"]}
    detached = binding.inspect()
    detached["capabilities"][0]["name"] = "changed"
    assert binding.inspect()["capabilities"][0]["name"] == "policy-help"


@pytest.mark.parametrize("field,value", [("tools", [lambda: None]),
                                         ("tools", "invalid"),
                                         ("sub_agents", [LlmAgent(name="child")])])
def test_merge_rejects_implicit_ownership_without_consuming_binding(field, value):
    binding = CapabilityBindings()
    original = {field: value}
    with pytest.raises(ValueError, match="Register"):
        binding.merge_into(original)
    assert original[field] is value
    assert binding.merge_into({})["tools"] == []
    with pytest.raises(ValueError, match="already issued"):
        binding.merge_into({})


def test_competing_lifecycle_and_skill_features_are_rejected():
    prior = RuntimeBindings(skills=[], native_toolset=SkillToolset(skills=[]),
                            feature_config={"pipeline": []}).agent_kwargs()
    binding = CapabilityBindings()
    with pytest.raises(ValueError, match="lifecycle callbacks"):
        binding.agent_kwargs(callbacks={"before_agent_callback": prior["before_agent_callback"]})
    assert binding.agent_kwargs()["tools"] == []
    with pytest.raises(ValueError, match="owns selection"):
        CapabilityBindings(feature_config={"pipeline": ["skill_retrieval"]})


def test_bad_final_transform_does_not_consume_binding():
    binding = CapabilityBindings()
    with pytest.raises(TypeError, match="final_model_transforms"):
        binding.agent_kwargs(final_model_transforms=lambda: None)
    assert binding.agent_kwargs()["tools"] == []
