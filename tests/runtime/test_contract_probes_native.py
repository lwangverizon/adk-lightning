"""Actual native execution observations, with deliberately faulty host variants.

No provider or business system is invoked. These are examples of host-owned
instrumentation, not generic automatic detection of arbitrary host retry code.
"""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.contract_probes import no_replay, rejected_call_recovery, single_emitter, state_isolation
from adkl.widgets import Role, WidgetSpec, stage


class Script(BaseLlm):
    model: str = 'synthetic'
    script: list
    index: int = 0

    async def generate_content_async(self, request, stream=False):
        parts = self.script[self.index]
        self.index += 1
        yield LlmResponse(content=types.Content(role='model', parts=parts))


def call(name, **args):
    return [types.Part(function_call=types.FunctionCall(name=name, args=args))]


async def turns(agent, messages):
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name='probe', user_id='synthetic')
    runner = Runner(agent=agent, app_name='probe', session_service=sessions)
    events = []
    try:
        for message in messages:
            events += [event async for event in runner.run_async(user_id='synthetic', session_id=session.id,
                new_message=types.Content(role='user', parts=[types.Part(text=message)]))]
        current = await sessions.get_session(app_name='probe', user_id='synthetic', session_id=session.id)
        return events, current
    finally:
        await runner.close()


@pytest.mark.parametrize('unsafe_retry', [False, True])
def test_empty_response_after_effect_detects_host_whole_turn_replay(unsafe_retry):
    async def scenario():
        executions = []
        def effect() -> dict:
            """Synthetic backend effect, not a real transaction."""
            executions.append('payment')
            return {'status': 'success'}
        model = Script(script=[call('effect'), [], call('effect'), []])
        agent = LlmAgent(name='probe', model=model, tools=[effect])
        # The buggy host repeats the entire user turn after an empty final
        # response. Instrumenting the backend catches what a text-only test misses.
        await turns(agent, ['pay'] * (2 if unsafe_retry else 1))
        assert no_replay(executions, expected=['payment']) is (not unsafe_retry)
    asyncio.run(scenario())


@pytest.mark.parametrize('claim_before_admission', [False, True])
def test_rejected_call_then_valid_call_in_same_native_turn(claim_before_admission):
    async def scenario():
        outcomes, effects = [], []
        def strategy(valid: bool, tool_context: ToolContext) -> dict:
            """Admit one valid synthetic strategy per invocation."""
            if claim_before_admission or valid:
                taken = tool_context.state.get('temp:slot', False)
                tool_context.state['temp:slot'] = True
            else:
                taken = False
            accepted = valid and not taken
            outcomes.append(accepted)
            if accepted:
                effects.append('valid')
            return {'accepted': accepted}
        agent = LlmAgent(name='probe', tools=[strategy], model=Script(script=[
            call('strategy', valid=False), call('strategy', valid=True), [types.Part(text='done')]]))
        await turns(agent, ['choose'])
        assert rejected_call_recovery(outcomes) is (not claim_before_admission)
        assert effects == ([] if claim_before_admission else ['valid'])
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['success', 'partial', 'cancel'])
@pytest.mark.parametrize('native_replay', [False, True])
def test_real_sdk_flush_observations_detect_second_owner(failure, native_replay):
    async def scenario():
        attempts = []
        async def build(ctx, items, rid):
            return [({'item': item}, '') for item in items]
        spec = WidgetSpec(key='card', widget_id='card', payload_type='fact', role=Role.DATA_PRIMARY, build=build)
        def begin(callback_context):
            stage(callback_context.state, spec, ['a', 'b'])
        async def sink(ctx, widget_id, payload):
            attempts.append(('sdk', payload['item']))
            if payload['item'] == 'b':
                if failure == 'partial':
                    raise ValueError('synthetic sink failure')
                if failure == 'cancel':
                    raise asyncio.CancelledError
        def tail(callback_context):
            if native_replay:
                attempts.append(('native', 'a'))
        binding = RuntimeBindings(skills=[], feature_config={'pipeline': ['staged_widgets']},
                                  widget_specs=[spec], widget_sink=sink)
        agent = LlmAgent(name='probe', model=Script(script=[[types.Part(text='done')]]),
            **binding.agent_kwargs(callbacks={'before_agent_callback': [begin], 'after_agent_callback': [tail]}))
        if failure == 'partial':
            with pytest.raises(ValueError, match='synthetic sink failure'):
                await turns(agent, ['show'])
        else:
            # ADK's workflow Runner consumes root-task cancellation. The SDK
            # callback still must not run the host completion tail on that path.
            await turns(agent, ['show'])
        # Cancellation must not reach even the faulty host tail.
        expected_pass = not native_replay or failure == 'cancel'
        assert single_emitter(attempts, owner='sdk', expected=['a', 'b']) is expected_pass
    asyncio.run(scenario())


@pytest.mark.parametrize('keep_stale', [False, True])
def test_failed_refresh_checks_native_persisted_and_returned_state(keep_stale):
    async def scenario():
        returned = []
        def refresh(fail: bool, tool_context: ToolContext) -> dict:
            """Fake refresh; invalidate before a failed backend attempt."""
            if not keep_stale:
                tool_context.state['cached'] = None
            if not fail:
                tool_context.state['cached'] = 'fresh'
            result = {'value': tool_context.state.get('cached')}
            returned.append(result['value'])
            return result
        agent = LlmAgent(name='probe', tools=[refresh], model=Script(script=[
            call('refresh', fail=False), [types.Part(text='done')],
            call('refresh', fail=True), [types.Part(text='unavailable')]]))
        _, current = await turns(agent, ['refresh', 'refresh again'])
        assert state_isolation({'persisted': current.state.get('cached'), 'returned': returned[-1]},
                               expected={'persisted': None, 'returned': None}) is (not keep_stale)
    asyncio.run(scenario())
