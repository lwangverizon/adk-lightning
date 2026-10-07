"""Host integration contracts across native turns and concurrent agents."""
import asyncio
from datetime import date
from unittest.mock import patch

from google.adk.agents import LlmAgent, ParallelAgent
from adkl.bindings import RuntimeBindings
from adkl.retrieval import ACTIVATED_KEY, Match, MatchResult
from test_bindings import ScriptModel, plain_skill, run
import pytest


def test_route_preserves_selector_pin():
    async def case():
        class Selector:
            async def match(self, text, state):
                return MatchResult([Match("next", 1., "embedding"),
                                    Match("flow", .2, "embedding")],
                                   pinned=frozenset({"flow"}))
        binding = RuntimeBindings(
            skills=[plain_skill("next"), plain_skill("flow")], selector=Selector(),
            route_skill=lambda **kw: "next",
            feature_config={"pipeline": ["skill_retrieval"], "skill_retrieval": {
                "auto_activate": True, "continue_active": True,
                "min_skills_for_retrieval": 0}})
        key = ACTIVATED_KEY.format(agent="host")
        _, session = await run(LlmAgent(name="host", model=ScriptModel(),
                                       **binding.agent_kwargs()), {key: ["flow"]})
        assert "flow" in session.state[key], session.state[key]
    asyncio.run(case())


def test_adkl_seeded_date_refreshes():
    async def case():
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="host", user_id="user")
        runner = Runner(agent=agent, app_name="host", session_service=sessions)
        async def turn():
            async for _ in runner.run_async(user_id="user", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="hello")])):
                pass
            return await sessions.get_session(app_name="host", user_id="user", session_id=session.id)
        with patch("adkl.skill_runtime.date") as clock:
            clock.today.return_value = date(2026, 9, 8)
            first = await turn()
            assert first.state["today"] == "2026-09-08"
            clock.today.return_value = date(2026, 9, 9)
            second = await turn()
        await runner.close()
        assert second.state["today"] == "2026-09-09", second.state["today"]
    asyncio.run(case())


def test_parallel_bindings_keep_their_selection():
    async def case():
        beta_ready, alpha_observed = asyncio.Event(), asyncio.Event()
        class Selector:
            def __init__(self, name):
                self.name = name
            async def match(self, text, state):
                if self.name == "alpha":
                    await asyncio.wait_for(beta_ready.wait(), 5)
                return MatchResult([Match(self.name, 1., "embedding")])
        async def hold_beta(callback_context, llm_request):
            beta_ready.set()
            await asyncio.wait_for(alpha_observed.wait(), 5)
        def observe_alpha(callback_context, llm_request):
            alpha_observed.set()
        agents, models = [], []
        for name in ("alpha", "beta"):
            binding = RuntimeBindings(skills=[plain_skill(name)], selector=Selector(name),
                feature_config={"pipeline": ["skill_retrieval"],
                                "skill_retrieval": {"continue_active": True}})
            model = ScriptModel()
            kwargs = binding.agent_kwargs(
                callbacks={"before_model_callback": [hold_beta]} if name == "beta" else {},
                before_model_observers=[observe_alpha] if name == "alpha" else [])
            agents.append(LlmAgent(name=name, model=model, **kwargs))
            models.append(model)
        _, session = await run(ParallelAgent(name="root", sub_agents=agents), {
            ACTIVATED_KEY.format(agent=name): [name] for name in ("alpha", "beta")})
        assert "Active skill: beta" in models[1].requests[0], models[1].requests[0]
        assert "Active skill: alpha" in models[0].requests[0]
        for name in ("alpha", "beta"):
            assert session.state[f"adkl:last_turn:{name}"]["activated"] == [name]
            assert session.state[f"adkl:last_turn:{name}"]["model_calls"] == 1
    asyncio.run(case())


def test_parallel_agents_prepare_the_same_skill_independently():
    async def case():
        ready = asyncio.Event()
        prepared = []
        class Selector:
            def __init__(self, second):
                self.second = second
            async def match(self, text, state):
                if self.second:
                    await asyncio.wait_for(ready.wait(), 5)
                return MatchResult([Match("shared", 1., "embedding")])
        async def prepare(*, skill_name, context, reason):
            prepared.append(context.agent_name)
        def after_first(callback_context, llm_request):
            ready.set()
        agents = []
        for name in ("alpha", "beta"):
            binding = RuntimeBindings(skills=[plain_skill("shared")],
                selector=Selector(name == "beta"), prepare_skill=prepare,
                feature_config={"pipeline": ["skill_retrieval"],
                                "skill_retrieval": {"continue_active": True}})
            agents.append(LlmAgent(name=name, model=ScriptModel(), **binding.agent_kwargs(
                before_model_observers=[after_first] if name == "alpha" else [])))
        await run(ParallelAgent(name="root", sub_agents=agents), {
            ACTIVATED_KEY.format(agent=name): ["shared"] for name in ("alpha", "beta")})
        assert prepared == ["alpha", "beta"]
    asyncio.run(case())


@pytest.mark.parametrize("state", [{}, {"today": "host value"},
    {"today": "2026-09-08", "adkl:default_today": "2026-09-08"}])
def test_host_can_disable_date_seeding(state):
    async def case():
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []}, seed_today=False)
        _, session = await run(LlmAgent(name="host", model=ScriptModel(),
                                       **binding.agent_kwargs()), state)
        assert session.state.get("today") == state.get("today")
    asyncio.run(case())


