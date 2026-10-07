"""Diagnostics on the HOST path, which is the only path that can run in prod.

`_compose_callbacks` used to read `_record_output_findings` and `root` off `self`
with getattr, and only `AgentRuntime` -- the console/authoring class -- defines
either. So on every `RuntimeBindings` path both resolved to None: PII redaction
ran with no audit trail, a file-backed context slot died on a TypeError raised
three modules deep, and the four warnings `features.resolve` computes went to
nobody. These tests pin the host end of each channel, because the console tests
that cover the same code cannot fail when the host wiring is missing.
"""
import asyncio

import pytest
from google.adk.agents import LlmAgent

from adkl.bindings import RuntimeBindings
from test_bindings import ScriptModel, Selector, plain_skill, run


class LeakyModel(ScriptModel):
    """Answers with something the redactor must both rewrite and report."""

    async def generate_content_async(self, request, stream=False):
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part(text="your ssn 123-45-6789 is on file")]))


class _Ctx:
    """The three attributes `emit_staged` and the wrapper actually read."""

    def __init__(self):
        self.state, self.agent_name = {}, "host"


def test_resolution_warnings_reach_a_host_callback():
    notes = []
    RuntimeBindings(skills=[], on_warning=notes.append, feature_config={
        "pipeline": ["output_cleanup"],
        "output_cleanup": {"enabled": False}}).agent_kwargs()
    assert notes, "the kill-switch warning was computed and dropped"
    assert "INACTIVE" in "\n".join(notes[0])


def test_resolution_warnings_are_also_readable_without_a_callback():
    """A host that wants them at startup, not per-composition."""
    binding = RuntimeBindings(skills=[plain_skill("flow")], selector=Selector(),
        feature_config={"pipeline": ["skill_retrieval"],
                        "skill_retrieval": {"pin_grace_turns": 3}})
    assert any("inert" in w for w in binding.resolution_warnings())


def test_a_clean_configuration_stays_quiet():
    notes = []
    binding = RuntimeBindings(skills=[], on_warning=notes.append,
                              feature_config={"pipeline": []})
    binding.agent_kwargs()
    assert notes == [] and binding.resolution_warnings() == []


def test_output_findings_reach_a_host_callback():
    """The compliance-relevant one: redaction that nobody can audit."""
    async def case():
        findings = []
        binding = RuntimeBindings(skills=[], on_finding=findings.append,
            feature_config={"pipeline": ["pii_redaction"]})
        model = LeakyModel()
        _, session = await run(LlmAgent(name="host", model=model,
                                        **binding.agent_kwargs()))
        assert findings, "redaction fired and reported to nobody"
        assert any("ssn" in note for group in findings for note in group), findings
    asyncio.run(case())


def test_a_file_backed_context_slot_resolves_against_the_hosts_root(tmp_path):
    (tmp_path / "policy.txt").write_text("no refunds", encoding="utf-8")
    binding = RuntimeBindings(skills=[], root=tmp_path, feature_config={
        "pipeline": ["prompt_state_injection"],
        "prompt_state_injection": {"slots": {"policy": {"from_file": "policy.txt"}}}})
    async def case():
        model = ScriptModel()
        await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        assert "no refunds" in model.requests[0]
    asyncio.run(case())


def test_a_file_backed_slot_without_a_root_says_so():
    """It used to be `TypeError: unsupported operand type(s) for /: 'NoneType'`."""
    with pytest.raises(ValueError, match="needs a project root"):
        RuntimeBindings(skills=[], feature_config={
            "pipeline": ["prompt_state_injection"],
            "prompt_state_injection": {"slots": {"policy": {"from_file": "policy.txt"}}},
        }).agent_kwargs()


@pytest.mark.parametrize("kind", ["on_finding", "on_warning"])
def test_a_non_callable_diagnostic_hook_is_refused_at_construction(kind):
    with pytest.raises(TypeError, match=kind):
        RuntimeBindings(skills=[], feature_config={"pipeline": []}, **{kind: "log"})


def _faulting_widget_binding(cleaned, **kw):
    """A staged-widget binding whose emission raises out of `emit_staged`.

    A broken `build` is NOT the fault to use here: `emit_staged` gate 5 already
    catches that and costs the widget rather than the turn. A sink returning a
    value is the fault that actually escapes, and it is the realistic one -- the
    sink is host code.
    """
    from adkl.widgets import WidgetSpec, Role, stage
    async def build(ctx, items, rid):
        return [{"type": "fact"}]
    spec = WidgetSpec(key="card", widget_id="card", payload_type="fact",
                      role=Role.DATA_PRIMARY, build=build)
    binding = RuntimeBindings(skills=[], widget_specs=[spec],
        widget_sink=lambda ctx, wid, payload: "not None",
        feature_config={"pipeline": ["staged_widgets"],
                        "staged_widgets": {"inject_contract": False}}, **kw)
    def cleanup(callback_context=None, **_):
        cleaned.append(callback_context.agent_name)
        return None
    wrapper = binding.agent_kwargs(
        callbacks={"after_agent_callback": [cleanup]})["after_agent_callback"][0]
    ctx = _Ctx()
    stage(ctx.state, spec, ["fact"])
    return wrapper, ctx


def test_an_emitter_fault_still_runs_host_completion_hooks():
    """Turn-context leak path: the wrapper REPLACES the host's after_agent chain.

    A raise from widget emission therefore skipped every host completion hook,
    including the `finally`-based per-turn cleanup that is the ordinary way a
    host frees turn-scoped resources -- and nothing outside ADKL could repair it.
    """
    cleaned, findings = [], []
    wrapper, ctx = _faulting_widget_binding(cleaned, on_finding=findings.append)
    assert asyncio.run(wrapper(ctx)) is None
    assert cleaned == ["host"], "host cleanup was skipped by the emitter fault"
    assert any("emission failed" in note
               for group in findings for note in group), findings


def test_an_unreported_emitter_fault_is_not_swallowed():
    """With no finding channel, silence would be worse than the raise."""
    from adkl.widgets import WidgetError
    cleaned = []
    wrapper, ctx = _faulting_widget_binding(cleaned)
    with pytest.raises(WidgetError):
        asyncio.run(wrapper(ctx))
    assert cleaned == ["host"], "cleanup must run before the fault propagates"


def test_inspection_reports_resolution_warnings_to_a_host():
    from adkl.host import describe_agent
    binding = RuntimeBindings(skills=[plain_skill("flow")], selector=Selector(),
        feature_config={"pipeline": ["skill_retrieval"],
                        "skill_retrieval": {"max_activated_skills": 2}})
    agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
    assert any("inert" in w for w in describe_agent(agent)["adkl"][0]["warnings"])
