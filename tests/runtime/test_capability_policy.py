"""Eligibility and event contracts exercise real native Runner dispatch."""
import asyncio

import pytest
from google.adk.agents import LlmAgent

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_policy import CapabilityDecision, CapabilityOutcome
from tests.support.native import run
from tests.support.capabilities import ChoiceModel, workflow


class Policy:
    def expose(self, context, catalog):
        return [d.name for d in catalog if not context.state.get("blocked")]

    def authorize(self, context, request):
        return CapabilityDecision(not context.state.get("deny"), "test_policy")


@pytest.mark.parametrize("state,executed", [({}, True), ({"blocked": True}, False), ({"deny": True}, False)])
def test_workflow_guard_and_events(state, executed):
    calls, events = [], []

    def lookup(ctx, node_input):
        calls.append("ran")
        return {"result": "fixture"}

    binding = CapabilityBindings(workflows=[workflow(lookup)], policy=Policy(), on_event=events.append)
    model = ChoiceModel(model="fixture", choice="workflow")
    agent = LlmAgent(name="host", model=model, **binding.agent_kwargs())
    try:
        asyncio.run(run(agent, state))
    except ValueError as exc:
        # Upstream raises for unregistered calls; the private host distribution
        # returns an error tool response. Both must have zero side effects.
        assert state.get("blocked") and "not found" in str(exc)
    assert bool(calls) is executed
    if executed:
        assert [e.status for e in events] == ["requested", "started", "completed"]
    elif not state.get("blocked"):
        assert [e.status for e in events] == ["requested", "denied"]
    else:
        # Native dispatch refuses a tool removed from the request before any
        # adapter executes; no fabricated execution event for an unavailable tool.
        assert events == []
    assert ("device_discovery" in model.requests[0].tools_dict) is not state.get("blocked", False)


def test_policy_failure_closed_and_result_outcome_explicit():
    class Broken(Policy):
        def authorize(self, context, request):
            raise RuntimeError("private")

    def lookup(ctx, node_input):
        return {"status": "rejected"}

    for policy, expected in [(Broken(), "denied"), (Policy(), "failed")]:
        events = []
        binding = CapabilityBindings(workflows=[workflow(lookup)], policy=policy,
            on_event=events.append, outcome_adapters={"device_discovery":
                lambda req, result: CapabilityOutcome("failed", "business_rejected")})
        asyncio.run(run(LlmAgent(name="host", model=ChoiceModel(model="fixture", choice="workflow"),
                                 **binding.agent_kwargs())))
        assert events[-1].status == expected
        assert "private" not in str(events)


def test_workflow_cancellation_has_terminal_event():
    from adkl.capability_execution import execute
    from adkl.capability_policy import CapabilityRequest
    from types import SimpleNamespace

    events = []
    flow = workflow(lambda ctx, node_input: {})
    binding = CapabilityBindings(workflows=[flow], on_event=events.append)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    ctx = SimpleNamespace(state={}, agent_name="host", invocation_id="turn", function_call_id="call__thought__PRIVATE_PROVIDER_DATA",
                          branch=None, run_node=cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute(binding, ctx,
            CapabilityRequest(flow.name, "workflow", flow.name, {"query": "x"}),
            cancelled))
    assert [e.status for e in events] == ["requested", "started", "cancelled"]
    assert all(e.call_id == "call" for e in events)
    assert "PRIVATE_PROVIDER_DATA" not in str(events)


def test_denied_pre_emission_still_completes_host():
    from adkl.widgets import WidgetSpec, Role, stage
    sink, completed = [], []

    async def build(ctx, items, request_id):
        return [(items[0], "ready")]

    spec = WidgetSpec(key="card", widget_id="card", payload_type="fixture", role=Role.DATA_PRIMARY, build=build)

    def begin(callback_context):
        stage(callback_context.state, spec, [{"value": "fixture"}])

    def end(callback_context):
        completed.append(True)

    binding = CapabilityBindings(widget_specs=[spec], feature_config={"pipeline": ["staged_widgets"]},
        widget_sink=lambda *args: sink.append(True), pre_emit=lambda ctx: CapabilityDecision(False, "suppressed"))
    model = ChoiceModel(model="fixture", choice="specialist")
    asyncio.run(run(LlmAgent(name="host", model=model, **binding.agent_kwargs(
        callbacks={"before_agent_callback": begin, "after_agent_callback": end}))))
    assert not sink and completed == [True]


@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
def test_pre_emission_exception_disarms_pending_output(failure):
    from types import SimpleNamespace
    from adkl.widgets import WidgetSpec, Role, stage

    async def build(ctx, items, request_id):
        return [(items[0], "ready")]

    def reject(ctx):
        raise failure()

    spec = WidgetSpec(key="card", widget_id="card", payload_type="fixture",
                      role=Role.DATA_PRIMARY, build=build)
    ctx = SimpleNamespace(state={}, agent_name="host")
    stage(ctx.state, spec, [{"value": "fixture"}])
    sink = []
    binding = CapabilityBindings(widget_specs=[spec], feature_config={"pipeline": ["staged_widgets"]},
        widget_sink=lambda *args: sink.append(True), pre_emit=reject)
    with pytest.raises(failure):
        asyncio.run(binding._make_emitter()(ctx))
    assert not ctx.state.get(spec.state_dirty)
    assert not sink
    assert ctx.state["adkl:widgets:host"]["reason"] == "validation_error"
