"""Native hosts retain exact toolset identity and execution ownership."""
import asyncio
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools.skill_toolset import SkillToolset

from adkl.bindings import RuntimeBindings
from adkl.host import describe_agent
from adkl.inspection import attach, bindings
from adkl.native_authoring import Workspace
from adkl.widgets import Role, emit_staged, resolve_contract
from tests.support.native import ScriptModel, plain_skill, run


def test_inspection_attachment_never_changes_execution(tmp_path):
    native = SkillToolset(skills=[plain_skill("help")])
    agent = LlmAgent(name="host", model=ScriptModel(), tools=[native])
    attach(agent, skills=[plain_skill("help")])
    assert agent.tools == [native]
    assert agent.before_agent_callback is None
    report = describe_agent(agent)
    assert report["adkl"][0]["executionOwnership"]["skills"] == "host"
    assert "_adkl_inspection_bindings" not in agent.model_dump()
    workspace = Workspace(tmp_path, {}, {"agent": report})
    assert workspace.mapping_findings()[0]["rule"] == "AUTHORING_SOURCE_UNMAPPED"
    with pytest.raises(ValueError, match="already"):
        attach(agent, skills=[])


def test_native_binding_preserves_toolset_callbacks_activation_and_model():
    native = SkillToolset(skills=[plain_skill("help")])
    observed = []
    def observe(context):
        observed.append(context.invocation_id)
        return {"activated": ["help"]}
    binding = RuntimeBindings(skills=[plain_skill("help")], native_toolset=native,
                              native_observation=observe, feature_config={"pipeline": []})
    model = ScriptModel()
    original = {"name": "host", "model": model, "tools": [native], "instruction": "Host policy"}
    kwargs = binding.merge_into(original)
    assert kwargs["tools"] == original["tools"] == [native]
    agent = LlmAgent(**kwargs)
    assert bindings(agent) == [binding]
    assert binding.inspect()["features"] == []
    _, session = asyncio.run(run(agent, {"_adk_activated_skill_host": ["help"], "today": "host date"}))
    assert observed
    assert session.state["_adk_activated_skill_host"] == ["help"]
    assert session.state["today"] == "host date"
    assert session.state["adkl:last_turn:host"]["execution_owner"] == "host"
    assert "Host policy" in model.requests[0]
    assert not any("SelectedSkillToolset" in name for names in describe_agent(agent)["callbackOrder"].values() for name in names)


def test_native_binding_rejects_competing_skill_policy():
    native = SkillToolset(skills=[])
    with pytest.raises(ValueError, match="owns selection"):
        RuntimeBindings(skills=[], native_toolset=native,
                        feature_config={"pipeline": ["skill_retrieval"]})


def test_adopted_register_shape_agrees_between_contract_and_emission(monkeypatch):
    from adkl import widgets
    seen = []
    async def build(context, items, request_id):
        seen.append(items)
        return [{"type": "card", "title": items["title"]}]
    def empty(context, items):
        assert isinstance(items, dict)
        return False
    source = SimpleNamespace(key="card", state_items="register", state_dirty="dirty",
                             state_emitted="emitted", presentation_role=Role.DATA_PRIMARY,
                             build=build, expected_empty=empty)
    monkeypatch.setattr(widgets, "_resolve_attr", lambda *a, **kw: [source])
    specs = widgets.adopt("fixture:specs")
    state = {"register": {"title": "Native"}, "dirty": True}
    assert resolve_contract(state, specs).contract is not None
    delivered = []
    asyncio.run(emit_staged(SimpleNamespace(state=state), specs,
                           sink=lambda ctx, ident, payload: delivered.append(payload)))
    assert seen == [{"title": "Native"}]
    assert delivered[0]["title"] == "Native"
    assert state["emitted"] is True


def test_inspection_catalog_preserves_real_native_tool_parameters(tmp_path):
    from google.adk.tools import FunctionTool

    def quiz(question: str, from_bank: bool = False) -> dict:
        """Stage a question for the student."""
        return {"question": question, "from_bank": from_bank}

    tool = FunctionTool(quiz)
    agent = LlmAgent(name="host", model=ScriptModel(), tools=[tool])
    attach(agent, skills=[], tools=[tool])
    workspace = Workspace(tmp_path, {}, {"agent": describe_agent(agent)})
    catalog = workspace.catalog()["tools"][0]
    assert catalog["schemaKnown"] is True
    assert {p["name"] for p in catalog["params"]} == {"question", "from_bank"}
    assert next(p for p in catalog["params"] if p["name"] == "question")["required"]
    assert catalog["source"] == "native_factory"
    assert catalog["sideEffectInferred"] is False


