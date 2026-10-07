"""Shared lifecycle policy with native execution, not a second executor."""
import asyncio
from types import SimpleNamespace

import pytest

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_contracts import CapabilityDescriptor, CapabilitySelection
from adkl.capability_execution import execute
from adkl.capability_lifecycle import (
    CapabilityLifecycle, CapabilityLifecycleConfig, SkillLifecycleAdapter,
)
from adkl.capability_policy import CapabilityOutcome, CapabilityRequest, CapabilityDecision
from adkl.capability_retrieval import CapabilityRetrieval

CATALOG = tuple(CapabilityDescriptor(n, 'skill', n, 'load_skill') for n in 'abcd') + tuple(
    CapabilityDescriptor(n, k, n, n) for n, k in [('flow', 'workflow'), ('child', 'agent'), ('lookup', 'tool')])


def fixture():
    ctx = SimpleNamespace(state={}, agent_name='host', invocation_id='one',
        function_call_id='call', session=SimpleNamespace(app_name='app', user_id='u', id='s'))
    clock, owner = [100.0], object()
    def release(c, name):
        c.state['active'] = [n for n in c.state.get('active', []) if n != name]
    lifecycle = CapabilityLifecycle(CapabilityLifecycleConfig(continuation_ttl_seconds=10),
        clock=lambda: clock[0], scope=lambda c, n: c.state.get('cart', 'cart-one'),
        skill_adapter=SkillLifecycleAdapter(owner, lambda c: c.state.get('active', []), release))
    lifecycle.bind(CATALOG, skill_owner=owner)
    return ctx, lifecycle, clock


def request(name='flow', kind='workflow'):
    return CapabilityRequest(name, kind, 'load_skill' if kind == 'skill' else name, {})


def wait(ctx, lifecycle, req):
    token = lifecycle.started(ctx, req)
    lifecycle.finished(ctx, req, token, continuation='waiting')


def test_retention_reads_live_activity_without_activation():
    ctx, lifecycle, _ = fixture()
    assert lifecycle.retained(ctx, CATALOG) == ()
    assert ctx.state == {}
    ctx.state['active'] = ['a', 'b', 'c', 'd']
    lifecycle.finished(ctx, request('d', 'skill'), None, result={'instructions': 'body', 'skill_name': 'd'})
    assert ctx.state['active'] == ['b', 'c', 'd']
    lifecycle.release(ctx, 'b')
    assert lifecycle.retained(ctx, CATALOG) == ('c', 'd')
    assert lifecycle.retained(ctx, (CATALOG[3],)) == ('d',)
    lifecycle.finished(ctx, request('a', 'skill'), None, result={'error': 'missing'})
    assert ctx.state['active'] == ['c', 'd']


@pytest.mark.parametrize('name,kind', [('flow', 'workflow'), ('child', 'agent'), ('lookup', 'tool')])
def test_shared_continuation_scope_expiry_release(name, kind):
    ctx, lifecycle, clock = fixture()
    req = request(name, kind)
    token = lifecycle.started(ctx, req)
    assert lifecycle.running(ctx, name)
    with pytest.raises(ValueError, match='running'):
        lifecycle.release(ctx, name)
    lifecycle.finished(ctx, req, token, continuation='waiting')
    ctx.invocation_id = 'two'
    assert lifecycle.retained(ctx, CATALOG) == (name,)
    ctx.state['cart'] = 'different-cart'
    assert lifecycle.retained(ctx, CATALOG) == ()
    lifecycle.reconcile(ctx, CATALOG)
    ctx.state['cart'] = 'cart-one'
    assert lifecycle.retained(ctx, CATALOG) == ()
    wait(ctx, lifecycle, req)
    clock[0] = 111
    assert lifecycle.retained(ctx, CATALOG) == ()
    lifecycle.reconcile(ctx, CATALOG)
    wait(ctx, lifecycle, req)
    lifecycle.release(ctx, name)
    assert lifecycle.retained(ctx, CATALOG) == ()
    assert 'cart-one' not in str(ctx.state['adkl:capability_lifecycle:host'])


