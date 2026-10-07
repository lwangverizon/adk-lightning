"""Retained native sessions can switch skill -> workflow -> skill without routing."""
import asyncio

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel, Field

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_policy import CapabilityDecision


class Input(BaseModel):
    query: str


class Model(BaseLlm):
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        index = max(i for i, c in enumerate(request.contents)
                    if c.role == "user" and any(p.text for p in c.parts or []))
        current = request.contents[index:]
        query = next(p.text for p in current[0].parts if p.text)
        responses = [p.function_response for c in current for p in c.parts or [] if p.function_response]
        if responses:
            part = types.Part(text="Parent answer")
        elif query == "workflow":
            part = types.Part(function_call=types.FunctionCall(name="lookup", args={"query": query}))
        else:
            part = types.Part(function_call=types.FunctionCall(name="load_skill", args={"skill_name": query}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class Policy:
    def expose(self, context, catalog):
        return [d.name for d in catalog if not context.state.get("deny_" + d.name)]

    def authorize(self, context, request):
        return CapabilityDecision(True)


def test_retained_session_switches_between_peers_and_rechecks_stale_skill_tool():
    from types import SimpleNamespace
    from adkl.capability_execution import guarded_tool
    from google.adk.tools.function_tool import FunctionTool

    effects = []

    def lookup(ctx, node_input: Input):
        effects.append(node_input.query)
        return {"evidence": "fixture"}

    def read_record():
        effects.append("read")
        return {"record": "fixture"}

    skills = [Skill(frontmatter=Frontmatter(name=name, description=name,
        metadata={"adk_additional_tools": ["read_record"]}), instructions="PRIVATE_" + name)
        for name in ("help-a", "help-b")]
    binding = CapabilityBindings(skills=skills, skill_tools=[read_record], policy=Policy(), workflows=[
        Workflow(name="lookup", description="Read evidence", input_schema=Input,
                 edges=[("START", FunctionNode(name="lookup_node", func=lookup))])])
    model = Model(model="fixture")
    agent = LlmAgent(name="host", model=model, **binding.agent_kwargs())

    async def run():
        runner = InMemoryRunner(agent=agent, app_name="journey")
        try:
            session = await runner.session_service.create_session(app_name="journey", user_id="u")
            for message in ("help-a", "workflow", "help-b"):
                async for _ in runner.run_async(user_id="u", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text=message)])):
                    pass
            saved = await runner.session_service.get_session(app_name="journey", user_id="u", session_id=session.id)
            assert saved.state["_adk_activated_skill_host"] == ["help-a", "help-b"]
        finally:
            await runner.close()

        # Simulate a canonical tool obtained while active but invoked after
        # trusted state revokes eligibility. It must not run the business tool.
        tool = guarded_tool(binding, FunctionTool(read_record), ["help-a", "help-b"])
        ctx = SimpleNamespace(state={"_adk_activated_skill_host": ["help-a"],
            "deny_help-a": True, "deny_help-b": True}, invocation_id="next",
            agent_name="host", function_call_id="stale")
        result = await tool.run_async(args={}, tool_context=ctx)
        assert result["error_code"] == "CAPABILITY_DENIED"

    asyncio.run(run())
    assert effects == ["workflow"]
    assert len(model.requests) == 6
    assert "PRIVATE_help-a" not in str(model.requests[0])
    assert "PRIVATE_help-b" not in str(model.requests[0])
