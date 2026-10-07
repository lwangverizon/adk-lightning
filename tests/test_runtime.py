#!/usr/bin/env python3
"""Tests for feature runtime implementations.

The bug these exist because of: `adkl.features` is a registry of declarations
with no behaviour, and `chat.py` read `resolved.active` for exactly two features
while ignoring the rest. A pipeline listing six ran four as no-ops -- and every
layer confirmed they were on. The config listed them, the registry validated
their order, `/api/agent` reported them active, and the console drew them
enabled. Unanimous confirmation of something false.

The support inventory is checked independently against runtime wiring, and
behavioral tests below exercise the callbacks. A declaration alone cannot
establish executed behavior.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import features as F  # noqa: E402
from adkl import runtime as R  # noqa: E402


class _Ctx:
    def __init__(self, state=None):
        self.state = state if state is not None else {}


class TestClassificationIsComplete(unittest.TestCase):
    def test_every_registered_feature_is_classified(self):
        """Advertised support must have an explicit runtime wiring entry."""
        advertised = {name for name, spec in F.REGISTRY.items()
                      if spec.support_status != "unsupported"}
        self.assertEqual(advertised, R.IMPLEMENTED)
        classified = R.IMPLEMENTED | set(R.NOT_IMPLEMENTED)
        missing = sorted(set(F.REGISTRY) - classified)
        self.assertEqual(
            missing, [],
            f"unclassified feature(s): {missing}. Keep support metadata and "
            "runtime wiring aligned; new declarations default to unsupported.")

    def test_unimplemented_features_carry_a_reason(self):
        for name, reason in R.NOT_IMPLEMENTED.items():
            self.assertTrue(reason.strip(),
                            f"{name} is unimplemented with no reason given")

    def test_build_callbacks_reports_what_it_cannot_honour(self):
        # The behaviour that makes the no-op visible in the turn trace. Uses
        # semantic_cache because it is the one still unimplemented -- this test
        # previously named history_slicing and correctly failed once that was
        # built, which is the classification test doing its job.
        _, unhonoured = R.build_callbacks(["skill_retrieval", "semantic_cache"], {})
        self.assertTrue(any("semantic_cache" in u for u in unhonoured))

    def test_identity_gate_is_reported_until_a_trusted_auth_boundary_exists(self):
        kwargs, _ = R.build_callbacks(
            ["identity_gate"], {"identity_gate": {"arg_names": ["mtn"]}})
        self.assertNotIn("before_tool_callback", kwargs)
        _, unhonoured = R.build_callbacks(["identity_gate"], {})
        self.assertTrue(any("identity_gate" in item for item in unhonoured))

    def test_an_unknown_feature_is_reported_not_ignored(self):
        _, unhonoured = R.build_callbacks(["not_a_real_feature"], {})
        self.assertTrue(any("not_a_real_feature" in u for u in unhonoured))

    def test_implemented_features_report_nothing(self):
        _, unhonoured = R.build_callbacks(
            ["skill_retrieval", "tool_result_trimming",
             "staged_widgets", "history_slicing",
             "output_cleanup", "pii_redaction"],
            {})
        self.assertEqual(unhonoured, [])

    def test_before_and_after_model_hooks_are_registered(self):
        kwargs, _ = R.build_callbacks(
            ["history_slicing", "output_cleanup"], {})
        self.assertIn("before_model_callback", kwargs)
        self.assertIn("after_model_callback", kwargs)

    def test_the_two_output_features_share_ONE_callback(self):
        """Both edit the same text, so two filters would fight.

        Independent callbacks would each read the original part and the second
        would overwrite the first's edit -- a redaction silently undone by a strip
        that ran after it.
        """
        kwargs, _ = R.build_callbacks(
            ["output_cleanup", "pii_redaction"], {})
        self.assertEqual(len(kwargs["after_model_callback"]), 1)

    def test_trimming_is_registered_as_an_after_tool_callback(self):
        kwargs, _ = R.build_callbacks(["tool_result_trimming"], {})
        self.assertIn("after_tool_callback", kwargs)

    def test_an_empty_kinds_list_redacts_NOTHING_not_everything(self):
        """`kinds: []` is a kill switch, and it used to invert.

        `pii_cfg.get("kinds") or None` collapsed [] to None, and None means ALL
        patterns downstream -- so declaring "redact nothing" redacted every class
        the module knows. Asserted end to end through the real callback, because
        the inversion needed two independent sites to be wrong and a unit test on
        either one alone would have passed.
        """
        text = "ssn 123-45-6789 call 555-867-5309 mail a@b.com"
        kwargs, _ = R.build_callbacks(["pii_redaction"], {"pii_redaction": {"kinds": []}})
        response = _Resp([_Part(text=text)])
        kwargs["after_model_callback"][0](callback_context=_Ctx(), llm_response=response)
        self.assertEqual(response.content.parts[0].text, text)

    def test_an_absent_kinds_list_still_redacts_the_registry_default(self):
        """The fix must not turn "unspecified" into "nothing" either."""
        kwargs, _ = R.build_callbacks(["pii_redaction"], {})
        response = _Resp([_Part(text="ssn 123-45-6789")])
        kwargs["after_model_callback"][0](callback_context=_Ctx(), llm_response=response)
        self.assertNotIn("123-45-6789", response.content.parts[0].text)

    def test_a_narrowed_kinds_list_redacts_only_what_it_names(self):
        kwargs, _ = R.build_callbacks(
            ["pii_redaction"], {"pii_redaction": {"kinds": ["email"]}})
        response = _Resp([_Part(text="ssn 123-45-6789 mail a@b.com")])
        kwargs["after_model_callback"][0](callback_context=_Ctx(), llm_response=response)
        text = response.content.parts[0].text
        self.assertIn("123-45-6789", text)   # not named, so untouched
        self.assertNotIn("a@b.com", text)

    def test_the_gate_is_NOT_wired_until_a_trusted_boundary_exists(self):
        """identity_gate is deliberately unimplemented.

        A gate whose only evidence is session state is not a security control --
        anything that can write state can disarm it. Reporting it as active would
        be the same "unanimous confirmation of something false" failure as four
        features running as no-ops. So it stays declared, unwired, and REPORTED.
        """
        kwargs, unhonoured = R.build_callbacks(
            ["identity_gate"], {"identity_gate": {"arg_names": ["mtn"]}})
        self.assertNotIn("before_tool_callback", kwargs)
        self.assertTrue(any("identity_gate" in u for u in unhonoured))


class TestIdentityGate(unittest.TestCase):
    def setUp(self):
        self.gate = R.make_identity_gate(["account_number", "mtn", "custId"])

    def test_a_fabricated_identifier_is_refused_while_unauthenticated(self):
        out = self.gate(args={"mtn": "5551234"}, tool_context=_Ctx())
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["refused_args"], ["mtn"])

    def test_the_refusal_tells_the_model_what_to_do_instead(self):
        # A bare refusal invites a retry with the same fabricated arg. The
        # instruction is what redirects the model to asking for sign-in.
        out = self.gate(args={"account_number": "12345"}, tool_context=_Ctx())
        self.assertIn("sign in", out["agent_instruction"].lower())

    def test_an_empty_value_is_not_a_fabricated_identifier(self):
        # A tool declaring `mtn: str = ""` passes an empty string on EVERY call.
        # Refusing on presence rather than truthiness would refuse every call to
        # that tool -- the gate would break the agent it is protecting.
        self.assertIsNone(self.gate(args={"mtn": ""}, tool_context=_Ctx()))

    def test_an_unverified_session_value_cannot_disarm_the_gate(self):
        out = self.gate(args={"mtn": "1"}, tool_context=_Ctx({"user": "u1"}))
        self.assertEqual(out["status"], "refused")

    def test_the_gate_disarms_only_on_a_VERIFIED_identity(self):
        # A real tool legitimately takes an account number once there is a
        # verified account -- but a bare truthy session value is not evidence of
        # authentication. The earlier version disarmed on any truthy
        # state["user"], so anything able to write session state could switch the
        # gate off. Verification must come from a trusted boundary.
        verified = _Ctx({"user": {"verified": True}})
        self.assertIsNone(self.gate(args={"mtn": "5551234"},
                                    tool_context=verified))

    def test_unrelated_args_pass(self):
        self.assertIsNone(
            self.gate(args={"query": "iphone"}, tool_context=_Ctx()))

    def test_several_offending_args_are_all_named(self):
        out = self.gate(args={"mtn": "1", "custId": "2"}, tool_context=_Ctx())
        self.assertEqual(out["refused_args"], ["custId", "mtn"])

    def test_a_missing_context_REFUSES_rather_than_passing(self):
        # ADK's callback signature has shifted across versions, so the gate must
        # not raise. But it must also not pass: with no state there is no evidence
        # of verification, and failing OPEN on a security control is the wrong
        # direction. Refusing a legitimate call is recoverable; leaking account
        # data is not.
        out = self.gate(args={"mtn": "1"}, tool_context=None)
        self.assertEqual(out["status"], "refused")

    def test_a_custom_auth_key_is_honoured(self):
        gate = R.make_identity_gate(["mtn"], auth_state_key="signed_in")
        self.assertIsNone(gate(args={"mtn": "1"},
                               tool_context=_Ctx({"signed_in": {"verified": True}})))


class TestResultTrimming(unittest.TestCase):
    def test_a_small_payload_is_untouched(self):
        trim = R.make_result_trimmer(max_chars=1000)
        self.assertIsNone(trim(tool_response={"status": "ok", "results": [1, 2]}))

    def test_large_results_are_explicitly_incomplete_not_business_subsets(self):
        trim = R.make_result_trimmer(max_chars=500)
        big = {"status": "ok", "results": [{"i": i, "pad": "x" * 80} for i in range(50)]}
        out = trim(tool_response=big)
        self.assertIn("_adkl_trimmed", out)
        self.assertNotIn("results", out)
        self.assertNotIn("status", out)
        self.assertLessEqual(len(json.dumps(out)), 500)
        self.assertEqual(len(big["results"]), 50)

    def test_keep_tail_takes_the_end_of_serialized_data(self):
        trim = R.make_result_trimmer(max_chars=300, keep="tail")
        out = trim(tool_response={"content": "START" + "m" * 5000 + "END"})
        self.assertTrue(out["preview"].endswith('END"}'))

    def test_plain_strings_are_also_bounded(self):
        trim = R.make_result_trimmer(max_chars=300)
        self.assertIn("_adkl_trimmed", trim(tool_response="a" * 5000))
        self.assertIsNone(trim(tool_response=None))

    def test_unserializable_results_fail_the_enabled_guard(self):
        trim = R.make_result_trimmer(max_chars=300)
        with self.assertRaises(ValueError):
            trim(tool_response={"fn": lambda: None})


class TestContractChannel(unittest.TestCase):
    """The fallback for a turn that ends at a tool call.

    A contract is normally injected before the model writes, which works only when
    a model call FOLLOWS the staging. When the staging tool sets
    skip_summarization the turn ends there, the register goes dirty at the turn
    boundary, and there is nothing left to inject into -- so the widget renders
    beside a reply never shaped for it. Proven live on "pixel 10" before this
    existed: contract=None across both model calls.
    """

    def _spec(self):
        from adkl import widgets as W
        return W.spec_from_dict("product_cta", {
            "widget_id": "p", "type": "Product_CTA", "build": "json:dumps",
            "role": "data_primary"})

    def _staged(self, spec, **extra):
        return {spec.state_dirty: True, spec.state_items: [{"id": 1}], **extra}

    def test_a_contract_rides_on_the_tool_result(self):
        spec = self._spec()
        state = self._staged(spec)
        out = R.make_contract_channel([spec])(
            tool_context=_Ctx(state), tool_response={"status": "ok"})
        self.assertIn("presentation_contract", out["agent_instruction"])

    def test_it_marks_the_turn_so_nothing_injects_twice(self):
        spec = self._spec()
        state = self._staged(spec)
        R.make_contract_channel([spec])(
            tool_context=_Ctx(state), tool_response={"status": "ok"})
        self.assertTrue(state["temp:adkl_contract_injected"])

    def test_it_stands_down_when_a_contract_was_already_injected(self):
        # Two blocks in one turn give the model competing depth instructions.
        spec = self._spec()
        state = self._staged(spec, **{"temp:adkl_contract_injected": True})
        self.assertIsNone(R.make_contract_channel([spec])(
            tool_context=_Ctx(state), tool_response={"status": "ok"}))

    def test_a_plain_result_stays_plain(self):
        spec = self._spec()
        self.assertIsNone(R.make_contract_channel([spec])(
            tool_context=_Ctx({}), tool_response={"status": "ok"}))

    def test_an_existing_instruction_is_preserved(self):
        # The tool's own guidance is load-bearing; replacing it would drop what
        # the tool needs the model to know.
        spec = self._spec()
        out = R.make_contract_channel([spec])(
            tool_context=_Ctx(self._staged(spec)),
            tool_response={"agent_instruction": "Price depends on trade-in."})
        self.assertIn("Price depends on trade-in.", out["agent_instruction"])
        self.assertIn("presentation_contract", out["agent_instruction"])

    def test_a_non_dict_result_is_left_alone(self):
        spec = self._spec()
        self.assertIsNone(R.make_contract_channel([spec])(
            tool_context=_Ctx(self._staged(spec)), tool_response="text"))

    def test_a_missing_state_does_not_crash(self):
        spec = self._spec()
        self.assertIsNone(R.make_contract_channel([spec])(
            tool_context=None, tool_response={"status": "ok"}))


class TestProgressiveAuth(unittest.TestCase):
    """The marker a sign-in gate reads.

    Without it a CTA can be offered before the agent has tried to answer
    publicly, which is the wrong order for an unauthenticated visitor.
    """

    def setUp(self):
        self.mark = R.make_public_help_marker(
            ["search_supports", "get_offers"], "temp:public_help_attempt")

    def _tool(self, name):
        return type("T", (), {"name": name})()

    def test_a_public_help_tool_sets_the_marker(self):
        state = {}
        self.mark(tool=self._tool("search_supports"), tool_context=_Ctx(state),
                  tool_response={"results": [1, 2]})
        self.assertEqual(state["temp:public_help_attempt"]["outcome"], "usable")

    def test_an_empty_result_still_counts_as_an_attempt(self):
        # The contract is "try public help first", not "succeed at it". A tool
        # that legitimately found nothing has still earned the sign-in prompt.
        state = {}
        self.mark(tool=self._tool("search_supports"), tool_context=_Ctx(state),
                  tool_response={"results": []})
        self.assertEqual(state["temp:public_help_attempt"]["outcome"], "empty")

    def test_an_error_still_counts_as_an_attempt(self):
        state = {}
        self.mark(tool=self._tool("get_offers"), tool_context=_Ctx(state),
                  tool_response={"status": "error"})
        self.assertEqual(state["temp:public_help_attempt"]["outcome"], "error")

    def test_an_unwatched_tool_sets_nothing(self):
        state = {}
        self.mark(tool=self._tool("render_cta"), tool_context=_Ctx(state),
                  tool_response={"results": [1]})
        self.assertEqual(state, {})

    def test_it_never_replaces_the_tool_result(self):
        # Returning a value would break the after-tool chain for everything
        # registered later -- ADK stops on the first truthy return.
        out = self.mark(tool=self._tool("search_supports"),
                        tool_context=_Ctx({}), tool_response={"results": [1]})
        self.assertIsNone(out)


class TestShortCircuit(unittest.TestCase):
    PHRASES = {"bill upload": {"widget_type": "BillUpload_CTA",
                               "reply": "Upload a bill."}}

    class Ctx:
        def __init__(self, text):
            part = type("P", (), {"text": text})()
            self.user_content = type("C", (), {"parts": [part]})()
            self.state = {}
            self.rendered = []

        def render_ui_widget(self, w):
            self.rendered.append(w)

    def _run(self, text):
        import asyncio
        ctx = self.Ctx(text)
        out = asyncio.run(R.make_short_circuit(self.PHRASES)(
            callback_context=ctx))
        return ctx, out

    def test_an_exact_phrase_renders_and_ends_the_turn(self):
        ctx, out = self._run("bill upload")
        self.assertTrue(ctx.rendered)
        self.assertIsNotNone(out)

    def test_matching_is_case_and_whitespace_insensitive(self):
        ctx, out = self._run("  Bill Upload  ")
        self.assertTrue(ctx.rendered)

    def test_a_longer_question_containing_the_phrase_is_NOT_hijacked(self):
        """The reason matching is whole-message.

        A substring match would turn "how do I do a bill upload for two lines"
        into a canned reply -- a real question answered with a stock card.
        """
        ctx, out = self._run("how do I do a bill upload for two lines")
        self.assertFalse(ctx.rendered)
        self.assertIsNone(out)

    def test_an_unrelated_message_passes_through(self):
        ctx, out = self._run("what plans do you have")
        self.assertIsNone(out)


class TestStateInjection(unittest.TestCase):
    class Req:
        def __init__(self):
            self.added = []

        def append_instructions(self, blocks):
            self.added.extend(blocks)

    def test_a_slot_is_filled_from_state(self):
        req = self.Req()
        R.make_state_injector({"coverage": {"from_state": "cov"}})(
            callback_context=_Ctx({"cov": "210 countries"}), llm_request=req)
        self.assertIn("210 countries", "".join(req.added))

    def test_a_missing_source_is_reported_not_blanked(self):
        """An empty placeholder reads to the model as a FACT.

        "<coverage></coverage>" says "no coverage", not "unknown" -- a wrong
        answer dressed as a confident one. So a missing slot is left alone and
        named.
        """
        missing = []
        req = self.Req()
        R.make_state_injector({"coverage": {"from_state": "absent"}},
                             on_missing=missing.extend)(
            callback_context=_Ctx({}), llm_request=req)
        self.assertEqual(req.added, [])
        self.assertEqual(missing, ["coverage"])


class _Part:
    def __init__(self, text=None, call=None, response=None, thought=False):
        self.text, self.thought = text, thought
        self.function_call, self.function_response = call, response


class _Content:
    def __init__(self, role, parts):
        self.role, self.parts = role, parts


class _Req:
    def __init__(self, contents):
        self.contents = contents


class _Resp:
    def __init__(self, parts, partial=False):
        self.content = _Content("model", parts)
        self.partial = partial


def _turn(user_text, model_text):
    return [_Content("user", [_Part(text=user_text)]),
            _Content("model", [_Part(text=model_text)])]


class TestHistorySlicing(unittest.TestCase):
    def test_short_history_is_untouched(self):
        req = _Req(_turn("q1", "a1") + _turn("q2", "a2"))
        before = list(req.contents)
        R.make_history_slicer(5)(callback_context=_Ctx(), llm_request=req)
        self.assertEqual(req.contents, before)

    def test_long_history_is_trimmed_to_the_window(self):
        contents = []
        for i in range(8):
            contents += _turn(f"q{i}", f"a{i}")
        req = _Req(contents)
        R.make_history_slicer(3)(callback_context=_Ctx(), llm_request=req)
        users = [c for c in req.contents if c.role == "user"]
        self.assertEqual(len(users), 3)
        # The MOST RECENT turns survive, not the oldest.
        self.assertEqual(users[-1].parts[0].text, "q7")

    def test_slicing_lands_on_a_turn_boundary(self):
        # Counting messages instead of turns would cut mid-exchange, leaving the
        # model a reply whose question it cannot see.
        contents = []
        for i in range(6):
            contents += _turn(f"q{i}", f"a{i}")
        req = _Req(contents)
        R.make_history_slicer(2)(callback_context=_Ctx(), llm_request=req)
        self.assertEqual(req.contents[0].role, "user")

    def test_a_tool_pair_is_not_split(self):
        """The invalid-request case the option exists to prevent.

        A function_response separated from its call makes the request invalid --
        the model receives a result it never asked for and either hallucinates the
        question or errors.
        """
        contents = [
            _Content("user", [_Part(text="q0")]),
            _Content("model", [_Part(call={"name": "search"})]),
            _Content("user", [_Part(response={"name": "search"})]),
            _Content("model", [_Part(text="a0")]),
            _Content("user", [_Part(text="q1")]),
            _Content("model", [_Part(text="a1")]),
        ]
        req = _Req(contents)
        # A window of 2 would otherwise cut at the function_response.
        R.make_history_slicer(2, preserve_tool_pairs=True)(
            callback_context=_Ctx(), llm_request=req)
        first = req.contents[0]
        has_orphan = any(getattr(p, "function_response", None)
                         for p in first.parts)
        self.assertFalse(has_orphan, "kept an orphaned tool response")

    def test_the_turn_predicate_is_what_keeps_a_tool_pair_whole(self):
        """`test_a_tool_pair_is_not_split` above cannot go red: with keep_turns=2
        its fixture has exactly 2 real turn-starts, so nothing gets trimmed at
        all and the assertion holds even with `preserve_tool_pairs=False`, even
        with the walk-back deleted, and even with `before_model` replaced by
        `return None`. This fixture actually trims (3 turn-starts, keep_turns=2)
        and checks the mechanism that does the work: `_is_user_turn_start`
        excluding any content that carries a `function_response`, not the
        (inert) walk-back.
        """
        contents = _turn("q0", "a0") + [
            _Content("user", [_Part(text="q1")]),
            _Content("model", [_Part(call={"name": "search"})]),
            _Content("user", [_Part(response={"name": "search"})]),
            _Content("model", [_Part(text="a1")]),
        ] + _turn("q2", "a2")
        req = _Req(contents)
        R.make_history_slicer(2)(callback_context=_Ctx(), llm_request=req)
        first = req.contents[0]
        self.assertEqual(first.role, "user")
        self.assertFalse(any(getattr(p, "function_response", None)
                             for p in first.parts))
        self.assertEqual(first.parts[0].text, "q1")

    def test_tool_responses_do_not_count_as_user_turns(self):
        """ADK represents function responses with role=user.

        Counting those as turns can cut off the real current question after a
        tool-heavy exchange, leaving the model to answer a response payload.
        """
        contents = _turn("q0", "a0") + [
            _Content("model", [_Part(call={"name": "search"})]),
            _Content("user", [_Part(response={"name": "search"})]),
            _Content("model", [_Part(text="tool answer")]),
        ] + _turn("current question", "")
        req = _Req(contents)
        R.make_history_slicer(1)(callback_context=_Ctx(), llm_request=req)
        self.assertEqual(req.contents[0].parts[0].text, "current question")

    def test_the_window_floor_is_one_turn(self):
        # A window of 0 would mean "answer with no question".
        contents = _turn("q0", "a0") + _turn("q1", "a1")
        req = _Req(contents)
        R.make_history_slicer(0)(callback_context=_Ctx(), llm_request=req)
        self.assertTrue(any(c.role == "user" for c in req.contents))

    def test_the_trim_is_recorded_for_the_trace(self):
        # Silent trimming is indistinguishable from a model that forgot.
        contents = []
        for i in range(6):
            contents += _turn(f"q{i}", f"a{i}")
        ctx = _Ctx()
        R.make_history_slicer(2)(callback_context=ctx, llm_request=_Req(contents))
        self.assertIn("temp:history_sliced", ctx.state)

    def test_a_frozen_request_is_left_alone(self):
        class Frozen:
            contents = _turn("q0", "a0") * 5
            def __setattr__(self, k, v):
                raise AttributeError("frozen")
        # Must not raise: losing the trim is better than failing the turn.
        R.make_history_slicer(1)(callback_context=_Ctx(), llm_request=Frozen())


class TestOutputFilters(unittest.TestCase):
    def _run(self, text, **kw):
        from adkl import output_filters
        part = _Part(text=text)
        resp = _Resp([part])
        output_filters.make_output_filter(**kw)(llm_response=resp)
        return part.text

    def test_a_fenced_block_is_stripped(self):
        out = self._run("Here.\n```json\n{\"a\":1}\n```\nDone.")
        self.assertNotIn("```", out)
        self.assertIn("Here.", out)

    def test_a_partial_response_is_never_touched(self):
        """The streaming constraint.

        A partial's own leading/trailing whitespace IS the join between it and its
        neighbours, so rewriting one concatenates words: "the plan" -> "theplan".
        """
        from adkl import output_filters
        part = _Part(text=" 555-123-4567 ")
        resp = _Resp([part], partial=True)
        output_filters.make_output_filter()(llm_response=resp)
        self.assertEqual(part.text, " 555-123-4567 ")

    def test_identifiers_are_masked(self):
        out = self._run("Call 555-123-4567 about acct 123456789012.")
        self.assertNotIn("555-123-4567", out)
        self.assertNotIn("123456789012", out)

    def test_a_session_id_survives_redaction(self):
        # Correlation ids are infrastructure. Masking them makes a reply
        # impossible to tie back to a trace.
        out = self._run("trace POE-M-abc123456789 done")
        self.assertIn("POE-M-abc123456789", out)

    def test_a_price_is_not_mistaken_for_an_identifier(self):
        # Over-masking corrupts a CORRECT answer, which is worse than missing one
        # identifier in a dev console.
        out = self._run("The iPhone is $1,199.99 or $33.33/mo over 36 months.")
        self.assertIn("1,199.99", out)
        self.assertIn("33.33", out)

    def test_a_thought_part_is_skipped(self):
        from adkl import output_filters
        part = _Part(text="Thought: 555-123-4567", thought=True)
        resp = _Resp([part])
        output_filters.make_output_filter()(llm_response=resp)
        self.assertIn("555-123-4567", part.text)

    def test_removals_are_reported(self):
        # A model leaking JSON is a PROMPT problem the author needs to see.
        notes = []
        from adkl import output_filters
        part = _Part(text="a\n```x\ncode\n```\nb")
        output_filters.make_output_filter(on_finding=notes.extend)(
            llm_response=_Resp([part]))
        self.assertTrue(notes)

    def test_redaction_can_be_disabled_independently(self):
        out = self._run("Call 555-123-4567.", strip=True, redact_kinds=False)
        self.assertIn("555-123-4567", out)


class TestStreamRedactor(unittest.TestCase):
    """Per-chunk redaction is NOT redaction.

    Streaming makes every pattern splittable: "call 555-" then "123-4567" passes
    both chunks untouched while the joined text is caught immediately. So enabling
    token streaming without this would have silently bypassed PII masking on
    exactly the text a customer reads.
    """

    def _stream(self, chunks, **kw):
        from adkl.output_filters import StreamRedactor
        r = StreamRedactor(**kw)
        return "".join(r.feed(c) for c in chunks) + r.flush(), r

    def test_the_split_that_leaked_before(self):
        out, _ = self._stream(["call 555-", "123-4567 now"])
        self.assertNotIn("555-123-4567", out)
        self.assertIn("[phone]", out)

    def test_a_split_email_is_caught(self):
        out, _ = self._stream(["Email help", "@example.com now"])
        self.assertIn("[email]", out)

    def test_a_split_account_number_is_caught(self):
        out, _ = self._stream(["Acct 1234", "56789012 is yours"])
        self.assertIn("[account]", out)

    def test_a_split_price_SURVIVES(self):
        # Over-masking corrupts a correct answer, which is the worse failure.
        out, _ = self._stream(["Your bill is $1,19", "9.99 this month"])
        self.assertIn("$1,199.99", out)

    def test_nothing_is_lost_across_many_chunks(self):
        text = "Plans start at $35 per line with no activation fee today."
        out, _ = self._stream([text[i:i + 3] for i in range(0, len(text), 3)])
        self.assertEqual(out, text)

    def test_flush_releases_the_held_tail(self):
        from adkl.output_filters import StreamRedactor
        r = StreamRedactor()
        # Shorter than the carry window, so feed() must hold ALL of it back.
        self.assertEqual(r.feed("short text"), "")
        self.assertEqual(r.flush(), "short text")

    def test_findings_are_accumulated_for_the_trace(self):
        _, r = self._stream(["call 555-867-5309 or ", "help@example.com"])
        self.assertTrue(any("phone" in f for f in r.found))
        self.assertTrue(any("email" in f for f in r.found))

    def test_a_disabled_kind_is_not_masked(self):
        out, _ = self._stream(["call 555-867-5309 now"], kinds=["email"])
        self.assertIn("555-867-5309", out)


if __name__ == "__main__":
    unittest.main(verbosity=0)
