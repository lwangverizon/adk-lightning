"""Real native workflow turns through Console, without providers or services."""
import asyncio
import sys
from types import ModuleType

import pytest
import yaml
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_policy import CapabilityDecision, CapabilityOutcome
from adkl.host import HostChatSession


class Inputs(BaseModel):
    query: str


class Model(BaseLlm):
    model: str = "scripted"
    calls: int = 0

    async def generate_content_async(self, request, stream=False):
        self.calls += 1
        if self.calls == 1:
            part = types.Part(function_call=types.FunctionCall(
                name="device_discovery", args={"query": "PRIVATE_ARGUMENT"}))
        else:
            part = types.Part(text="Public answer")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class Policy:
    def __init__(self, deny):
        self.deny = deny

    def expose(self, context, catalog):
        return [d.name for d in catalog]

    def authorize(self, context, request):
        return CapabilityDecision(not self.deny, "fixture_policy")


def session(root, monkeypatch, *, mode="success", name="probe"):
    root.mkdir(exist_ok=True)
    nodes, observations = [], []

    async def lookup(ctx, node_input: Inputs):
        await asyncio.sleep(0)
        nodes.append(node_input.query)
        if mode == "failure":
            raise RuntimeError("fixture failure")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return {"status": "ok", "private": "PRIVATE_RESULT"}

    def observer(event):
        observations.append(event)
        if mode == "observer_failure":
            raise RuntimeError("observer failure must not alter execution")

    workflow = Workflow(name="device_discovery", description="Find devices",
        input_schema=Inputs, edges=[("START", FunctionNode(name="lookup", func=lookup))])
    def outcome(request, result):
        # Native NodeTool returns errors as values on this ADK baseline. The
        # explicit fixture contract classifies them; ADKL must not infer business
        # failure from arbitrary prose or replace native recovery semantics.
        if isinstance(result, str) and result.startswith("Error running node "):
            return CapabilityOutcome("failed", "fixture_execution_error")
        return CapabilityOutcome()

    binding = CapabilityBindings(workflows=[workflow], policy=Policy(mode == "denied"),
                                 on_event=observer,
                                 outcome_adapters={workflow.name: outcome})
    model = Model()
    agent = LlmAgent(name=name, model=model, **binding.agent_kwargs())
    module_name = "console_fixture_" + name
    module = ModuleType(module_name)
    module.build = lambda: agent
    monkeypatch.setitem(sys.modules, module_name, module)
    (root / "agent.yaml").write_text(yaml.safe_dump({"version": 1,
        "agent": {"name": name, "factory": module_name + ":build"}}), encoding="utf-8")
    return HostChatSession(root, env={}), nodes, observations, model


@pytest.mark.parametrize("mode,terminal", [
    ("success", "completed"), ("denied", "denied"),
    ("failure", "failed"), ("observer_failure", "completed"),
])
def test_console_workflow_outcomes_and_followup(tmp_path, monkeypatch, mode, terminal):
    async def run():
        chat, nodes, observed, model = session(tmp_path, monkeypatch, mode=mode)
        try:
            result = await chat.send("Find phones")
            execution = result["trace"]["execution"]
            events = [e for e in execution["events"] if e["type"] == "capability"]
            assert events[-1]["status"] == terminal
            assert [e["status"] for e in events] == [e.status for e in observed]
            assert bool(nodes) is (mode != "denied")
            assert "PRIVATE_ARGUMENT" not in str(execution)
            assert "PRIVATE_RESULT" not in str(execution)
            node_events = [e for e in execution["events"] if e["type"] == "node_output"]
            if mode in {"success", "observer_failure"}:
                assert node_events
                assert all(e["status"] == "output_observed" for e in node_events)
            assert execution["preparedModelRequests"] == model.calls
            assert execution["preparedModelRequests"] > 0
            assert result["trace"]["matches"] == []
            assert result["trace"]["boundTools"] == []
            assert any("device_discovery" in e.get("tools", []) for e in execution["events"])
            assert all(e["agent"] == "probe" for e in events)
            followup = await chat.send("Thanks")
            assert followup["reply"] == "Public answer"
            assert not any(e["type"] == "capability"
                           for e in followup["trace"]["execution"]["events"])
            assert followup["trace"]["execution"]["preparedModelRequests"] == 1
        finally:
            await chat._runner.close()
    asyncio.run(run())


def test_parallel_console_sessions_are_isolated(tmp_path, monkeypatch):
    async def run():
        a, *_ = session(tmp_path / "a", monkeypatch, name="alpha")
        b, *_ = session(tmp_path / "b", monkeypatch, name="beta", mode="denied")
        try:
            results = await asyncio.gather(a.send("A"), b.send("B"))
            for name, result in zip(["alpha", "beta"], results):
                events = result["trace"]["execution"]["events"]
                assert {e["agent"] for e in events if e["type"] != "node_output"} == {name}
                nodes = [e for e in events if e["type"] == "node_output"]
                assert bool(nodes) is (name == "alpha")
        finally:
            await a._runner.close()
            await b._runner.close()
    asyncio.run(run())


def test_native_cancellation_is_observed_and_collector_does_not_leak(tmp_path, monkeypatch):
    from adkl.console_trace import _current

    async def run():
        chat, _, observed, _ = session(tmp_path, monkeypatch, mode="cancel")
        try:
            # This native Runner consumes root cancellation; Console must not
            # manufacture a different execution outcome or leak the collector.
            result = await chat.send("Cancel workflow")
            assert observed[-1].status == "cancelled"
            assert any(e.get("status") == "cancelled"
                       for e in result["trace"]["execution"]["events"])
            assert _current.get() is None
        finally:
            await chat._runner.close()
    asyncio.run(run())
