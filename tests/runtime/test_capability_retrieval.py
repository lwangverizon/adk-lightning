"""Unified discovery gates native operations, not just descriptive prompt text."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adkl.capability_bindings import CapabilityBindings, CapabilityDescriptor
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection
from tests.support.capabilities import fixtures
from tests.support.native import run


def context(session="one", invocation="turn"):
    return SimpleNamespace(session=SimpleNamespace(app_name="app", user_id="u", id=session),
                           agent_name="parent", invocation_id=invocation)


def test_coalesces_same_turn_but_isolates_sessions_and_rechecks_continuity():
    async def exercise():
        rank = AsyncMock(return_value=CapabilitySelection(("a",)))
        keep = []
        retrieval = CapabilityRetrieval(rank, retain=lambda ctx, cat: keep, cache_size=2)
        catalog = (CapabilityDescriptor("a", "skill", "A", "load_skill"),
                   CapabilityDescriptor("b", "workflow", "B", "b"))
        results = await asyncio.gather(*(retrieval.select(context(), catalog) for _ in range(8)))
        assert all(r.names == ("a",) for r in results)
        assert rank.await_count == 1
        keep.append("b")
        assert (await retrieval.select(context(), catalog)).names == ("a", "b")
        await retrieval.select(context("two"), catalog)
        await retrieval.select(context("one", "next"), catalog)
        assert rank.await_count == 3
        assert len(retrieval._cache) == 2
        assert not retrieval._locks
    asyncio.run(exercise())


@pytest.mark.parametrize("value", [None, CapabilitySelection(("unknown",)),
                                   CapabilitySelection((123,)),
                                   CapabilitySelection((), fallback="yes")])
def test_invalid_selection_never_becomes_all_catalog(value):
    async def exercise():
        retrieval = CapabilityRetrieval(lambda ctx, cat: value)
        with pytest.raises((ValueError, TypeError)):
            await retrieval.select(context(), ())
        assert not retrieval._cache and not retrieval._locks
    asyncio.run(exercise())


def test_cancellation_does_not_poison_waiter_or_cache():
    async def exercise():
        started = asyncio.Event()
        attempts = 0
        async def rank(ctx, cat):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                started.set()
                await asyncio.Event().wait()
            return CapabilitySelection(())
        retrieval = CapabilityRetrieval(rank)
        first = asyncio.create_task(retrieval.select(context(), ()))
        await started.wait()
        second = asyncio.create_task(retrieval.select(context(), ()))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await second == CapabilitySelection(())
        assert not retrieval._locks
    asyncio.run(exercise())


def test_native_shortlist_hides_unrelated_workflow_and_child():
    from google.adk.agents import LlmAgent
    async def exercise():
        old, _, model, specialist, calls = fixtures("skill")
        # Build fresh child because the fixture tree already owns its child.
        child = LlmAgent(name="specialist", description="Diagnose problems",
                         model=specialist, mode="single_turn")
        seen = []
        def rank(ctx, catalog):
            seen.append(catalog)
            return CapabilitySelection(("policy-help",))
        binding = CapabilityBindings(skills=old._capability_skills,
            workflows=old._workflows, sub_agents=[child],
            retrieval=CapabilityRetrieval(rank))
        await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        assert len(seen) == 1
        assert {d.kind for d in seen[0]} == {"skill", "workflow", "agent"}
        first = model.requests[0]
        assert "device_discovery" not in first.tools_dict
        assert "specialist" not in first.tools_dict
        assert "Diagnose problems" not in str(first.config.system_instruction)
        assert "PRIVATE_SKILL_BODY" in str(model.requests[1].contents)
        assert not calls and not specialist.requests
    asyncio.run(exercise())


def test_cached_shortlist_cannot_authorize_a_revoked_workflow():
    from google.adk.agents import LlmAgent
    from adkl.capability_policy import CapabilityDecision

    async def exercise():
        old, _, model, _, calls = fixtures("workflow")
        permitted = {"device_discovery"}

        class Policy:
            def expose(self, ctx, catalog):
                return list(permitted)

            def authorize(self, ctx, request):
                return CapabilityDecision(True)

        def rank(ctx, catalog):
            return CapabilitySelection(tuple(d.name for d in catalog))

        def revoke(tool, args, tool_context):
            permitted.clear()

        binding = CapabilityBindings(workflows=old._workflows, policy=Policy(),
                                     retrieval=CapabilityRetrieval(rank))
        await run(LlmAgent(name="host", model=model, **binding.agent_kwargs(
            callbacks={"before_tool_callback": revoke})))
        assert "device_discovery" in model.requests[0].tools_dict
        assert "CAPABILITY_DENIED" in str(model.requests[1].contents)
        assert not calls
    asyncio.run(exercise())


def test_fallback_is_explicit_and_does_not_widen_the_catalog():
    async def exercise():
        catalog = (CapabilityDescriptor("a", "skill", "A", "load_skill"),)
        retrieval = CapabilityRetrieval(
            lambda ctx, cat: CapabilitySelection((), fallback=True))
        assert await retrieval.select(context(), catalog) == CapabilitySelection((), True)
    asyncio.run(exercise())


def test_waiter_cancellation_does_not_cancel_ranking_owner():
    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()

        async def rank(ctx, catalog):
            started.set()
            await finish.wait()
            return CapabilitySelection(())

        retrieval = CapabilityRetrieval(rank)
        owner = asyncio.create_task(retrieval.select(context(), ()))
        await started.wait()
        waiter = asyncio.create_task(retrieval.select(context(), ()))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not owner.done()
        finish.set()
        assert await owner == CapabilitySelection(())
        assert not retrieval._locks
    asyncio.run(exercise())


def test_invalid_retention_does_not_poison_completed_ranking():
    async def exercise():
        catalog = (CapabilityDescriptor("a", "skill", "A", "load_skill"),)
        rank = AsyncMock(return_value=CapabilitySelection(("a",)))
        retained = ["revoked"]
        retrieval = CapabilityRetrieval(rank, retain=lambda ctx, cat: retained)
        with pytest.raises(ValueError, match="ineligible or unknown"):
            await retrieval.select(context(), catalog)
        retained.clear()
        assert (await retrieval.select(context(), catalog)).names == ("a",)
        assert rank.await_count == 1
        assert not retrieval._locks
    asyncio.run(exercise())


def test_contract_imports_remain_compatible():
    from adkl import capability_contracts

    assert CapabilityDescriptor is capability_contracts.CapabilityDescriptor
    assert CapabilitySelection is capability_contracts.CapabilitySelection


def test_retrieval_rejects_unscoped_handoffs_and_independent_native_selector():
    from google.adk.agents import LlmAgent
    from google.adk.tools.skill_toolset import SkillToolset
    retrieval = CapabilityRetrieval(lambda ctx, cat: CapabilitySelection(()))
    with pytest.raises(ValueError, match="single_turn"):
        CapabilityBindings(retrieval=retrieval, sub_agents=[
            LlmAgent(name="chat", description="Chat", model="fixture")])
    with pytest.raises(TypeError, match="shared capability discovery"):
        CapabilityBindings(retrieval=retrieval, native_toolset=SkillToolset(skills=[]))
