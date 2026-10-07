"""Observe actual ADK tool/callback boundaries; synthetic model, no external I/O."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adkl.contract_probes import capability_reachability, delivery_consistency, result_integrity
from examples.incident_contracts import consume_result
from tests.support.native import run


class CallModel(BaseLlm):
    model: str = 'fixture'
    called: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        if not self.called:
            self.called = True
            part = types.Part(function_call=types.FunctionCall(name='fetch', args={}, id='f'))
        else:
            part = types.Part(text='Done')
        yield LlmResponse(content=types.Content(role='model', parts=[part]))


@pytest.mark.parametrize('faulty', [False, True])
def test_real_tool_response_to_consumer(faulty):
    async def scenario():
        observations = []

        def fetch() -> dict:
            """Return a synthetic successful envelope with metadata."""
            return {'items': ['item'], 'metadata': 'guidance'}

        def consume(tool, args, tool_context, tool_response):
            observations.append(consume_result(tool_response, faulty=faulty))

        agent = LlmAgent(name='probe', model=CallModel(), tools=[fetch], after_tool_callback=consume)
        await run(agent)
        assert len(observations) == 1
        assert result_integrity(observed=observations[0], expected={
            'status': 'present', 'cached': True, 'fallback': False}) is (not faulty)
    asyncio.run(scenario())


@pytest.mark.parametrize('faulty', [False, True])
def test_actual_outgoing_declaration_and_dispatch_inventory(faulty):
    async def scenario():
        observations = []

        def fetch() -> dict:
            """Synthetic declaration."""
            return {}

        def boundary(callback_context, llm_request):
            declarations = [declaration.name for tool in llm_request.config.tools or []
                            for declaration in tool.function_declarations or []]
            observations.append(capability_reachability(
                catalog=['fetch'], indexed=['fetch'], exposed=declarations,
                dispatchable=list(llm_request.tools_dict), required=['fetch'],
                references={'old_owner': 'old_owner' if faulty else 'fetch'}))

        agent = LlmAgent(name='probe', model=CallModel(), tools=[fetch], before_model_callback=boundary)
        await run(agent)
        assert observations and all(value is (not faulty) for value in observations)
    asyncio.run(scenario())


@pytest.mark.parametrize('faulty', [False, True])
@pytest.mark.parametrize('sink_ok', [False, True])
def test_completion_does_not_claim_failed_sink_delivery(faulty, sink_ok):
    async def scenario():
        staged, accepted, claimed = [], [], []

        def fetch() -> dict:
            """Stage a synthetic card; do not treat staging as delivery."""
            staged.append('card')
            return {'staged': True}

        def completion(callback_context):
            if sink_ok:
                accepted.extend(staged)
            if faulty:
                claimed.extend(staged)
            else:
                claimed.extend(accepted)

        agent = LlmAgent(name='probe', model=CallModel(), tools=[fetch],
                         after_agent_callback=completion)
        await run(agent)
        assert delivery_consistency(staged=staged, accepted=accepted, claimed=claimed,
            expected=['card'] if sink_ok else []) is (not faulty or sink_ok)
    asyncio.run(scenario())
