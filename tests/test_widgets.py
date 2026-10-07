#!/usr/bin/env python3
"""Tests for the staged-widget pipeline: gate order, fail direction, contracts.

Synthetic hooks, so these run on any interpreter with no sibling repo. Native
ADK execution is covered by `tests/runtime/test_minimal_examples.py`.

What is worth protecting here is mostly *order* and *fail direction*. Both are
invisible in normal operation and both change behaviour when edited:

* gate order encodes real decisions -- checking `suppress` before `no_items`
  would report "suppressed" for an empty register, sending a reader to the wrong
  place entirely
* `revive_when` fails CLOSED and `expected_empty` fails OPEN, deliberately and in
  opposite directions, which is exactly the kind of asymmetry a later refactor
  "tidies" into consistency

The other theme is that every decline is *named*. A pipeline that renders nothing
and says nothing is the failure this module was written to remove, so a gate that
declines silently is a bug even when the decision is right.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import widgets as W  # noqa: E402


# ---- hooks referenced by declaration, so spec_from_dict has real targets ----

def always_true(state):
    return True


def always_false(state):
    return False


def raises(state):
    raise RuntimeError("predicate exploded")


def role_upgrade(state):
    return W.Role.ACTION_WITH_STEPS


def role_none(state):
    return None


def note_hook(state):
    return "server-authored line"


def note_empty(state):
    return ""


async def build_one(ctx, items, request_id):
    return [({"type": "Card", "n": len(items)}, "grounded caption")]


async def build_three(ctx, items, request_id):
    return [({"type": "Card", "i": i}, "") for i in range(3)]


async def build_nothing(ctx, items, request_id):
    return []


async def build_raises(ctx, items, request_id):
    raise RuntimeError("builder exploded")


HERE = "tests.test_widgets"


class Ctx:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.rendered = []

    def render_ui_widget(self, widget):
        self.rendered.append(widget)


def spec(**over) -> W.WidgetSpec:
    d = {"widget_id": "w-1", "type": "Card", "build": f"{HERE}:build_one"}
    d.update(over)
    return W.spec_from_dict(over.pop("key", "demo"), d)


def emit(spec_, state) -> tuple:
    ctx = Ctx(state)
    res = asyncio.run(W.emit_staged(ctx, [spec_]))
    return ctx, res


def staged(spec_, items=None) -> dict:
    return {spec_.state_dirty: True, spec_.state_items: items or [{"a": 1}]}


class TestDeclaration(unittest.TestCase):
    def test_missing_required_fields_are_named(self):
        for missing in ("widget_id", "type", "build"):
            d = {"widget_id": "w", "type": "C", "build": f"{HERE}:build_one"}
            del d[missing]
            with self.assertRaises(W.WidgetError) as cm:
                W.spec_from_dict("x", d)
            self.assertIn(missing, str(cm.exception))

    def test_an_unknown_role_lists_the_valid_ones(self):
        # A typo'd role would otherwise mean no contract, which is a silent
        # degradation rather than an error.
        with self.assertRaises(W.WidgetError) as cm:
            spec(role="data-primary")   # hyphen, not underscore
        self.assertIn("action_primary", str(cm.exception))

    def test_keys_are_derived_by_default(self):
        s = spec(key="offers")
        self.assertEqual(s.state_items, "staged_offers_items")
        self.assertEqual(s.state_dirty, "temp:staged_offers_dirty")
        self.assertEqual(s.state_emitted, "temp:staged_offers_emitted")

    def test_keys_can_be_overridden_to_adopt_an_existing_tool(self):
        # Derivation stops typos but cannot guess what a tool already writes.
        # A host can use a custom dirty-state key; accept that explicit mapping.
        s = spec(register="staged_nav_cards",
                 stage={"dirty_key": "temp:nav_cards_stage_dirty"},
                 emit={"emitted_key": "temp:nav_cards_emitted"})
        self.assertEqual(s.state_items, "staged_nav_cards")
        self.assertEqual(s.state_dirty, "temp:nav_cards_stage_dirty")
        self.assertEqual(s.state_emitted, "temp:nav_cards_emitted")

    def test_a_malformed_predicate_fails_at_load_not_at_the_turn_boundary(self):
        with self.assertRaises(W.WidgetError) as cm:
            spec(emit={"suppress": {"nonsense": "x"}})
        self.assertIn("state_truthy", str(cm.exception))

    def test_declarative_predicates_need_no_python(self):
        s = spec(emit={"suppress": {"state_truthy": "in:checkout"}})
        self.assertTrue(s.suppress({"in:checkout": True}))
        self.assertFalse(s.suppress({}))


class TestGateOrder(unittest.TestCase):
    """Order matters: the gate NAME is what sends a reader somewhere."""

    def test_1_not_live_when_nothing_staged(self):
        ctx, res = emit(spec(), {})
        self.assertFalse(ctx.rendered)
        self.assertEqual([g.gate for g in res.declined()], ["not_live"])

    def test_2_already_emitted_beats_the_later_gates(self):
        s = spec()
        st = staged(s)
        st[s.state_emitted] = True
        ctx, res = emit(s, st)
        self.assertFalse(ctx.rendered)
        self.assertEqual(res.declined()[0].gate, "already_emitted")

    def test_3_suppressed_is_checked_before_no_items(self):
        # The ordering assertion: an empty AND suppressed register must report
        # "suppressed". Reporting "no_items" would point at the staging tool
        # when the truth is the surface declined to show it.
        s = spec(emit={"suppress": {"hook": f"{HERE}:always_true"}})
        st = {s.state_dirty: True, s.state_items: []}
        ctx, res = emit(s, st)
        self.assertEqual(res.declined()[0].gate, "suppressed")

    def test_4_no_items_when_dirty_but_empty(self):
        s = spec()
        ctx, res = emit(s, {s.state_dirty: True, s.state_items: []})
        self.assertEqual(res.declined()[0].gate, "no_items")

    def test_5_build_returning_nothing_is_named_distinctly(self):
        # built_nothing vs expected_empty is the difference between "alert" and
        # "normal", so they must not collapse into one message.
        s = spec(build=f"{HERE}:build_nothing")
        ctx, res = emit(s, staged(s))
        self.assertEqual(res.declined()[0].gate, "built_nothing")

        s2 = spec(build=f"{HERE}:build_nothing",
                  emit={"expected_empty": {"hook": f"{HERE}:always_true"}})
        ctx2, res2 = emit(s2, staged(s2))
        self.assertEqual(res2.declined()[0].gate, "expected_empty")

    def test_6_renders_and_records(self):
        s = spec()
        ctx, res = emit(s, staged(s))
        self.assertEqual(len(ctx.rendered), 1)
        self.assertEqual(res.emitted[0]["type"], "Card")
        self.assertTrue(all(g.passed for g in res.gates))

    def test_a_broken_builder_costs_its_widget_not_the_turn(self):
        s = spec(build=f"{HERE}:build_raises")
        ctx, res = emit(s, staged(s))          # must not raise
        self.assertFalse(ctx.rendered)
        self.assertEqual(res.declined()[0].gate, "build_failed")
        self.assertIn("RuntimeError", res.declined()[0].detail)

    def test_every_decline_is_named(self):
        # The whole point of the module. A silent decline is a bug even when the
        # decision is correct, because "nothing rendered" with no reason is what
        # cost a debugging round before this existed.
        for s, st in [(spec(), {}),
                      (spec(), {spec().state_dirty: True, spec().state_items: []}),
                      (spec(build=f"{HERE}:build_nothing"), staged(spec()))]:
            _, res = emit(s, st)
            for g in res.declined():
                self.assertTrue(g.gate, "a declined gate must name itself")


class TestEmitMechanics(unittest.TestCase):
    def test_the_emitted_flag_is_written_because_adk_needs_a_state_delta(self):
        # Not bookkeeping: ADK emits the after-agent event only when the callback
        # returns content or mutates state, and render_ui_widget mutates event
        # actions rather than state. Without this write the widget is lost.
        s = spec()
        st = staged(s)
        emit(s, st)
        self.assertTrue(st[s.state_emitted])

    def test_several_payloads_get_indexed_ids(self):
        s = spec(build=f"{HERE}:build_three")
        ctx, res = emit(s, staged(s))
        self.assertEqual([e["widgetId"] for e in res.emitted],
                         ["w-1-0", "w-1-1", "w-1-2"])

    def test_a_single_payload_keeps_the_bare_id(self):
        s = spec()
        _, res = emit(s, staged(s))
        self.assertEqual(res.emitted[0]["widgetId"], "w-1")

    def test_a_grounded_caption_beats_the_default_floor(self):
        s = spec(companion={"default": "floor text"})
        _, res = emit(s, staged(s))
        self.assertEqual(res.companion, "grounded caption")

    def test_the_floor_applies_when_no_caption_was_grounded(self):
        # So a staged widget can never reach the user with no visible text.
        s = spec(build=f"{HERE}:build_three",
                 companion={"default": "floor text"})
        _, res = emit(s, staged(s))
        self.assertEqual(res.companion, "floor text")

    def test_a_note_is_written_only_after_something_emitted(self):
        # "No widget, no sentence" has to be structural: a server-authored line
        # about the card is a lie on a turn where the card is missing.
        s = spec(companion={"note": f"{HERE}:note_hook"})
        st = staged(s)
        emit(s, st)
        self.assertEqual(st.get("staged_companion_note"), "server-authored line")

        s2 = spec(build=f"{HERE}:build_nothing",
                  companion={"note": f"{HERE}:note_hook"})
        st2 = staged(s2)
        emit(s2, st2)
        self.assertIsNone(st2.get("staged_companion_note"))

    def test_a_raising_note_does_not_break_the_turn(self):
        s = spec(companion={"note": f"{HERE}:raises"})
        ctx, _ = emit(s, staged(s))
        self.assertEqual(len(ctx.rendered), 1)   # widget still went out

    def test_stage_writes_items_and_flag_together(self):
        # Separately is how one gets forgotten: items with no flag never emit,
        # a flag with no items declines at no_items.
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])
        self.assertEqual(st[s.state_items], [{"x": 1}])
        self.assertTrue(st[s.state_dirty])
        self.assertTrue(st["staged_emit_pending"])

    def test_clear_turn_flags_reports_a_register_never_drained(self):
        # The signature of a missing emit callback, which is the silent failure
        # this pipeline exists to surface.
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])
        dropped = W.clear_turn_flags(st, [s])
        self.assertEqual(dropped, [s.key])
        self.assertFalse(st[s.state_dirty])


def next_turn(state: dict) -> dict:
    """State as the NEXT turn sees it: ADK never persists a `temp:` key.

    Authoritative: google.adk.sessions.State.TEMP_PREFIX, and
    InMemorySessionService.merge_state raises on a temp: key with "temp state
    is never persisted". Every test below that claims to be about a turn
    BOUNDARY has to go through here -- passing the same dict twice is what made
    a cross-turn report look tested when it could not work at all.
    """
    return {k: v for k, v in state.items() if not k.startswith("temp:")}


class TestStaging(unittest.TestCase):
    def test_a_drop_is_reported_across_a_real_turn_boundary(self):
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])          # turn N stages
        # turn N's emit never runs: end_invocation, a raising run impl, a
        # disconnect.
        self.assertEqual(W.clear_turn_flags(next_turn(st), [s]), [s.key])

    def test_a_flushed_register_is_not_reported_next_turn(self):
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])
        emit(s, st)                          # the flush RAN
        self.assertTrue(st[s.state_emitted])
        self.assertFalse(st[W.PENDING_KEY])
        self.assertEqual(W.clear_turn_flags(next_turn(st), [s]), [])

    def test_the_turn_start_reset_does_not_clear_the_pending_marker(self):
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])
        after = next_turn(st)
        W.clear_turn_flags(after, [s])
        # A host watchdog runs AFTER this hook (composed before-agent index 0)
        # and gates on this marker. Clearing it here is what disarmed it.
        self.assertTrue(after[W.PENDING_KEY])
        self.assertEqual(W.PENDING_KEY, "staged_emit_pending")

    def test_a_flush_that_raised_still_leaves_the_drop_reportable(self):
        s = spec()
        st = {}
        W.stage(st, s, [{"x": 1}])

        async def fail(ctx, widget_id, payload):
            raise OSError("transport failed")

        with self.assertRaises(OSError):
            asyncio.run(W.emit_staged(Ctx(st), [s], sink=fail))
        self.assertTrue(st[W.PENDING_KEY])
        self.assertEqual(W.clear_turn_flags(next_turn(st), [s]), [s.key])


class TestRevive(unittest.TestCase):
    def test_revive_lets_a_non_dirty_register_emit(self):
        # A flow whose only affordance is the widget otherwise loses its card the
        # moment the model stops calling the stager, while the reply still refers
        # to it.
        s = spec(emit={"revive_when": {"hook": f"{HERE}:always_true"},
                       "revive_cap": 2})
        st = {s.state_items: [{"a": 1}]}       # NOT dirty
        ctx, res = emit(s, st)
        self.assertEqual(len(ctx.rendered), 1)
        self.assertTrue(res.gates[0].revived)

    def test_revive_fails_closed_when_the_predicate_raises(self):
        # Deliberately opposite to expected_empty. A wrong True renders UI no
        # other gate expected, so silence beats optimism.
        s = spec(emit={"revive_when": {"hook": f"{HERE}:raises"},
                       "revive_cap": 2})
        ctx, res = emit(s, {s.state_items: [{"a": 1}]})
        self.assertFalse(ctx.rendered)
        self.assertEqual(res.declined()[0].gate, "not_live")

    def test_the_cap_stops_an_unbounded_sticky_register(self):
        # Each revived emit costs a real build, so uncapped means re-rendering
        # on every later turn.
        s = spec(emit={"revive_when": {"hook": f"{HERE}:always_true"},
                       "revive_cap": 1})
        st = {s.state_items: [{"a": 1}]}
        ctx1, _ = emit(s, st)
        self.assertEqual(len(ctx1.rendered), 1)
        self.assertEqual(st[s.state_revive_count], 1)

        st[s.state_emitted] = False   # a fresh turn clears this
        ctx2, res2 = emit(s, st)
        self.assertFalse(ctx2.rendered, "the cap should have stopped this")
        self.assertEqual(res2.declined()[0].gate, "not_live")

    def test_a_genuinely_dirty_emit_resets_the_count(self):
        s = spec(emit={"revive_when": {"hook": f"{HERE}:always_true"},
                       "revive_cap": 3})
        st = {s.state_items: [{"a": 1}], s.state_revive_count: 2}
        st[s.state_dirty] = True                 # staged for real this turn
        emit(s, st)
        self.assertEqual(st[s.state_revive_count], 0)


class TestContracts(unittest.TestCase):
    def test_a_role_resolves_to_its_contract(self):
        for role, expected in [("data_primary", W.Contract.SYNTHESIS),
                               ("action_primary", W.Contract.GUIDANCE),
                               ("action_with_steps", W.Contract.STEP_HANDOFF),
                               ("input_request", W.Contract.ELICITATION),
                               ("supporting", W.Contract.ANSWER)]:
            s = spec(role=role)
            res = W.resolve_contract(staged(s), [s])
            self.assertEqual(res.contract, expected, role)

    def test_data_primary_outranks_an_action_role(self):
        # Under-shaping is the worse error: a reply that describes a destination
        # when it should synthesize rows leaves the rows unexplained.
        a = W.spec_from_dict("a", {"widget_id": "a", "type": "C",
                                   "build": f"{HERE}:build_one",
                                   "role": "action_primary"})
        b = W.spec_from_dict("b", {"widget_id": "b", "type": "C",
                                   "build": f"{HERE}:build_one",
                                   "role": "data_primary"})
        state = {**staged(a), **staged(b)}
        self.assertEqual(W.resolve_contract(state, [a, b]).contract,
                         W.Contract.SYNTHESIS)

    def test_supporting_never_suppresses_another_specs_synthesis(self):
        sup = W.spec_from_dict("sup", {"widget_id": "s", "type": "C",
                                        "build": f"{HERE}:build_one",
                                        "role": "supporting"})
        data = W.spec_from_dict("dat", {"widget_id": "d", "type": "C",
                                        "build": f"{HERE}:build_one",
                                        "role": "data_primary"})
        state = {**staged(sup), **staged(data)}
        self.assertEqual(W.resolve_contract(state, [sup, data]).contract,
                         W.Contract.SYNTHESIS)

    def test_no_role_means_no_contract(self):
        s = spec()   # no role
        res = W.resolve_contract(staged(s), [s])
        self.assertIsNone(res.contract)
        self.assertEqual(res.sources, [])

    def test_resolve_role_refines_the_static_floor(self):
        s = spec(role="action_primary", resolve_role=f"{HERE}:role_upgrade")
        self.assertEqual(W.resolve_contract(staged(s), [s]).contract,
                         W.Contract.STEP_HANDOFF)

    def test_returning_none_keeps_the_static_role(self):
        # Refinement is strictly additive; it must never remove a contract.
        s = spec(role="action_primary", resolve_role=f"{HERE}:role_none")
        self.assertEqual(W.resolve_contract(staged(s), [s]).contract,
                         W.Contract.GUIDANCE)

    def test_a_raising_refinement_keeps_the_static_role(self):
        s = spec(role="action_primary", resolve_role=f"{HERE}:raises")
        self.assertEqual(W.resolve_contract(staged(s), [s]).contract,
                         W.Contract.GUIDANCE)

    def test_expected_empty_fails_OPEN_in_contract_resolution(self):
        # Opposite direction to revive_when, deliberately: a false "nothing will
        # render" costs a correctly-shaped reply, which is worse than a redundant
        # instruction block.
        s = spec(role="action_primary",
                 emit={"expected_empty": {"hook": f"{HERE}:raises"}})
        self.assertEqual(W.resolve_contract(staged(s), [s]).contract,
                         W.Contract.GUIDANCE)

    def test_the_instruction_block_is_tagged_for_tail_injection(self):
        block = W.instruction_for(W.Contract.SYNTHESIS)
        self.assertIn("<presentation_contract", block)
        self.assertIn("synthesis", block)

    def test_no_contract_means_no_block(self):
        self.assertEqual(W.instruction_for(None), "")

    def test_every_contract_has_instruction_text(self):
        # A contract resolving to an empty block would shape nothing while
        # reporting that it did.
        for c in W.Contract:
            self.assertTrue(W.instruction_for(c).strip(), c)


class TestResolverEmitterAgreement(unittest.TestCase):
    """The #1898 invariant: both sides must agree what is live.

    Disagreement means a widget renders on a turn whose reply was never shaped
    for it, or a reply is shaped for a widget that never arrives. Enforced by
    construction -- both call `spec_is_live` -- and pinned here because that is
    a property a refactor could quietly break.
    """

    def _agree(self, s, state) -> None:
        contract_live = s.key in W.resolve_contract(state, [s]).sources
        ctx = Ctx(dict(state))
        asyncio.run(W.emit_staged(ctx, [s]))
        self.assertEqual(contract_live, bool(ctx.rendered),
                         f"resolver={contract_live} emitter={bool(ctx.rendered)}")

    def test_agreement_across_every_gate(self):
        s = spec(role="action_primary")
        self._agree(s, {})                                    # not live
        self._agree(s, staged(s))                             # live
        self._agree(s, {s.state_dirty: True, s.state_items: []})   # no items
        st = staged(s)
        st[s.state_emitted] = True
        self._agree(s, st)                                    # already emitted

    def test_agreement_on_a_suppressed_spec(self):
        s = spec(role="action_primary",
                 emit={"suppress": {"hook": f"{HERE}:always_true"}})
        self._agree(s, staged(s))

    def test_agreement_on_a_revived_spec(self):
        s = spec(role="action_primary",
                 emit={"revive_when": {"hook": f"{HERE}:always_true"},
                       "revive_cap": 2})
        self._agree(s, {s.state_items: [{"a": 1}]})

    def test_agreement_when_the_revive_predicate_raises(self):
        # Both must fail closed together. One side optimistic is the bug.
        s = spec(role="action_primary",
                 emit={"revive_when": {"hook": f"{HERE}:raises"},
                       "revive_cap": 2})
        self._agree(s, {s.state_items: [{"a": 1}]})


class TestWidgetPracticeRules(unittest.TestCase):
    """Practice rules, keyed on what the 18 real prospect specs look like.

    Structure rules check the pipeline is WIRED; these check it is wired WELL.
    They run on ADOPTED specs too -- an earlier version returned early on
    adoption, which left the tool blind on the only corpus it had.
    """

    def _lint(self, **over):
        from adkl.widget_lint import lint
        spec = {"widget_id": "w", "type": "T", "build": f"{HERE}:build_one"}
        spec.update(over)
        return {(f.spec, f.message) for f in lint({
            "features": {"pipeline": ["staged_widgets"]},
            "widgets": {"specs": {"demo": spec}}})}

    def _messages(self, **over):
        return " ".join(m for _s, m in self._lint(**over))

    def test_data_primary_without_expected_empty_is_a_warning(self):
        # The sharpest of these: SYNTHESIS tells the model to synthesize over
        # rows, so an empty build leaves a reply referring to absent data.
        msgs = self._messages(role="data_primary",
                              companion={"default": "here"})
        self.assertIn("synthesize OVER ROWS", msgs)

    def test_a_non_data_role_gets_the_milder_note(self):
        msgs = self._messages(role="action_primary",
                              companion={"default": "here"})
        self.assertIn("no expected_empty", msgs)
        self.assertNotIn("synthesize OVER ROWS", msgs)

    def test_no_companion_floor_is_flagged(self):
        # A card with no words is not self-explanatory.
        msgs = self._messages(role="action_primary",
                              emit={"expected_empty": "never"})
        self.assertIn("NO visible text", msgs)

    def test_a_companion_note_counts_as_a_floor(self):
        msgs = self._messages(role="action_primary",
                              emit={"expected_empty": "never"},
                              companion={"note": f"{HERE}:note_hook"})
        self.assertNotIn("NO visible text", msgs)

    def test_an_effectively_uncapped_revive_is_flagged(self):
        msgs = self._messages(role="action_primary",
                              companion={"default": "x"},
                              emit={"expected_empty": "never",
                                    "revive_when": {"state_truthy": "k"},
                                    "revive_cap": 1000000})
        self.assertIn("not a cap", msgs)

    def test_a_real_cap_is_not_flagged(self):
        msgs = self._messages(role="action_primary",
                              companion={"default": "x"},
                              emit={"expected_empty": "never",
                                    "revive_when": {"state_truthy": "k"},
                                    "revive_cap": 3})
        self.assertNotIn("not a cap", msgs)

    def test_a_clean_spec_produces_no_practice_findings(self):
        # The rules must be satisfiable, or they are noise an author routes
        # around rather than fixes.
        msgs = self._messages(role="data_primary",
                              companion={"default": "here"},
                              emit={"expected_empty": "never"})
        self.assertNotIn("expected_empty", msgs)
        self.assertNotIn("NO visible text", msgs)
        self.assertNotIn("not a cap", msgs)


class TestAdoptedCapSentinel(unittest.TestCase):
    def test_adoption_preserves_type_and_normalizes_foreign_roles(self):
        from enum import StrEnum
        from types import SimpleNamespace
        from unittest.mock import patch
        class HostRole(StrEnum):
            DATA = "data_primary"
        for role in (HostRole.DATA, "data_primary"):
            source = SimpleNamespace(key="card", build=build_one, state_items="items",
                                     payload_type="host_fact", presentation_role=role)
            with patch.object(W, "_resolve_attr", return_value=[source]):
                adopted = W.adopt("synthetic:REGISTRY")[0]
            self.assertEqual(adopted.payload_type, "host_fact")
            self.assertEqual(adopted.role, W.Role.DATA_PRIMARY)
            self.assertEqual(adopted.adoption_warnings, [])

    def test_adoption_reports_unmapped_role(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source = SimpleNamespace(key="card", build=build_one, presentation_role="host_only")
        with patch.object(W, "_resolve_attr", return_value=[source]), \
                self.assertWarnsRegex(UserWarning, "unmapped presentation role"):
            adopted = W.adopt("synthetic:REGISTRY")[0]
        self.assertIsNone(adopted.role)
        self.assertIn("host_only", adopted.adoption_warnings[0])

    def test_adoption_rejects_unidentifiable_specs(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch.object(W, "_resolve_attr", return_value=[SimpleNamespace()]), \
                self.assertRaisesRegex(W.WidgetError, "without a valid key"):
            W.adopt("synthetic:REGISTRY")

    def test_adopted_counter_key_enforces_upstream_ceiling(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        source = SimpleNamespace(key="card", build=build_one,
            state_items="items", revive_count_key="customer_revives",
            revive_when=lambda state: state.get("customer_revives", 0) < 2)
        with patch.object(W, "_resolve_attr", return_value=[source]):
            adopted = W.adopt("synthetic:REGISTRY")[0]
        ctx, result = emit(adopted, {"items": [{"a": 1}], "customer_revives": 1})
        self.assertEqual(len(result.emitted), 1)
        self.assertEqual(ctx.state["customer_revives"], 2)
        ctx.state.pop(adopted.state_emitted, None)
        self.assertEqual(W.spec_is_live(ctx.state, adopted), (False, False))

    def test_an_adopted_spec_delegates_its_ceiling(self):
        """-1 means upstream's own revive_when enforces the limit.

        The first version used 1_000_000, which the linter correctly read as an
        uncapped sticky register and warned about -- a false positive on a spec
        that IS capped, just not here. A sentinel another layer misreads as data
        is worse than an explicit one.
        """
        s = spec(emit={"revive_when": {"hook": f"{HERE}:always_true"}})
        object.__setattr__(s, "revive_cap", -1)
        state = {s.state_items: [{"a": 1}], s.state_revive_count: 999}
        consider, revived = W.spec_is_live(state, s)
        self.assertTrue(consider, "a delegated cap must not block revival")
        self.assertTrue(revived)


class TestStructuralIsolation(unittest.TestCase):
    def test_structural_checks_never_import_hooks_or_registries(self):
        from unittest.mock import patch
        from adkl.widget_lint import lint
        doc = {"features": {"pipeline": ["staged_widgets"]}, "widgets": {
            "adopt": "customer_private:REGISTRY",
            "specs": {"card": {"widget_id": "card", "type": "Card",
                "build": "customer_private:build", "role": "data_primary",
                "emit": {"revive_when": {"hook": "customer_private:revive"}},
                "companion": {"note": "customer_private:note"}}}}}
        with patch.object(W.importlib, "import_module", side_effect=AssertionError("imported")):
            findings = lint(doc, structural=True)
        self.assertTrue(any("deferred" in f.message for f in findings))
        self.assertTrue(any("data_primary" in f.message for f in findings))
        self.assertFalse(any(f.level == "ERROR" for f in findings))
        doc["widgets"]["specs"]["card"]["build"] = "malformed"
        self.assertTrue(any(f.level == "ERROR" for f in lint(doc, structural=True)))

    def test_cli_respects_remote_execution_boundary(self):
        import contextlib
        import io
        import tempfile
        from unittest.mock import patch
        from adkl import toolbind, widget_lint
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            agent = root / "agent.yaml"
            agent.write_text("tools: {repo: /customer/private}\nwidgets: {adopt: private:REGISTRY}\n"
                             "features: {pipeline: [staged_widgets]}\n")
            (root / "execution.yaml").write_text(
                "version: 1\ndefault: ExampleCo\nprofiles:\n"
                "  ExampleCo: {target: remote, environment: ExampleCo}\n")
            with patch.object(toolbind, "prepare", side_effect=AssertionError("prepared")), \
                    patch.object(W, "adopt", side_effect=AssertionError("imported")), \
                    contextlib.redirect_stdout(io.StringIO()):
                with patch.object(sys, "argv", ["widget_lint", str(agent), "--structural"]):
                    self.assertEqual(widget_lint.main(), 0)
                with patch.object(sys, "argv", ["widget_lint", str(agent)]):
                    self.assertEqual(widget_lint.main(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=0)