@pytest.mark.parametrize('kind,name', [('workflow', 'flow'), ('agent', 'child'), ('tool', 'lookup')])
@pytest.mark.parametrize('status,continuation', [('completed', 'none'), ('completed', 'waiting'), ('failed', 'none')])
def test_dispatch_separates_operation_outcome_from_continuation(kind, name, status, continuation):
    async def run():
        ctx, lifecycle, _ = fixture()
        events = []
        binding = SimpleNamespace(_lifecycle=lifecycle, _on_event=events.append,
            _outcomes={name: lambda req, result: CapabilityOutcome(status, continuation=continuation)},
            _policy=SimpleNamespace(authorize=lambda c, r: CapabilityDecision(True)),
            discover=lambda c: asyncio.sleep(0, result={name}))
        async def operation():
            assert lifecycle.running(ctx, name)
            return {'private': 'not lifecycle state'}
        await execute(binding, ctx, request(name, kind), operation)
        assert events[-1].status == status
        assert lifecycle.retained(ctx, CATALOG) == ((name,) if continuation == 'waiting' else ())
        binding._policy.authorize = lambda c, r: CapabilityDecision(False, 'revoked')
        assert (await execute(binding, ctx, request(name, kind), operation))['error_code'] == 'CAPABILITY_DENIED'
        assert 'private' not in str(ctx.state)
    asyncio.run(run())


@pytest.mark.parametrize('kind,name', [('workflow', 'flow'), ('agent', 'child')])
@pytest.mark.parametrize('cancel', [False, True])
def test_failed_execution_cleans_up_and_preserves_exception(kind, name, cancel):
    async def run():
        ctx, lifecycle, _ = fixture()
        binding = SimpleNamespace(_lifecycle=lifecycle, _on_event=None, _outcomes={},
            _policy=None, discover=lambda c: asyncio.sleep(0, result={name}))
        async def operation():
            raise asyncio.CancelledError() if cancel else RuntimeError('fixture')
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await execute(binding, ctx, request(name, kind), operation)
        assert not lifecycle.retained(ctx, CATALOG)
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['workflow', 'agent'])
def test_native_runner_persists_waiting_and_reconciles_scope(kind):
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from pydantic import Field
    from tests.support.capabilities import ChoiceModel, workflow

    name = 'device_discovery' if kind == 'workflow' else 'specialist'
    class Model(BaseLlm):
        requests: list = Field(default_factory=list)
        async def generate_content_async(self, req, stream=False):
            self.requests.append(req.model_copy(deep=True))
            part = (types.Part.from_function_call(name=name, args={'query': 'fixture'} if kind == 'workflow' else {'request': 'fixture'})
                    if len(self.requests) == 1 else types.Part(text='Done'))
            yield LlmResponse(content=types.Content(role='model', parts=[part]))

    async def run():
        rankings = []
        def rank(ctx, catalog):
            rankings.append(ctx.invocation_id)
            return CapabilitySelection((name,) if len(rankings) == 1 else ())
        lifecycle = CapabilityLifecycle(scope=lambda ctx, name: ctx.state.get('cart'))
        objects = ({'workflows': [workflow(lambda ctx, node_input: {'step': 'pending'})]}
                   if kind == 'workflow' else {'sub_agents': [LlmAgent(name=name,
                       description='Specialist', mode='single_turn',
                       model=ChoiceModel(model='fixture', choice='specialist'))]})
        binding = CapabilityBindings(**objects, lifecycle=lifecycle,
            retrieval=CapabilityRetrieval(rank),
            outcome_adapters={name: lambda req, result: CapabilityOutcome(continuation='waiting')})
        model = Model(model='fixture')
        def set_scope(callback_context):
            count = callback_context.state.get('turn', 0) + 1
            callback_context.state['turn'] = count
            callback_context.state['cart'] = 'B' if count == 3 else 'A'
        runner = InMemoryRunner(agent=LlmAgent(name='host', model=model,
            **binding.agent_kwargs(callbacks={'before_agent_callback': set_scope})), app_name='fixture')
        session = await runner.session_service.create_session(app_name='fixture', user_id='u')
        try:
            for text in ('start', 'continue'):
                async for _ in runner.run_async(user_id='u', session_id=session.id,
                        new_message=types.Content(role='user', parts=[types.Part(text=text)])):
                    pass
            assert len(rankings) == 2
            assert name in model.requests[-1].tools_dict
            stored = await runner.session_service.get_session(app_name='fixture', user_id='u', session_id=session.id)
            assert stored.state['adkl:capability_lifecycle:host'][name]['status'] == 'waiting'
            for text in ('switch cart', 'switch back'):
                async for _ in runner.run_async(user_id='u', session_id=session.id,
                        new_message=types.Content(role='user', parts=[types.Part(text=text)])):
                    pass
                assert name not in model.requests[-1].tools_dict
            stored = await runner.session_service.get_session(app_name='fixture', user_id='u', session_id=session.id)
            assert stored.state['adkl:capability_lifecycle:host'] == {}
        finally:
            await runner.close()
    asyncio.run(run())


