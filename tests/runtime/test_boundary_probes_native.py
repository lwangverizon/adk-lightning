"""Real Runner observations with intentionally faulty hosts, no model/backend I/O."""
import asyncio
import hashlib
import json

import pytest
from google.adk.agents import LlmAgent
from google.adk.tools import ToolContext
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from adkl.contract_probes import consent_binding, handoff_continuity, outcome_consistency
from adkl.request_costs import validate
from test_contract_probes_native import Script, call, turns


@pytest.mark.parametrize('unsafe', [False, True])
@pytest.mark.parametrize('reply', ['yes', 'no', 'yes but not yet', 'balance?'])
def test_native_review_and_write_boundary(unsafe, reply):
    async def scenario():
        effects, observed = [], []

        def review(tool_context: ToolContext) -> dict:
            """Record a synthetic reviewed target; never real financial data."""
            tool_context.state['review'] = {'account': 'fixture', 'target': 'fee',
                                           'invocation': tool_context.invocation_id}
            return {'reviewed': True}

        def submit(tool_context: ToolContext) -> dict:
            """Fixture consent admission and fake-backend instrumentation."""
            marker = tool_context.state.get('review', {})
            text = ' '.join(p.text for p in tool_context.user_content.parts if p.text)
            checks = {'affirmative': text == 'yes',
                      'account_matches': marker.get('account') == 'fixture',
                      'target_matches': marker.get('target') == 'fee',
                      'fresh': bool(marker),
                      'next_user_turn': marker.get('invocation') != tool_context.invocation_id,
                      'not_consumed': bool(marker)}
            observed.append(checks)
            if unsafe or all(checks.values()):
                effects.append('write')
                tool_context.state['review'] = None
            return {'submitted': bool(effects)}

        agent = LlmAgent(name='probe', tools=[review, submit], model=Script(script=[
            call('review'), [types.Part(text='Review the fee?')],
            call('submit'), [types.Part(text='done')]]))
        await turns(agent, ['review', reply])
        expected = ['write'] if reply == 'yes' else []
        assert consent_binding(checks=observed[0], effects=effects,
                               expected_effects=expected) is (not unsafe or reply == 'yes')
    asyncio.run(scenario())


@pytest.mark.parametrize('wrong_analytics', [False, True])
@pytest.mark.parametrize('status', ['success', 'empty', 'suppressed', 'error', 'timeout', 'cancelled'])
def test_native_result_and_callback_outcomes(status, wrong_analytics):
    async def scenario():
        observed = {}

        def operation() -> dict:
            """Fake normalized terminal result, including simulated cancellation."""
            observed['workflow'] = status
            return {'outcome': status}

        def callback(tool, args, tool_context, tool_response):
            observed['callback'] = tool_response['outcome']
            # Deliberately faulty host analytics, not a mock of the probe.
            observed['analytics'] = 'success' if wrong_analytics else tool_response['outcome']

        agent = LlmAgent(name='probe', tools=[operation], after_tool_callback=callback,
                         model=Script(script=[call('operation'), [types.Part(text='done')]]))
        events, _ = await turns(agent, ['run'])
        for event in events:
            if event.content:
                for part in event.content.parts or []:
                    if part.function_response:
                        observed['completion'] = part.function_response.response['outcome']
        assert outcome_consistency(observed, expected=status,
            observers=['workflow', 'callback', 'analytics', 'completion']) is (
                not wrong_analytics or status == 'success')
    asyncio.run(scenario())


@pytest.mark.parametrize('late_handoff', [False, True])
def test_handoff_is_observed_at_discovery_not_just_final_prompt(late_handoff):
    async def scenario():
        snapshots, expected = [], []

        def search() -> dict:
            """Synthetic search declaration."""
            return {}

        class Discovery(BaseToolset):
            async def get_tools(self, readonly_context=None):
                context = readonly_context
                text = ' '.join(p.text for p in context.user_content.parts if p.text)
                used = text == 'yes' and bool(context.state.get('handoff'))
                query = context.state['handoff'] if used else text
                tools = [FunctionTool(search)] if query == 'device' else []
                observation = {'session': context.session.id, 'invocation': context.invocation_id,
                    'query_digest': hashlib.sha256(query.encode()).hexdigest(),
                    'tools': [t.name for t in tools], 'handoff_used': used}
                snapshots.append(observation)
                expected.append({**observation,
                    'query_digest': hashlib.sha256(b'device').hexdigest(),
                    'tools': ['search'], 'handoff_used': True})
                return tools

            async def close(self):
                pass

        def begin(callback_context):
            if not late_handoff:
                callback_context.state['handoff'] = 'device'

        def before_model(callback_context, llm_request):
            # This can make the final prompt right while discovery was wrong.
            callback_context.state['handoff'] = 'device'
            llm_request.append_instructions(['Prior greeting: device'])
            # Capture real assembled request bytes without a tokenizer dependency.
            partitions = {'system': json.dumps(llm_request.config.system_instruction),
                          'tools': json.dumps([t.name for t in llm_request.tools_dict.values()]),
                          'history': json.dumps([c.model_dump(mode='json') for c in llm_request.contents]),
                          'other': ''}
            counts = {key: len(value.encode()) for key, value in partitions.items()}
            counts['total'] = sum(counts.values())
            # Deliberate fixture byte-based proxy, clearly not provider tokens.
            proxy = {key: value // 4 for key, value in counts.items()}
            validate([{'request': 1, 'phase': 'followup', 'boundary': 'adk_request',
                       'tokenizer_sha256': hashlib.sha256(b'fixture-bytes-div-four').hexdigest(),
                       'bytes': counts, 'proxy_tokens': proxy}])

        agent = LlmAgent(name='probe', tools=[Discovery()], before_agent_callback=begin,
            before_model_callback=before_model, model=Script(script=[[types.Part(text='done')]]))
        await turns(agent, ['yes'])
        assert snapshots
        assert handoff_continuity(observed=snapshots[0], expected=expected[0]) is (not late_handoff)
    asyncio.run(scenario())