def test_host_date_override_relinquishes_default():
    async def case():
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        _, session = await run(LlmAgent(name="host", model=ScriptModel(),
            **binding.agent_kwargs()), {"today": "host calendar", "adkl:default_today": "2026-09-08"})
        assert session.state["today"] == "host calendar"
        assert session.state["adkl:default_today"] is None
    asyncio.run(case())


@pytest.mark.parametrize("pins", [frozenset({"missing"}), ["flow"], None])
def test_invalid_selector_pins_fail_before_model(pins):
    async def case():
        class Selector:
            async def match(self, text, state):
                return MatchResult([Match("flow", 1., "embedding")], pinned=pins)
        model = ScriptModel()
        binding = RuntimeBindings(skills=[plain_skill("flow")], selector=Selector(),
                                  feature_config={"pipeline": ["skill_retrieval"],
                                      "skill_retrieval": {"min_skills_for_retrieval": 0}})
        with pytest.raises(ValueError, match="pins must name exposed"):
            await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        assert not model.requests
    asyncio.run(case())


@pytest.mark.parametrize("limit,count,retained", [(0, 1, False), (1, 1, True), (1, 2, False)])
def test_configured_pin_retention(limit, count, retained):
    from test_bindings import run_turns
    async def case():
        class Selector:
            async def match(self, text, state):
                return MatchResult([Match("next", 1., "embedding"), Match("flow", .2, "embedding")],
                                   pinned=frozenset({"flow"}))
        binding = RuntimeBindings(skills=[plain_skill("next"), plain_skill("flow")],
            selector=Selector(), feature_config={"pipeline": ["skill_retrieval"],
                "skill_retrieval": {"continue_active": True, "pin_grace_turns": limit,
                                    "min_skills_for_retrieval": 0}})
        key = ACTIVATED_KEY.format(agent="host")
        session = await run_turns(LlmAgent(name="host", model=ScriptModel(),
            **binding.agent_kwargs()), count, {key: ["flow"]})
        assert ("flow" in session.state[key]) is retained
    asyncio.run(case())


def test_final_transform_runs_after_procedure_and_before_observer():
    async def case():
        seen = []
        def transform(callback_context, llm_request):
            assert "Active skill: flow" in str(llm_request.config.system_instruction)
            llm_request.append_instructions(["Host gateway normalization"])
            seen.append("transform")
        async def observe(callback_context, llm_request):
            assert "Host gateway normalization" in str(llm_request.config.system_instruction)
            seen.append("observer")
        binding = RuntimeBindings(skills=[plain_skill("flow")], feature_config={"pipeline": []})
        model = ScriptModel()
        kwargs = binding.merge_into({"name": "host", "model": model},
            final_model_transforms=[transform], before_model_observers=[observe])
        await run(LlmAgent(**kwargs), {ACTIVATED_KEY.format(agent="host"): ["flow"]})
        assert seen == ["transform", "observer"]
        assert "Host gateway normalization" in model.requests[0]
        assert binding.inspect()["finalModelTransforms"] == 1
    asyncio.run(case())


def test_final_transform_cannot_silently_short_circuit():
    async def case():
        model = ScriptModel()
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        kwargs = binding.agent_kwargs(final_model_transforms=[lambda **kw: "bad response"])
        with pytest.raises(TypeError, match="must mutate the request and return None"):
            await run(LlmAgent(name="host", model=model, **kwargs))
        assert not model.requests
    asyncio.run(case())


def test_host_injection_keeps_recorded_contract_and_delivery():
    from adkl.widgets import WidgetSpec, Role, stage, Contract
    async def case():
        async def build(ctx, items, rid):
            return [{"type": "fact"}]
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact",
                          role=Role.DATA_PRIMARY, build=build)
        def stage_card(callback_context):
            stage(callback_context.state, spec, ["fact"])
        delivered = []
        def sink(ctx, wid, payload):
            delivered.append(payload)
        model = ScriptModel()
        binding = RuntimeBindings(skills=[], widget_specs=[spec], widget_sink=sink,
            feature_config={"pipeline": ["staged_widgets"], "staged_widgets": {"inject_contract": False}})
        _, session = await run(LlmAgent(name="host", model=model, **binding.agent_kwargs(
            callbacks={"before_agent_callback": [stage_card]})))
        assert session.state["adkl:widgets:host"]["contract"] == Contract.SYNTHESIS.value
        assert session.state["adkl:widgets:host"]["sources"] == ["card"]
        assert "<presentation_contract" not in model.requests[0]
        assert delivered == [{"type": "fact"}]
    asyncio.run(case())


def test_adoption_loss_is_visible_to_host_inspection_and_audit():
    from types import SimpleNamespace
    from adkl import widgets
    from adkl.host import describe_agent
    async def build(ctx, items, rid):
        return items
    source = SimpleNamespace(key="card", payload_type="fact", build=build,
                             presentation_role="host_only")
    with patch.object(widgets, "_resolve_attr", return_value=[source]), pytest.warns(UserWarning):
        specs = widgets.adopt("host:REGISTRY")
    binding = RuntimeBindings(skills=[], widget_specs=specs,
                              feature_config={"pipeline": ["staged_widgets"]})
    agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
    snapshot = describe_agent(agent)["adkl"][0]
    assert snapshot["widgets"][0]["payloadType"] == "fact"
    assert "host_only" in snapshot["widgets"][0]["adoptionWarnings"][0]
    assert any(f["rule"] == "WIDGET_ADOPTION_LOSS" for f in snapshot["audit"]["findings"])
