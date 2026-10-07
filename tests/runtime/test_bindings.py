"""Host-native construction executes the shared lifecycle without project files."""
import asyncio
import unittest
from types import SimpleNamespace

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import models
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.retrieval import ACTIVATED_KEY, Match, MatchResult
from adkl.skill_runtime import PIN_GRACE_LIMIT
from adkl.widgets import WidgetSpec, Role, stage
from tests.support.native import ScriptModel, plain_skill, run, run_turns


class Selector:
    def __init__(self, auto=True):
        self.auto = auto

    async def match(self, text, state):
        return MatchResult([Match("support", 1.0, "embedding")],
                           auto_activated="support" if self.auto else None)


def skill():
    return models.Skill(frontmatter=models.Frontmatter(
        name="support", description="Answer public questions",
        metadata={"adk_inject_state": True}), instructions="Use {topic} to answer.")


def config(auto=True, staged=False):
    return {"pipeline": ["skill_retrieval", "history_slicing", "skill_state_injection"]
            + (["staged_widgets"] if staged else []),
            "skill_retrieval": {"auto_activate": auto, "min_skills_for_retrieval": 0},
            "history_slicing": {"recent_turns": 1},
            "skill_state_injection": {"skills": {"support": {
                "topic": {"from_state": "topic", "type": "string", "required": True}}}}}


