"""A real mixed shortlist, skill load/tool and workflow in one Console turn."""
import asyncio
import sys
from types import ModuleType

import yaml
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection
from adkl.host import HostChatSession


class Inputs(BaseModel):
    query: str


class MixedModel(BaseLlm):
    model: str = "scripted"
    calls: int = 0

    async def generate_content_async(self, request, stream=False):
        calls = [
            ("load_skill", {"skill_name": "records"}),
            ("lookup", {}),
            ("device_discovery", {"query": "PRIVATE_QUERY"}),
        ]
        if self.calls < len(calls):
            name, args = calls[self.calls]
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        else:
            part = types.Part(text="Done")
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def test_mixed_retrieval_and_execution_are_distinct(tmp_path, monkeypatch):
    ranks = []
    executed = []

    def rank(context, catalog):
        ranks.append(tuple(d.kind for d in catalog))
        return CapabilitySelection(tuple(d.name for d in catalog))

    def lookup() -> dict:
        """Read a fixture."""
        executed.append("skill")
        return {"value": "PRIVATE_RESULT"}

    async def node(ctx, node_input: Inputs):
        executed.append("workflow")
        return {"value": "PRIVATE_RESULT"}

    skill = Skill(frontmatter=Frontmatter(name="records", description="Read records",
        metadata={"adk_additional_tools": ["lookup"]}), instructions="Call lookup.")
    workflow = Workflow(name="device_discovery", description="Find devices",
        input_schema=Inputs, edges=[("START", FunctionNode(name="search", func=node))])
    binding = CapabilityBindings(skills=[skill], skill_tools=[lookup],
        workflows=[workflow], retrieval=CapabilityRetrieval(rank))
    model = MixedModel()
    agent = LlmAgent(name="mixed", model=model, **binding.agent_kwargs())
    module = ModuleType("mixed_console_fixture")
    module.build = lambda: agent
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump({"version": 1,
        "agent": {"name": "mixed", "factory": module.__name__ + ":build"}}),
        encoding="utf-8")

    async def run():
        chat = HostChatSession(tmp_path, env={})
        try:
            frames = [f async for f in chat.stream("Read and search")]
            trace = frames[-1]["result"]["trace"]["execution"]
            events = trace["events"]
            shortlist = [e for e in events if e["type"] == "retrieval"]
            assert len(shortlist) == 1
            assert {c["kind"] for c in shortlist[0]["candidates"]} == {"skill", "workflow"}
            completed = [e for e in events if e.get("status") == "completed"]
            assert [(e["kind"], e["operation"]) for e in completed] == [
                ("skill", "load_skill"), ("skill", "lookup"),
                ("workflow", "device_discovery")]
            assert any(e["type"] == "node_output" for e in events)
            assert trace["preparedModelRequests"] == 4
            assert "PRIVATE_QUERY" not in str(trace)
            assert "PRIVATE_RESULT" not in str(trace)
            assert executed == ["skill", "workflow"]
            assert len(ranks) == 1
        finally:
            await chat._runner.close()
    asyncio.run(run())
