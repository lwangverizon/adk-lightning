"""What `prepare` tells a host when its selector breaks the contract.

Five predicates shared one message across "invalid" and "unavailable", and the
raise happens at before-agent index 0 -- so the turn is already lost and the
error text is the entire diagnostic a host gets. Nothing covered it, which is how
it stayed that way. A REPEATED candidate is the odd one out: it is deduped rather
than refused, because native ADK treats a repeat as cosmetic and killing a
customer turn over one is disproportionate.
"""
import asyncio

import pytest
from google.adk.agents import LlmAgent

from adkl.bindings import RuntimeBindings
from adkl.retrieval import Match, MatchResult
from adkl.runtime_state import agent_key
from test_bindings import ScriptModel, plain_skill, run


def _turn(result, **cfg):
    class Selector:
        async def match(self, text, state):
            return result
    binding = RuntimeBindings(
        skills=[plain_skill("flow"), plain_skill("next")], selector=Selector(),
        feature_config={"pipeline": ["skill_retrieval"],
                        "skill_retrieval": {"min_skills_for_retrieval": 0, **cfg}})
    return run(LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs()))


def test_a_non_matchresult_names_the_type_it_got():
    with pytest.raises(ValueError, match="got list"):
        asyncio.run(_turn([Match("flow", 1., "embedding")]))


def test_an_unserved_skill_is_named():
    with pytest.raises(ValueError, match="does not serve: ghost"):
        asyncio.run(_turn(MatchResult([Match("flow", 1., "embedding"),
                                       Match("ghost", .5, "embedding")])))


def test_an_excluded_skill_counts_as_unserved():
    with pytest.raises(ValueError, match="does not serve: next"):
        asyncio.run(_turn(MatchResult([Match("next", 1., "embedding")]),
                          excluded_skills=["next"]))


@pytest.mark.parametrize("score", [float("nan"), float("inf"), "high"])
def test_a_bad_score_names_the_skill_holding_it(score):
    with pytest.raises(ValueError, match="score for: flow"):
        asyncio.run(_turn(MatchResult([Match("flow", score, "embedding")])))


def test_a_non_finite_margin_is_reported_separately_from_scores():
    with pytest.raises(ValueError, match="non-finite margin"):
        asyncio.run(_turn(MatchResult([Match("flow", 1., "embedding")],
                                      ranking_margin=float("nan"))))


def test_a_pin_outside_the_candidates_is_reported_separately():
    with pytest.raises(ValueError, match="pins must name exposed candidates"):
        asyncio.run(_turn(MatchResult([Match("flow", 1., "embedding")],
                                      pinned=frozenset({"next"}))))


def test_a_repeated_candidate_is_deduped_not_fatal():
    """Two state keys naming one skill is ordinary host config, not an outage."""
    _, session = asyncio.run(_turn(MatchResult([
        Match("flow", 1., "embedding"), Match("next", .5, "embedding"),
        Match("flow", .4, "pinned")])))
    # SELECTION is a `temp:` key and does not survive the turn; the diagnostics
    # record of the same ranking does, and is what a host reads anyway.
    ranked = session.state[agent_key("adkl:last_turn", _Named("host"))]["matches"]
    assert [m["skill"] for m in ranked] == ["flow", "next"], ranked


def test_dedupe_keeps_the_fields_a_positional_rebuild_would_drop():
    """Losing `fallback` would turn a fallback turn into a confident one."""
    from dataclasses import replace
    original = MatchResult([Match("flow", 1., "embedding"), Match("flow", .4, "pinned")],
                           fallback=True, ranking_margin=0.25)
    deduped = replace(original, matches=original.matches[:1])
    assert (deduped.fallback, deduped.margin) == (True, 0.25)


class _Named:
    """`agent_key` reads only `agent_name`."""

    def __init__(self, name):
        self.agent_name = name


class TestScopePassing:
    """How `scope` reaches a selector without breaking the two-argument protocol.

    Rolling query history needs an agent name to key its state on, but hosts
    already ship `async def match(self, text, state)`. Passing a third argument
    unconditionally would break every one of them at the first turn, so the
    binding checks for an advertised `accepts_scope` capability rather than for
    `isinstance(..., SkillRetrieval)` -- a class check is what makes a host's own
    subclass or wrapper fall off the supported path silently.
    """

    def _run(self, selector):
        binding = RuntimeBindings(
            skills=[plain_skill("flow")], selector=selector,
            feature_config={"pipeline": ["skill_retrieval"],
                            "skill_retrieval": {"min_skills_for_retrieval": 0}})
        asyncio.run(run(LlmAgent(name="host", model=ScriptModel(),
                                 **binding.agent_kwargs())))

    def test_a_legacy_two_argument_selector_is_called_unchanged(self):
        seen = []

        class Legacy:
            async def match(self, text, state):
                seen.append("called")
                return MatchResult([Match("flow", 1., "embedding")])

        self._run(Legacy())
        assert seen == ["called"]

    def test_a_selector_advertising_the_capability_receives_the_agent_name(self):
        seen = []

        class Scoped:
            accepts_scope = True

            async def match(self, text, state, *, scope=None):
                seen.append(scope)
                return MatchResult([Match("flow", 1., "embedding")])

        self._run(Scoped())
        assert seen == ["host"]

    def test_a_selector_that_only_declares_it_falsely_is_not_passed_scope(self):
        # `accepts_scope = False` is the same contract as absent, so a host can
        # turn the capability off on a subclass that inherits it.
        class Optout:
            accepts_scope = False

            async def match(self, text, state):
                return MatchResult([Match("flow", 1., "embedding")])

        self._run(Optout())

    def test_the_builtin_retrieval_advertises_the_capability(self):
        from adkl.retrieval import SkillRetrieval
        assert SkillRetrieval.accepts_scope is True