def test_scope_change_during_execution_and_stale_completion():
    ctx, lifecycle, _ = fixture()
    req = request()
    token = lifecycle.started(ctx, req)
    ctx.state['cart'] = 'different-cart'
    lifecycle.finished(ctx, req, token, continuation='waiting')
    assert not lifecycle.retained(ctx, CATALOG)
    old = lifecycle.started(ctx, req)
    ctx.invocation_id = 'two'
    new = lifecycle.started(ctx, req)
    lifecycle.finished(ctx, req, old)
    assert lifecycle.running(ctx, 'flow')
    lifecycle.finished(ctx, req, new)
    assert not lifecycle.running(ctx, 'flow')


def test_eligibility_reconciliation_prevents_revival():
    ctx, lifecycle, _ = fixture()
    wait(ctx, lifecycle, request())
    lifecycle.reconcile(ctx, ())
    assert not lifecycle.retained(ctx, CATALOG)


def test_requires_explicit_matching_skill_owner():
    lifecycle = CapabilityLifecycle()
    with pytest.raises(ValueError, match='explicit adapter'):
        lifecycle.bind(CATALOG, skill_owner=object())


@pytest.mark.parametrize('kwargs', [{'max_active_skills': 0}, {'max_active_skills': True},
    {'continuation_ttl_seconds': float('nan')}, {'max_waiting_capabilities': -1}])
def test_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        CapabilityLifecycleConfig(**kwargs)


def test_shared_waiting_budget_and_session_isolation():
    ctx, _, _ = fixture()
    lifecycle = CapabilityLifecycle(CapabilityLifecycleConfig(max_waiting_capabilities=1))
    lifecycle.bind(CATALOG[4:])
    wait(ctx, lifecycle, request())
    wait(ctx, lifecycle, request('child', 'agent'))
    assert lifecycle.retained(ctx, CATALOG) == ('child',)
    other = SimpleNamespace(**{**vars(ctx), 'state': {}})
    assert lifecycle.retained(other, CATALOG) == ()


def test_binding_rejects_handoff_lifecycle_even_without_retrieval():
    from google.adk.agents import LlmAgent
    with pytest.raises(ValueError, match='single_turn'):
        CapabilityBindings(lifecycle=CapabilityLifecycle(), sub_agents=[
            LlmAgent(name='chat', description='Chat handoff')])


def test_scope_adapter_failure_after_return_releases_running_record():
    async def run():
        def scope(ctx, name):
            if ctx.state.get('broken'):
                raise RuntimeError('scope unavailable')
            return 'cart'
        lifecycle = CapabilityLifecycle(scope=scope)
        lifecycle.bind(CATALOG[4:])
        ctx, _, _ = fixture()
        binding = SimpleNamespace(_lifecycle=lifecycle, _on_event=None, _policy=None,
            _outcomes={'flow': lambda r, v: CapabilityOutcome(continuation='waiting')},
            discover=lambda c: asyncio.sleep(0, result={'flow'}))
        async def operation():
            ctx.state['broken'] = True
            return {}
        with pytest.raises(RuntimeError, match='scope unavailable'):
            await execute(binding, ctx, request(), operation)
        assert not lifecycle.running(ctx, 'flow')
    asyncio.run(run())


def test_invalid_continuation():
    with pytest.raises(ValueError):
        CapabilityOutcome('failed', continuation='waiting')
    with pytest.raises(ValueError):
        CapabilityOutcome('waiting')
