"""Shared shortcut executes on real bindings, including custom selectors."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.skills import models

from adkl.bindings import RuntimeBindings
from adkl.retrieval import Match, MatchResult, RetrievalConfig, SkillRetrieval
from test_bindings import ScriptModel, run


class Embedder:
    calls = 0

    async def embed(self, text):
        self.calls += 1
        return [1., 0.]


class Selector:
    calls = 0

    async def match(self, text, state):
        self.calls += 1
        return MatchResult([Match("support", .9, "embedding")])


def skills(count):
    return [models.Skill(frontmatter=models.Frontmatter(
        name="support" if i == 0 else f"skill-{i}", description=f"DESCRIPTION::{i}"),
        instructions=f"BODY::{i}") for i in range(count)]


@pytest.mark.parametrize("count", [0, 1, 4, 5, 6])
@pytest.mark.parametrize("custom", [False, True])
def test_native_shortcut_and_exact_boundary(count, custom):
    async def scenario():
        pool = skills(count)
        embedder, selector = Embedder(), Selector()
        source = {"selector": selector} if custom else {
            "vectors": {s.name: [1., 0.] for s in pool}, "embedder": embedder}
        binding = RuntimeBindings(skills=pool, feature_config={
            "pipeline": ["skill_retrieval"], "skill_retrieval": {
                "query_history": True, "auto_activate": False,
            }}, **source)
        model = ScriptModel()
        _, session = await run(LlmAgent(name="host", model=model,
                                       **binding.agent_kwargs()))
        called = selector.calls if custom else embedder.calls
        if count < 5:
            assert called == 0
            for i in range(count):
                assert f"DESCRIPTION::{i}" in model.requests[0]
                assert f"BODY::{i}" not in model.requests[0]
            assert not session.state.get("_adk_activated_skill_host")
            assert "adkl:query_history:host" not in session.state
            diag = session.state["adkl:last_turn:host"]
            assert diag["retrieval_mode"] == "all_descriptions"
            assert not diag["fallback"]
            assert diag["picked"] is None
        else:
            assert called == 1
    asyncio.run(scenario())


def test_exclusions_count_and_explicit_load():
    async def scenario():
        selector = Selector()
        binding = RuntimeBindings(skills=skills(5), selector=selector, feature_config={
            "pipeline": ["skill_retrieval"], "skill_retrieval": {
                "excluded_skills": ["skill-4"], "auto_activate": True,
            }})
        model = ScriptModel(explicit=True)
        _, session = await run(LlmAgent(name="host", model=model,
                                       **binding.agent_kwargs()))
        assert selector.calls == 0
        assert "DESCRIPTION::4" not in model.requests[0]
        assert "BODY::0" not in model.requests[0]
        assert "BODY::0" in model.requests[-1]
        assert session.state["_adk_activated_skill_host"] == ["support"]
    asyncio.run(scenario())


def test_opt_out_calls_custom_selector():
    async def scenario():
        selector = Selector()
        binding = RuntimeBindings(skills=skills(1), selector=selector, feature_config={
            "pipeline": ["skill_retrieval"],
            "skill_retrieval": {"min_skills_for_retrieval": 0}})
        await run(LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs()))
        assert selector.calls == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("minimum", [-1, True, 1.5])
def test_invalid_threshold(minimum):
    with pytest.raises(ValueError, match="min_skills_for_retrieval"):
        RetrievalConfig(min_skills_for_retrieval=minimum)


def test_direct_retrieval_skips_embeddings_and_caps():
    embedder = Embedder()
    retrieval = SkillRetrieval(
        RetrievalConfig(top_k=1, total_cap=1, auto_activate=True, query_history=True),
        {s.name: [1., 0.] for s in skills(4)}, embedder)
    state = {}
    result = asyncio.run(retrieval.match("help", state, scope="host"))
    assert len(result.matches) == 4
    assert all(m.source == "catalog" for m in result.matches)
    assert not result.fallback and result.auto_activated is None
    assert result.margin == 0
    assert not state and embedder.calls == 0
