#!/usr/bin/env python3
"""Tests for skill retrieval.

The behaviours worth pinning are the ones that fail *silently* in the original:
dimension mismatch, an unresolvable tool name, a dropped in-progress skill, and
an embedding backend raising something that is not TimeoutError.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl.retrieval import (  # noqa: E402
    ACTIVATED_KEY,
    Match,
    MatchResult,
    RetrievalConfig,
    SkillRetrieval,
    StatePin,
    config_from_dict,
    cosine,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeEmbedder:
    """Deterministic 3-d embedder: one axis per topic, so scores are predictable."""

    AXES = {
        "device": (1.0, 0.0, 0.0),
        "offer": (0.0, 1.0, 0.0),
        "question": (0.0, 0.0, 1.0),
    }

    def __init__(self, fail: Exception | None = None, delay: float = 0.0):
        self.fail = fail
        self.delay = delay
        self.calls = 0

    async def embed(self, text: str):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        for word, vec in self.AXES.items():
            if word in text.lower():
                return list(vec)
        return [0.0, 0.0, 0.0]


VECTORS = {
    "device-exploration": [1.0, 0.0, 0.0],
    "offer-exploration": [0.0, 1.0, 0.0],
    "public-qa": [0.0, 0.0, 1.0],
}
DESCRIPTIONS = {
    "device-exploration": "browse phones tablets iphone galaxy pixel",
    "offer-exploration": "deals promotions discounts trade-in savings",
    "public-qa": "factual questions activation esim unlocking policy",
}


def make(**over) -> SkillRetrieval:
    # Tiny fixtures exercise ranking, not the default small-catalog shortcut.
    cfg_kwargs = dict(top_k=3, total_cap=3, min_score=0.15, min_skills_for_retrieval=0)
    cfg_kwargs.update({k: v for k, v in over.items() if k != "embedder"})
    return SkillRetrieval(
        RetrievalConfig(**cfg_kwargs), VECTORS,
        over.get("embedder") or FakeEmbedder(), DESCRIPTIONS)


class TestMatching(unittest.TestCase):
    def test_semantic_match_ranks_the_right_skill_first(self):
        r = run(make().match("show me a device"))
        self.assertEqual(r.matches[0].skill, "device-exploration")
        self.assertEqual(r.matches[0].source, "embedding")

    def test_below_min_score_is_excluded(self):
        # Orthogonal axes mean the other two score 0.0, under min_score.
        r = run(make().match("show me a device"))
        self.assertEqual([m.skill for m in r.matches if m.source == "embedding"],
                         ["device-exploration"])

    def test_empty_input_exposes_everything_rather_than_nothing(self):
        # An empty match set would make every skill unloadable, which is worse
        # than paying for all of them.
        r = run(make().match("   "))
        self.assertTrue(r.fallback)
        self.assertEqual(len(r.matches), 3)

    def test_margin_reports_ambiguity(self):
        r = MatchResult([Match("a", 0.9, "embedding"), Match("b", 0.88, "embedding")])
        self.assertAlmostEqual(r.margin, 0.02, places=6)
        self.assertEqual(MatchResult([Match("a", 0.9, "embedding")]).margin, 1.0)

    def test_excluded_skill_is_never_returned_even_when_its_vector_wins(self):
        r = run(make(excluded_skills=frozenset({"device-exploration"})).match("device"))
        self.assertNotIn("device-exploration", r.names)


class TestDegradation(unittest.TestCase):
    def test_backend_error_falls_back_instead_of_raising(self):
        # Selection is a ranking optimization. A failed ranking should cost
        # tokens, not the conversation.
        r = run(make(embedder=FakeEmbedder(fail=RuntimeError("502"))).match("device"))
        self.assertTrue(r.fallback)
        self.assertEqual(len(r.matches), 3)

    def test_non_timeout_exception_is_also_caught(self):
        # Backend clients raise their own types that do NOT subclass TimeoutError
        # (litellm.exceptions.*, custom service errors). A narrow except let those escape
        # into ADK request handling as a hard error.
        class ServiceAPIError(Exception):
            pass

        r = run(make(embedder=FakeEmbedder(fail=ServiceAPIError())).match("device"))
        self.assertTrue(r.fallback)

    def test_timeout_falls_back(self):
        r = run(make(embedder=FakeEmbedder(delay=0.2),
                     timeout_seconds=0.01).match("device"))
        self.assertTrue(r.fallback)

    def test_fallback_allows_only_eligible_skills_to_load(self):
        r = run(make(embedder=FakeEmbedder(fail=RuntimeError())).match("x"))
        self.assertFalse(make().allows("anything-at-all", r))
        self.assertTrue(make().allows("device-exploration", r))


class TestDimensionGuard(unittest.TestCase):
    def test_mismatched_vector_file_is_rejected_at_construction(self):
        # cosine() returns 0.0 on a length mismatch, so this would silently
        # disable retrieval rather than fail. Catch it when the object is built.
        with self.assertRaises(ValueError) as cm:
            SkillRetrieval(RetrievalConfig(dimensions=768), VECTORS, FakeEmbedder())
        self.assertIn("silently disable retrieval", str(cm.exception))

    def test_matching_dimensions_construct_fine(self):
        SkillRetrieval(RetrievalConfig(dimensions=3), VECTORS, FakeEmbedder())

    def test_cosine_returns_zero_on_length_mismatch(self):
        self.assertEqual(cosine([1.0, 0.0], [1.0, 0.0, 0.0]), 0.0)


class TestLexicalBackfill(unittest.TestCase):
    def test_exact_product_name_reaches_a_skill_embeddings_missed(self):
        # "iphone" is in the device description but the fake embedder maps this
        # query to the question axis, so only lexical can surface it.
        r = run(make(top_k=1, total_cap=3).match("question about iphone"))
        names = [m.skill for m in r.matches]
        self.assertEqual(r.matches[0].skill, "public-qa")
        self.assertIn("device-exploration", names)
        self.assertEqual(
            next(m for m in r.matches if m.skill == "device-exploration").source,
            "lexical")

    def test_lexical_matches_score_zero_so_they_cannot_auto_activate(self):
        r = run(make(top_k=1, total_cap=3).match("question about iphone"))
        for m in r.matches:
            if m.source == "lexical":
                self.assertEqual(m.score, 0.0)

    def test_disabling_lexical_drops_the_backfill(self):
        r = run(make(top_k=1, total_cap=3, lexical=False)
                .match("question about iphone"))
        self.assertEqual([m.skill for m in r.matches], ["public-qa"])


class TestStatePinning(unittest.TestCase):
    PIN = StatePin("auth_txn_id", "public-qa", "non_empty")

    def test_pin_keeps_an_in_progress_skill_reachable(self):
        # The load-bearing case: a terse mid-flow turn would not retrieve the
        # flow's skill, its tools would unbind, and the flow would break.
        r = run(make(state_pins=(self.PIN,)).match("the iPhone",
                                                   {"auth_txn_id": "t-1"}))
        self.assertIn("public-qa", r.names)
        self.assertEqual(
            next(m for m in r.matches if m.skill == "public-qa").source, "pinned")

    def test_pin_is_inert_when_the_flow_is_not_in_progress(self):
        r = run(make(state_pins=(self.PIN,)).match("device", {}))
        self.assertNotIn("pinned", [m.source for m in r.matches])

    def test_pin_is_inert_on_a_terminal_value(self):
        r = run(make(state_pins=(self.PIN,)).match("device",
                                                   {"auth_txn_id": ""}))
        self.assertNotIn("pinned", [m.source for m in r.matches])

    def test_pin_survives_a_full_cap(self):
        # Dropping a pin because the cap is already full would defeat its
        # purpose. Cap of 1 with lexical backfill filling it, then the pin still
        # has to get through.
        r = run(make(top_k=1, total_cap=1, state_pins=(self.PIN,))
                .match("device iphone", {"auth_txn_id": "t"}))
        self.assertEqual(len([m for m in r.matches if m.source != "pinned"]), 1)
        self.assertIn("public-qa", r.names)

    def test_in_form_matches_a_value_set(self):
        pin = StatePin("step", "public-qa", ("in", ("SHOWN", "PICKED")))
        r = run(make(state_pins=(pin,)).match("device", {"step": "SHOWN"}))
        self.assertIn("public-qa", r.names)
        r2 = run(make(state_pins=(pin,)).match("device", {"step": "DONE"}))
        self.assertNotIn("public-qa", r2.names)


class TestAutoActivation(unittest.TestCase):
    def test_off_by_default(self):
        r = run(make().match("device"))
        self.assertIsNone(r.auto_activated)

    def test_fires_above_threshold_and_margin(self):
        r = run(make(auto_activate=True, auto_activate_min_score=0.9)
                .match("device"))
        self.assertEqual(r.auto_activated, "device-exploration")

    def test_respects_the_exclude_list(self):
        r = run(make(auto_activate=True, auto_activate_min_score=0.9,
                     auto_activate_exclude=frozenset({"device-exploration"}))
                .match("device"))
        self.assertIsNone(r.auto_activated)

    def test_impossible_threshold_is_rejected_not_silently_accepted(self):
        # The original encodes "off" as min_score=2.0, unreachable for cosine.
        # A sentinel meaning off is invisible in a diff.
        with self.assertRaises(ValueError) as cm:
            RetrievalConfig(auto_activate=True, auto_activate_min_score=2.0)
        self.assertIn("auto_activate: false", str(cm.exception))

    def test_a_pinned_skill_cannot_auto_activate(self):
        pin = StatePin("k", "public-qa", "non_empty")
        r = run(make(auto_activate=True, auto_activate_min_score=0.0,
                     top_k=0, state_pins=(pin,)).match("x", {"k": "v"}))
        self.assertIsNone(r.auto_activated)


class TestBinding(unittest.TestCase):
    TOOLS = {"device-exploration": ["search_products", "get_cart_summary"],
             "offer-exploration": ["get_offers"]}
    CATALOG = ["search_products", "get_cart_summary", "get_offers"]

    def test_only_activated_skills_bind_their_tools(self):
        r = make()
        result = run(r.match("device"))
        state = {ACTIVATED_KEY.format(agent="a"): ["device-exploration"]}
        bound, _ = r.bind_tools(result, state, self.TOOLS, self.CATALOG, "a")
        self.assertEqual(bound, ["search_products", "get_cart_summary"])

    def test_an_activated_skill_that_no_longer_matches_loses_its_tools(self):
        r = make()
        result = run(r.match("device"))
        state = {ACTIVATED_KEY.format(agent="a"): ["offer-exploration"]}
        bound, _ = r.bind_tools(result, state, self.TOOLS, self.CATALOG, "a")
        self.assertEqual(bound, [])

    def test_unresolvable_tool_names_are_returned_not_dropped(self):
        # The original does `if name not in candidate_tools: continue` with no
        # log, so a typo means the agent is told to call a tool it lacks.
        r = make()
        result = run(r.match("device"))
        state = {ACTIVATED_KEY.format(agent="a"): ["device-exploration"]}
        _bound, unresolved = r.bind_tools(
            result, state, {"device-exploration": ["typo_tool"]},
            self.CATALOG, "a")
        self.assertEqual(unresolved, ["typo_tool"])

    def test_max_activated_skills_caps_binding(self):
        r = make(max_activated_skills=1)
        result = run(r.match("  "))   # fallback exposes all
        state = {ACTIVATED_KEY.format(agent="a"):
                 ["device-exploration", "offer-exploration"]}
        bound, _ = r.bind_tools(result, state, self.TOOLS, self.CATALOG, "a")
        self.assertNotIn("get_offers", bound)

    def test_auto_activated_skill_binds_without_prior_state(self):
        r = make(auto_activate=True, auto_activate_min_score=0.9)
        result = run(r.match("device"))
        bound, _ = r.bind_tools(result, {}, self.TOOLS, self.CATALOG, "a")
        self.assertIn("search_products", bound)

    def test_enforcement_blocks_an_unmatched_skill(self):
        # ADK parses allowed_tools but never enforces it. This is that check.
        r = make()
        result = run(r.match("device"))
        self.assertTrue(r.allows("device-exploration", result))
        self.assertFalse(r.allows("offer-exploration", result))


class TestConfigValidation(unittest.TestCase):
    def test_total_cap_below_top_k_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            RetrievalConfig(top_k=8, total_cap=4)
        self.assertIn("truncated", str(cm.exception))

    def test_config_from_dict_builds_pins(self):
        cfg = config_from_dict({
            "top_k": 3, "total_cap": 3,
            "state_pins": [
                {"state_key": "k", "skill": "s", "active_when": "non_empty"},
                {"state_key": "j", "skill": "t", "active_when": {"in": ["A", "B"]}},
            ],
        })
        self.assertEqual(len(cfg.state_pins), 2)
        self.assertEqual(cfg.state_pins[1].active_when, ("in", ("A", "B")))

    def test_config_from_dict_ignores_unknown_keys(self):
        # The features registry already rejects unknown options; this just must
        # not explode if one slips through.
        cfg = config_from_dict({"top_k": 2, "total_cap": 2, "bm25": True})
        self.assertEqual(cfg.top_k, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestQueryHistory(unittest.TestCase):
    """Rolling query context, and the silent-failure modes it must not repeat.

    A terse follow-up carries no retrievable signal: FakeEmbedder returns the
    zero vector for text naming no topic, which is the same thing a real embedder
    does approximately for "tell me more". Without history the skill the customer
    is still discussing scores 0.0, falls under min_score and unbinds its tools
    mid-task.

    The reference implementation of this mechanism wrapped its state write in
    `except (AttributeError, TypeError): pass`. Since that write was the key's
    only writer, the average could never accumulate and the feature did nothing
    in production for as long as it was configured on, with no log and no raise.
    Every test here exists because that is invisible without one.
    """

    def _turns(self, *texts, state=None, scope="host", **over):
        r = make(query_history=True, **over)
        state = {} if state is None else state
        return [run(r.match(t, state, scope=scope)) for t in texts], state, r

    def test_a_terse_follow_up_keeps_the_previous_topic_reachable(self):
        results, _, _ = self._turns("show me a device", "tell me more")
        self.assertNotIn("device-exploration",
                         [m.skill for m in results[1].matches if m.source == "embedding"])
        self.assertIn("device-exploration", results[1].names)

    def test_without_the_feature_the_same_follow_up_loses_the_topic(self):
        r = make()
        state = {}
        run(r.match("show me a device", state, scope="host"))
        second = run(r.match("tell me more", state, scope="host"))
        self.assertNotIn("device-exploration", second.names)

    def test_history_is_labelled_so_a_reader_can_tell_it_apart(self):
        results, _, _ = self._turns("show me a device", "tell me more")
        sources = {m.skill: m.source for m in results[1].matches}
        self.assertEqual(sources["device-exploration"], "history")

    def test_history_scores_zero_and_so_can_never_auto_activate(self):
        # Auto-activation requires source == "embedding". A skill only history
        # surfaced must not be injectable without the model asking for it.
        results, _, _ = self._turns("show me a device", "tell me more",
                                    auto_activate=True, auto_activate_min_score=0.5)
        self.assertIsNone(results[1].auto_activated)
        self.assertEqual([m.score for m in results[1].matches
                          if m.source == "history"], [0.0])

    def test_ranking_and_margin_stay_current_turn(self):
        """History widens reachability; it must not move confidence."""
        results, _, _ = self._turns("show me a device", "any offer today")
        self.assertEqual(results[1].matches[0].skill, "offer-exploration")
        self.assertEqual(results[1].matches[0].source, "embedding")

    def test_the_first_turn_reports_no_history(self):
        results, _, _ = self._turns("show me a device")
        self.assertEqual([m for m in results[0].matches if m.source == "history"], [])

    def test_the_average_actually_accumulates_in_state(self):
        from adkl.retrieval import QUERY_HISTORY_KEY
        _, state, _ = self._turns("show me a device", "any offer today")
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="host")],
                         [0.5, 0.5, 0.0])

    def test_older_turns_decay_geometrically(self):
        from adkl.retrieval import QUERY_HISTORY_KEY
        _, state, _ = self._turns("show me a device", "any offer today",
                                  "a question please")
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="host")],
                         [0.25, 0.25, 0.5])

    def test_one_embedding_call_per_turn_regardless_of_depth(self):
        embedder = FakeEmbedder()
        r = make(query_history=True, embedder=embedder)
        state = {}
        for text in ("show me a device", "any offer", "tell me more", "and more"):
            run(r.match(text, state, scope="host"))
        self.assertEqual(embedder.calls, 4)

    def test_two_agents_in_one_session_do_not_blend_each_others_queries(self):
        from adkl.retrieval import QUERY_HISTORY_KEY
        r = make(query_history=True)
        state = {}
        run(r.match("show me a device", state, scope="alpha"))
        run(r.match("any offer today", state, scope="beta"))
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="alpha")], [1.0, 0.0, 0.0])
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="beta")], [0.0, 1.0, 0.0])

    def test_a_read_only_state_turns_the_feature_off_LOUDLY(self):
        """The exact defect this feature was modelled on, inverted into a test."""
        from types import MappingProxyType
        r = make(query_history=True)
        run(r.match("show me a device", {}, scope="host"))
        frozen = MappingProxyType({})
        with self.assertLogs("adkl.retrieval", level="WARNING") as logs:
            result = run(r.match("tell me more", frozen, scope="host"))
        self.assertIn("read-only state", "\n".join(logs.output))
        self.assertNotIn("device-exploration", result.names)

    def test_an_unscoped_caller_gets_the_feature_off_with_a_reason(self):
        r = make(query_history=True)
        with self.assertLogs("adkl.retrieval", level="WARNING") as logs:
            run(r.match("show me a device", {}, scope=None))
        self.assertIn("agent scope", "\n".join(logs.output))

    def test_a_stale_vector_of_the_wrong_dimension_is_ignored_not_blended(self):
        # Re-indexing with a different embedding model leaves an old vector in a
        # live session. Blending across spaces returns garbage rather than failing.
        from adkl.retrieval import QUERY_HISTORY_KEY
        state = {QUERY_HISTORY_KEY.format(agent="host"): [1.0] * 768}
        results, state, _ = self._turns("show me a device", state=state)
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="host")], [1.0, 0.0, 0.0])

    def test_the_weight_is_configurable(self):
        from adkl.retrieval import QUERY_HISTORY_KEY
        _, state, _ = self._turns("show me a device", "any offer today",
                                  query_history_weight=0.75)
        self.assertEqual(state[QUERY_HISTORY_KEY.format(agent="host")],
                         [0.75, 0.25, 0.0])

    def test_a_degenerate_weight_is_refused_rather_than_silently_inert(self):
        for weight in (0.0, 1.0, -0.5, 2.0):
            with self.subTest(weight=weight):
                with self.assertRaises(ValueError) as caught:
                    RetrievalConfig(query_history_weight=weight)
                self.assertIn("query_history: false", str(caught.exception))

    def test_the_feature_is_off_by_default(self):
        self.assertFalse(RetrievalConfig().query_history)

    def test_the_feature_block_maps_both_options(self):
        cfg = config_from_dict({"query_history": True, "query_history_weight": 0.25})
        self.assertTrue(cfg.query_history)
        self.assertEqual(cfg.query_history_weight, 0.25)
