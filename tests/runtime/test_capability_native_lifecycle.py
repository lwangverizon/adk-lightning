"""Native tool parity on the baseline and lifecycle contract on capable ADK.

Lifecycle tests skip explicitly on older ADK; they also run against the pinned
upstream evaluation wheel. No mock implementation of native lifetime semantics.
"""
import asyncio
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import Frontmatter, Resources, Skill
from google.adk.tools import skill_toolset
from google.adk.tools.function_tool import FunctionTool
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel, Field

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_execution import guarded_tool
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection
from tests.support.native import run


class Model(BaseLlm):
    calls: list = Field(default_factory=list)
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        index = len(self.requests) - 1
        part = (types.Part(function_call=types.FunctionCall(**self.calls[index]))
                if index < len(self.calls) else types.Part(text="Done"))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def retrieval():
    return CapabilityRetrieval(lambda ctx, catalog: CapabilitySelection(tuple(d.name for d in catalog)))


def test_guarded_binary_resource_keeps_native_preparation():
    skill = Skill(frontmatter=Frontmatter(name="images", description="Read images"),
                  instructions="Read image", resources=Resources(assets={"image.png": b"fixture"}))
    binding = CapabilityBindings(skills=[skill], retrieval=retrieval())
    model = Model(model="fixture", calls=[dict(name="load_skill_resource",
        args={"skill_name": "images", "file_path": "assets/image.png"})])
    asyncio.run(run(LlmAgent(name="host", model=model, **binding.agent_kwargs())))
    assert any(p.inline_data and p.inline_data.data == b"fixture"
               for c in model.requests[-1].contents for p in c.parts or [])
    assert type(model.requests[-1].tools_dict["load_skill_resource"]).__name__ == "GuardedTool"


@pytest.mark.parametrize("primitive", [False, True])
def test_controlled_workflow_keeps_native_schema_and_validation_recovery(primitive):
    class Input(BaseModel):
        query: str

    flow = Workflow(name="lookup", description="Lookup", input_schema=str if primitive else Input,
        edges=[("START", FunctionNode(name="node", func=lambda ctx, node_input: {"ok": True}))])
    binding = CapabilityBindings(workflows=[flow], retrieval=retrieval())
    model = Model(model="fixture", calls=[dict(name="lookup", args={"request": "hello"} if primitive else {})])
    asyncio.run(run(LlmAgent(name="host", model=model, **binding.agent_kwargs())))
    responses = [p.function_response.response for c in model.requests[-1].contents
                 for p in c.parts or [] if p.function_response]
    assert len(model.requests) == 2
    assert responses[0] == {"ok": True} if primitive else "validat" in str(responses[0]).lower()


def test_unload_cleanup_does_not_depend_on_ranker_but_rechecks_authorization():
    from adkl.capability_policy import CapabilityDecision

    def unload_skill(skill_name: str):
        """Release skill."""
        return {"released": skill_name}

    class Policy:
        allow = True
        def expose(self, context, catalog):
            return []
        def authorize(self, context, request):
            return CapabilityDecision(self.allow)

    policy = Policy()
    skill = Skill(frontmatter=Frontmatter(name="records", description="Records"), instructions="Read")
    binding = CapabilityBindings(skills=[skill], policy=policy,
        retrieval=CapabilityRetrieval(lambda ctx, cat: pytest.fail("Cleanup must not rank")))
    tool = guarded_tool(binding, FunctionTool(unload_skill), [])
    ctx = SimpleNamespace(state={}, agent_name="host", invocation_id="turn", function_call_id="call")
    assert asyncio.run(tool.run_async(args={"skill_name": "records"}, tool_context=ctx)) == {"released": "records"}
    policy.allow = False
    assert asyncio.run(tool.run_async(args={"skill_name": "records"}, tool_context=ctx))["error_code"] == "CAPABILITY_DENIED"


@pytest.fixture
def lifecycle(monkeypatch):
    if not hasattr(skill_toolset, "SkillLifecycleConfig"):
        pytest.skip("Installed ADK has no native skill lifecycle; run upstream contract lane")
    monkeypatch.setenv("ADK_ENABLE_SKILL_LIFECYCLE", "1")
    yield skill_toolset


def test_lifecycle_support_fails_explicitly_on_old_adk():
    if hasattr(skill_toolset, "SkillLifecycleConfig"):
        pytest.skip("Old-runtime rejection only")
    with pytest.raises(ValueError, match="native SkillLifecycleConfig"):
        CapabilityBindings(skill_lifecycle=object())


@pytest.mark.parametrize("mode", ["BOUNDED", "EPHEMERAL"])
def test_native_lifetime_controls_tools_retention_and_request_history(lifecycle, mode):
    from google.adk.models.llm_request import LlmRequest
    from adkl.skill_lifecycle import active_skill_names

    def lookup():
        """Read record."""
        return {"ok": True}

    skills = [Skill(frontmatter=Frontmatter(name=n, description=n,
        metadata={"adk_additional_tools": ["lookup"]}), instructions="PRIVATE_" + n)
        for n in ("first", "second")]
    config = lifecycle.SkillLifecycleConfig(default_mode=getattr(lifecycle.SkillLifecycleMode, mode), max_active_skills=1)
    chosen = ["first", "second"]
    binding = CapabilityBindings(skills=skills, skill_tools=[lookup], skill_lifecycle=config,
        retrieval=CapabilityRetrieval(lambda ctx, cat: CapabilitySelection(tuple(chosen))), retain_active_skills=True)
    native = binding._skill_toolset
    ctx = SimpleNamespace(state={}, agent_name="host", invocation_id="one", function_call_id="call",
        session=SimpleNamespace(app_name="app", user_id="u", id="s"))

    async def exercise():
        tools = {t.name: t for t in await native.get_tools(ctx)}
        result = await tools["load_skill"].run_async(args={"skill_name": "first"}, tool_context=ctx)
        assert "PRIVATE_first" == result["instructions"]
        stale = next(t for t in await native.get_tools(ctx) if t.name == "lookup")
        original = types.Part.from_function_response(name="load_skill", response=result)
        if mode == "BOUNDED":
            result = await tools["load_skill"].run_async(args={"skill_name": "second"}, tool_context=ctx)
            assert result["unloaded_skills"] == ["first"]
            assert active_skill_names(native, ctx) == ("second",)
        else:
            assert active_skill_names(native, ctx) == ("first",)
        chosen.clear()
        ctx.invocation_id = "two"
        request = LlmRequest(contents=[types.Content(role="user", parts=[original])])
        await native.process_llm_request(tool_context=ctx, llm_request=request)
        assert request.contents[0].parts[0].function_response.response["status"] == "unloaded"
        assert original.function_response.response["instructions"] == "PRIVATE_first"
        if mode == "EPHEMERAL":
            # Stored state deliberately still contains first; public activity does not.
            assert ctx.state["_adk_activated_skill_host"] == ["first"]
            assert not active_skill_names(native, ctx)
            assert not await binding.discover(ctx)
            assert "lookup" not in {t.name for t in await native.get_tools(ctx)}
            assert (await stale.run_async(args={}, tool_context=ctx))["error_code"] == "CAPABILITY_DENIED"
        else:
            assert await binding.discover(ctx) == {"second"}
    asyncio.run(exercise())