def test_native_workspace_lists_declared_workflows_without_running_them(tmp_path):
    from pydantic import BaseModel
    from google.adk.workflow import FunctionNode, Workflow
    from adkl.capability_bindings import CapabilityBindings

    class Input(BaseModel):
        query: str

    async def lookup(ctx, node_input: Input):
        raise AssertionError("Inspection must not execute workflows")

    flow = Workflow(name="device_discovery", description="Find available devices",
                    input_schema=Input, edges=[("START", FunctionNode(name="lookup", func=lookup))])
    binding = CapabilityBindings(workflows=[flow])
    agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
    inventory = Workspace(tmp_path, {}, {"agent": describe_agent(agent)}).workflows
    assert [(w["name"], w["description"], w["agent"]) for w in inventory] == [
        ("device_discovery", "Find available devices", "host")]
    graph = inventory[0]["graph"]
    assert graph["available"] is True
    assert {n["name"] for n in graph["nodes"]} == {"__START__", "lookup"}
    assert [(e["from"], e["to"], e["route"]) for e in graph["edges"]] == [
        ("__START__", "lookup", None)]
    assert Workspace(tmp_path, {}, {"agent": describe_agent(
        LlmAgent(name="plain", model=ScriptModel()))}).workflows == []
    # Host-owned workflows use the public inspection-only attachment; no callback
    # replacement, closure discovery, or tool execution is needed.
    declared = LlmAgent(name="declared", model=ScriptModel())
    attach(declared, skills=[], workflows=[flow])
    assert declared.before_agent_callback is None
    assert declared.tools == []
    attached = Workspace(tmp_path, {}, {"agent": describe_agent(declared)}).workflows
    assert attached[0]["graph"] == graph
    assert attached[0]["operation"] is None


def test_native_workflow_graph_keeps_branches_and_parallel_joins(tmp_path):
    from pydantic import BaseModel
    from google.adk.workflow import FunctionNode, Workflow
    from adkl.capability_bindings import CapabilityBindings

    class Input(BaseModel):
        query: str

    async def step(ctx, node_input: Input):
        raise AssertionError("Graph inspection must not execute nodes")

    choose = FunctionNode(name="choose", func=step)
    left = FunctionNode(name="left", func=step)
    right = FunctionNode(name="right", func=step)
    join = FunctionNode(name="join", func=step)
    flow = Workflow(name="branches", description="Branch and join", input_schema=Input,
                    edges=[("START", choose), (choose, {"a": left, "b": right}),
                           (left, join), (right, join)])
    agent = LlmAgent(name="host", model=ScriptModel(),
                     **CapabilityBindings(workflows=[flow]).agent_kwargs())
    graph = Workspace(tmp_path, {}, {"agent": describe_agent(agent)}).workflows[0]["graph"]
    assert {n["name"] for n in graph["nodes"]} == {"__START__", "choose", "left", "right", "join"}
    assert {(e["from"], e["to"], e["route"]) for e in graph["edges"]} == {
        ("__START__", "choose", None), ("choose", "left", "a"),
        ("choose", "right", "b"), ("left", "join", None),
        ("right", "join", None)}


def test_native_workflow_graph_keeps_repair_loop(tmp_path):
    from pydantic import BaseModel
    from google.adk.workflow import FunctionNode, Workflow
    from adkl.capability_bindings import CapabilityBindings

    class Input(BaseModel):
        query: str

    async def step(ctx, node_input: Input):
        raise AssertionError("Inspection must not run the repair loop")

    select = FunctionNode(name="select", func=step)
    validate = FunctionNode(name="validate", func=step)
    repair = FunctionNode(name="repair", func=step)
    finish = FunctionNode(name="finish", func=step)
    flow = Workflow(name="devices", description="Find devices", input_schema=Input,
                    edges=[("START", select), (select, validate),
                           (validate, {"repair": repair, "done": finish}),
                           (repair, select)])
    agent = LlmAgent(name="host", model=ScriptModel(),
                     **CapabilityBindings(workflows=[flow]).agent_kwargs())
    edges = Workspace(tmp_path, {}, {"agent": describe_agent(agent)}).workflows[0]["graph"]["edges"]
    assert ("repair", "select", None) in {
        (edge["from"], edge["to"], edge["route"]) for edge in edges}