class TestBindings(unittest.IsolatedAsyncioTestCase):
    async def test_merge_preserves_host_constructor_and_native_callback_execution(self):
        calls = []
        def host_tool() -> str:
            return "host"
        def before_agent(callback_context):
            calls.append("host")
        def before_model(callback_context, llm_request):
            calls.append("model")
        original = {"name": "host", "model": ScriptModel(), "instruction": "Host policy",
                    "tools": [host_tool], "before_agent_callback": before_agent,
                    "before_model_callback": [before_model], "after_agent_callback": None}
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        self.assertEqual(binding.inspect(), binding.inspect())
        inspected = binding.inspect()
        inspected["configuration"]["pipeline"] = ["invalid"]
        self.assertEqual(binding.inspect()["features"], [])
        merged = binding.merge_into(original)
        self.assertIs(original["before_agent_callback"], before_agent)
        self.assertEqual(original["tools"], [host_tool])
        self.assertIsNot(merged["tools"], original["tools"])
        self.assertEqual(merged["instruction"], "Host policy")
        await run(LlmAgent(**merged))
        self.assertEqual(calls, ["host", "model"])
        self.assertEqual(binding.inspect()["skills"], [])
        with self.assertRaisesRegex(ValueError, "already issued"):
            binding.merge_into(original)

    async def test_merge_refuses_promoting_skill_tools_and_duplicate_lifecycles(self):
        def scoped() -> str:
            return "private"
        from google.adk.tools import FunctionTool
        binding = RuntimeBindings(skills=[], tools=[scoped], feature_config={"pipeline": []})
        with self.assertRaisesRegex(ValueError, "binding-owned"):
            binding.merge_into({"tools": [FunctionTool(scoped)]})
        # Failed validation must not consume the binding.
        merged = binding.merge_into({})
        other = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        with self.assertRaisesRegex(ValueError, "skill toolsets"):
            other.merge_into(merged)

    async def check_preparation(self, auto):
        calls = []
        async def prepare(*, skill_name, context, reason):
            calls.append((skill_name, reason))
            context.state["topic"] = "grounded facts"
        def guard(tool, args, tool_context):
            assert tool_context.state["topic"] == "grounded facts"
            calls.append("guard")
        binding = RuntimeBindings(skills=[skill()], feature_config=config(auto),
                                  selector=Selector(auto), prepare_skill=prepare)
        model = ScriptModel(explicit=not auto)
        agent = LlmAgent(name="host", model=model, instruction="Host policy",
                         **binding.agent_kwargs(callbacks={"before_tool_callback": [guard]}))
        _, session = await run(agent)
        assert "Use grounded facts to answer" in model.requests[-1]
        assert '"topic": "grounded facts"' in model.requests[-1]
        assert calls == [("support", "automatic")] if auto else calls == [("support", "explicit"), "guard"]
        assert session.state["adkl:last_turn"]["model_calls"] == (1 if auto else 2)
        with self.assertRaisesRegex(ValueError, "already issued"):
            binding.agent_kwargs()


    async def test_preparation_failure_blocks_auto_activation_and_model(self):
        async def reject(**kwargs):
            raise PermissionError("context unavailable")
        model = ScriptModel()
        binding = RuntimeBindings(skills=[skill()], feature_config=config(),
                                  selector=Selector(), prepare_skill=reject)
        with self.assertRaisesRegex(PermissionError, "context unavailable"):
            await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        assert not model.requests


    async def test_strategy_cannot_enable_disabled_activation_or_unknown_skill(self):
        for result, auto in [(MatchResult([Match("other", 1, "embedding")]), True),
                             (MatchResult([Match("support", 1, "embedding")], auto_activated="support"), False)]:
            class BadSelector:
                async def match(self, text, state):
                    return result
            binding = RuntimeBindings(skills=[skill()], feature_config=config(auto), selector=BadSelector())
            with self.assertRaisesRegex(ValueError, "Selector"):
                await run(LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs()))


    async def test_sink_precedes_content_returning_completion_and_cleans_stale_flags(self):
        delivered = []
        async def build(ctx, items, rid):
            return [({"type": "fact", "request": rid}, "caption")]
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact", role=Role.DATA_PRIMARY,
                          dirty_key="legacy_dirty", emitted_key="legacy_emitted", build=build)
        def begin(callback_context):
            assert callback_context.state["temp:adkl_dropped_stages"] == ["card"]
            stage(callback_context.state, spec, ["fresh"])
        async def sink(ctx, widget_id, payload):
            delivered.append((widget_id, payload))
        def complete(callback_context):
            assert delivered
            return types.Content(role="model", parts=[types.Part(text="Host completion")])
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": ["staged_widgets"]},
                                  widget_specs=[spec], widget_sink=sink,
                                  request_id=lambda ctx: ctx.invocation_id)
        events, session = await run(LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs(
            callbacks={"before_agent_callback": [begin], "after_agent_callback": [complete]})),
            {spec.state_dirty: True, spec.state_items: ["stale"]})
        assert len(delivered) == 1
        assert delivered[0][1]["request"] == events[-1].invocation_id
        assert session.state[spec.state_emitted]
        assert events[-1].content.parts[0].text == "Host completion"


    async def test_companion_sink_replaces_model_content_delivery(self):
        """A host owning its transport must be able to refuse the generic route.

        Without a sink, a companion the model never wrote is returned as *model*
        content, which persists into conversation history -- the next turn reads
        it as its own prior wording and imitates it. So for a host that builds
        the companion turn from the state delta, the generic route is a
        behaviour change, not an alternative delivery of the same sentence.

        Unguarded until now, and this repo lost the hook once already: a host
        passing `companion_sink=` got `TypeError: unexpected keyword argument`
        from the tree while its vendored wheel accepted it, which reads as a
        host bug and is not one.
        """
        async def build(ctx, items, rid):
            return [({"type": "fact"}, "Here is the card.")]
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact",
                          role=Role.DATA_PRIMARY, build=build)

        class SilentModel(BaseLlm):
            """Emits no text, so the companion floor is what would be delivered."""
            model: str = "silent"

            async def generate_content_async(self, request, stream=False):
                yield LlmResponse(content=types.Content(role="model", parts=[]))

        def stage_card(callback_context):
            stage(callback_context.state, spec, ["fresh"])

        delivered, widgets = [], []
        async def widget_sink(ctx, widget_id, payload):
            widgets.append(widget_id)
        async def companion_sink(ctx, text):
            delivered.append(text)

        binding = RuntimeBindings(
            skills=[], feature_config={"pipeline": ["staged_widgets"]},
            widget_specs=[spec], widget_sink=widget_sink,
            companion_sink=companion_sink)
        events, _session = await run(LlmAgent(
            name="host", model=SilentModel(), **binding.agent_kwargs(
                callbacks={"before_agent_callback": [stage_card]})))

        self.assertEqual(widgets, ["card"])
        self.assertEqual(delivered, ["Here is the card."])
        # The point of the hook: the sentence must NOT also become model content.
        self.assertEqual([p.text for e in events
                          for p in (getattr(e.content, "parts", None) or [])
                          if getattr(p, "text", None)], [])

    async def test_companion_sink_returning_a_value_is_a_contract_violation(self):
        # None-or-raise, same as widget_sink: a sink that returns something has
        # not said whether it accepted delivery, and guessing would silently drop
        # the sentence.
        async def build(ctx, items, rid):
            return [({"type": "fact"}, "Here is the card.")]
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact",
                          role=Role.DATA_PRIMARY, build=build)

        class SilentModel(BaseLlm):
            model: str = "silent"

            async def generate_content_async(self, request, stream=False):
                yield LlmResponse(content=types.Content(role="model", parts=[]))

        def stage_card(callback_context):
            stage(callback_context.state, spec, ["fresh"])

        binding = RuntimeBindings(
            skills=[], feature_config={"pipeline": ["staged_widgets"]},
            widget_specs=[spec],
            widget_sink=lambda ctx, wid, payload: None,
            companion_sink=lambda ctx, text: "accepted")
        with self.assertRaisesRegex(Exception, "companion sink must return None"):
            await run(LlmAgent(name="host", model=SilentModel(),
                               **binding.agent_kwargs(callbacks={
                                   "before_agent_callback": [stage_card]})))

    async def test_a_non_callable_companion_sink_is_refused_at_construction(self):
        with self.assertRaisesRegex(TypeError, "companion_sink must be callable"):
            RuntimeBindings(skills=[], feature_config={"pipeline": []},
                            companion_sink="not callable")

    async def test_sink_failure_does_not_mark_emitted(self):
        from adkl.widgets import emit_staged
        async def build(ctx, items, rid):
            return [{"type": "fact"}]
        spec = WidgetSpec(key="card", widget_id="card", payload_type="fact", build=build)
        ctx = SimpleNamespace(state={})
        stage(ctx.state, spec, ["value"])
        async def fail(*args):
            raise OSError("transport failed")
        with self.assertRaises(OSError):
            await emit_staged(ctx, [spec], sink=fail)
        assert not ctx.state.get(spec.state_emitted)


    async def test_shared_binding_keeps_concurrent_session_context_separate(self):
        async def prepare(*, context, **kwargs):
            value = context.state["seed"]
            await asyncio.sleep(0)
            context.state["topic"] = value
        binding = RuntimeBindings(skills=[skill()], feature_config=config(), selector=Selector(),
                                  prepare_skill=prepare)
        agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
        results = await asyncio.gather(run(agent, {"seed": "first"}), run(agent, {"seed": "second"}))
        assert [session.state["topic"] for _, session in results] == ["first", "second"]

    async def test_automatic_preparation(self):
        await self.check_preparation(True)

    async def test_explicit_preparation(self):
        await self.check_preparation(False)

    async def test_builtin_vectors_apply_manual_only_policy(self):
        class Embedder:
            async def embed(self, text):
                return [1.0, 0.0]
        settings = config()
        settings["skill_retrieval"]["auto_activate_exclude"] = ["support"]
        model = ScriptModel(explicit=True)
        binding = RuntimeBindings(skills=[skill()], feature_config=settings,
                                  vectors={"support": [1.0, 0.0]}, embedder=Embedder())
        _, session = await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()),
                               {"topic": "public facts"})
        assert len(model.requests) == 2
        assert session.state["adkl:last_turn"]["auto_activated"] is None
        assert "Use public facts" in model.requests[-1]

    async def test_native_domain_tool_is_scoped_to_activation(self):
        def lookup() -> dict:
            """Look up a synthetic domain fact."""
            return {"available": False}
        content = skill()
        content.frontmatter.metadata["adk_additional_tools"] = ["lookup"]
        binding = RuntimeBindings(skills=[content], tools=[lookup],
                                  feature_config=config(False), selector=Selector(False))
        kwargs = binding.agent_kwargs()
        selection = kwargs["tools"][0]
        ctx = SimpleNamespace(state={"temp:adkl_selection:host": ["support"]},
                              agent_name="host", invocation_id="fixture")
        assert "lookup" not in [t.name for t in await selection.get_tools(ctx)]
        ctx.state["_adk_activated_skill_host"] = ["support"]
        assert "lookup" in [t.name for t in await selection.get_tools(ctx)]

    async def test_observers_then_transforms_keep_empty_replacements(self):
        seen = []
        def observer(**kwargs):
            seen.append(kwargs["tool_response"].copy())
        def empty(**kwargs):
            return {}
        def second(**kwargs):
            assert kwargs["tool_response"] == {}
            return {"available": False}
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        callback = binding.agent_kwargs(after_tool_observers=[observer], callbacks={
            "after_tool_callback": [empty, second]})["after_tool_callback"][0]
        result = await callback(tool=None, args={}, tool_context=None, tool_response={"raw": 1})
        assert seen == [{"raw": 1}]
        assert result == {"available": False}

    async def test_a_ranked_skill_can_also_be_pin_protected(self):
        """A selector cannot mark a skill BOTH ranked and `source="pinned"` --
        a duplicate name in `matches` trips `prepare`'s own dedup check and
        kills the turn -- so a skill that is ranked low AND still mid-flow can
        only be represented once, as its ranked entry, and the eviction test
        below reads `Match.source == "pinned"` only. Without a separate
        channel, being ranked makes a skill MORE likely to be evicted than
        never being ranked at all. `MatchResult.pinned` is that channel.
        """
        class RankedAndMaybePinned:
            def __init__(self, pin):
                self.pin = pin

            async def match(self, text, state):
                matches = [Match("billing", 1.0, "embedding"),
                          Match("support", 0.2, "embedding")]
                pinned = frozenset({"support"}) if self.pin else frozenset()
                return MatchResult(matches, auto_activated="billing", pinned=pinned)

        settings = {"pipeline": ["skill_retrieval"],
                   "skill_retrieval": {"auto_activate": True, "continue_active": True}}
        settings["skill_retrieval"]["min_skills_for_retrieval"] = 0
        key = ACTIVATED_KEY.format(agent="host")
        skills = [plain_skill("support"), plain_skill("billing")]

        # Without the channel: ranked-but-not-winning is evicted, even though
        # it was already active and mid-flow.
        binding = RuntimeBindings(skills=skills, feature_config=settings,
                                  selector=RankedAndMaybePinned(pin=False))
        _, session = await run(LlmAgent(name="host", model=ScriptModel(),
                                        **binding.agent_kwargs()), {key: ["support"]})
        self.assertNotIn("support", session.state[key])

        # With it: the identical ranked position survives, because the
        # selector separately declared it pin-protected.
        binding2 = RuntimeBindings(skills=skills, feature_config=settings,
                                   selector=RankedAndMaybePinned(pin=True))
        _, session2 = await run(LlmAgent(name="host", model=ScriptModel(),
                                         **binding2.agent_kwargs()), {key: ["support"]})
        self.assertIn("support", session2.state[key])
        self.assertIn("billing", session2.state[key])

    async def test_a_pin_only_retention_decays_after_a_bounded_number_of_turns(self):
        """A pin exists to survive a terse mid-flow turn, not to hold a skill
        for the rest of the session. Without a bound, a fired pin -- native or
        a selector's own `MatchResult.pinned` -- protects its skill forever,
        re-injecting its full instructions on every remaining model call
        whether or not the flow is still in progress.
        """
        class AlwaysPinned:
            async def match(self, text, state):
                return MatchResult(
                    [Match("billing", 1.0, "embedding"), Match("support", 0.2, "embedding")],
                    auto_activated="billing", pinned=frozenset({"support"}))

        settings = {"pipeline": ["skill_retrieval"],
                   "skill_retrieval": {"auto_activate": True, "continue_active": True}}
        settings["skill_retrieval"]["min_skills_for_retrieval"] = 0
        key = ACTIVATED_KEY.format(agent="host")
        skills = [plain_skill("support"), plain_skill("billing")]
        binding = RuntimeBindings(skills=skills, feature_config=settings, selector=AlwaysPinned())
        agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())

        session = await run_turns(agent, PIN_GRACE_LIMIT, {key: ["support"]})
        self.assertIn("support", session.state[key],
                      f"a pin should protect for {PIN_GRACE_LIMIT} turns")

        binding3 = RuntimeBindings(skills=skills, feature_config=settings, selector=AlwaysPinned())
        agent3 = LlmAgent(name="host", model=ScriptModel(), **binding3.agent_kwargs())
        session3 = await run_turns(agent3, PIN_GRACE_LIMIT + 1, {key: ["support"]})
        self.assertNotIn("support", session3.state[key],
                         "an unbounded pin never lets its skill go")

    async def test_today_is_only_seeded_when_the_host_has_not_already(self):
        """A host router seeding a weekday-bearing `today` for its own prompt
        must not have it silently overwritten with a bare ISO date on every
        turn -- `prepare` runs on every model call, so an unconditional write
        clobbered it before the FIRST model call even happened.
        """
        binding = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        _, session = await run(LlmAgent(name="host", model=ScriptModel(),
                                        **binding.agent_kwargs()),
                               {"today": "Monday, May 24, 2026"})
        self.assertEqual(session.state["today"], "Monday, May 24, 2026")

        binding2 = RuntimeBindings(skills=[], feature_config={"pipeline": []})
        _, session2 = await run(LlmAgent(name="host", model=ScriptModel(),
                                         **binding2.agent_kwargs()))
        self.assertRegex(session2.state["today"], r"^\d{4}-\d{2}-\d{2}$")

    async def test_a_continuing_skill_is_prepared_on_every_model_call(self):
        """A skill can become `active` without going through the
        auto-activation loader call or `before_tool` at all -- continuing from
        a prior turn is exactly that shape: `prepare`'s auto-activation branch
        only fires for a FRESHLY auto-activated skill, so a skill merely
        surviving via ranking/continuation was never handed to
        `prepare_skill`. A state-injecting skill's placeholder would then
        substitute against state its own preparation hook never got a chance
        to write.
        """
        calls = []

        async def prepare(*, skill_name, context, reason):
            calls.append((skill_name, reason))
            context.state["topic"] = "grounded facts"

        class Continuing:
            async def match(self, text, state):
                # Confident every turn (so `continue_active` engages), but
                # never a FRESH auto-activation -- as if "support" auto
                # activated on an earlier turn and is now merely continuing.
                return MatchResult([Match("support", 1.0, "embedding")])

        settings = config(auto=True)
        settings["skill_retrieval"]["continue_active"] = True
        binding = RuntimeBindings(skills=[skill()], feature_config=settings,
                                  selector=Continuing(), prepare_skill=prepare)
        model = ScriptModel()
        agent = LlmAgent(name="host", model=model, **binding.agent_kwargs())
        key = ACTIVATED_KEY.format(agent="host")
        await run(agent, {key: ["support"]})
        self.assertEqual(calls, [("support", "auto")])
        self.assertIn("Use grounded facts to answer", model.requests[-1])
