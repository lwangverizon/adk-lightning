"""Native ADK callback, stream and session contracts; synthetic model, no network."""
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adkl import chat, execution, features, output_filters, runtime, widgets


class SequenceModel(BaseLlm):
    model: str = 'offline'
    calls: int = 0
    responses: list = []
    requests: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.model_copy(deep=True))
        batch = self.responses[self.calls]
        self.calls += 1
        for response in batch:
            yield response


def response(text=None, partial=False, call=None):
    part = types.Part(text=text) if text is not None else types.Part(function_call=call)
    return LlmResponse(content=types.Content(role='model', parts=[part]), partial=partial)


class TestNativeStream(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_host_margin_does_not_lose_completed_reply(self):
        def diagnostics(callback_context):
            callback_context.state['adkl:last_turn:offline'] = {
                'margin': None, 'picked': 'public-qa', 'model_calls': 1}

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'prompt.md').write_text('Test only.')
            (root / 'agent.yaml').write_text(yaml.safe_dump({
                'agent': {'name': 'offline', 'provider': 'gemini', 'model': 'offline',
                          'instruction': {'template': 'prompt.md'}},
                'features': {'pipeline': []}}))
            session = chat.ChatSession(root, env={'GOOGLE_API_KEY': 'synthetic'})
            model = SequenceModel(responses=[[response('Grounded fee answer.')]])
            with patch.object(chat.gw, 'build_model', return_value=model):
                session._build()
            session._agent.after_agent_callback = diagnostics
            frames = [event async for event in session.stream('upgrade fee')]
            self.assertEqual(frames[-1]['type'], 'done')
            self.assertEqual(frames[-1]['result']['reply'], 'Grounded fee answer.')
            self.assertIsNone(frames[-1]['result']['trace']['margin'])
            self.assertEqual(session.history[-1]['text'], 'Grounded fee answer.')

    async def test_multiple_model_calls_have_one_filtered_reply_and_history(self):
        model = SequenceModel(responses=[
            [response('call 555-', True), response('123-4567 ', True),
             LlmResponse(content=types.Content(role='model', parts=[
                 types.Part(text='call 555-'), types.Part(text='123-4567 '),
                 types.Part(function_call=types.FunctionCall(name='lookup', args={}))]))],
            [response('```json\n{"hidden": true}\n```', True),
             response('```json\n{"hidden": true}\n```\nComplete.')],
        ])
        def lookup() -> dict:
            """Return synthetic data."""
            return {'status': 'ok'}

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'prompt.md').write_text('Test only.')
            (root / 'agent.yaml').write_text(yaml.safe_dump({
                'agent': {'name': 'offline', 'provider': 'gemini', 'model': 'offline',
                          'instruction': {'template': 'prompt.md'}},
                'features': {'pipeline': ['output_cleanup', 'pii_redaction']}}))
            session = chat.ChatSession(root, env={'GOOGLE_API_KEY': 'synthetic'})
            with patch.object(chat.gw, 'build_model', return_value=model):
                session._build()
            session._agent.tools = [lookup]
            frames = [event async for event in session.stream('hello')]
            result = frames[-1]['result']
            self.assertIsNone(result['trace']['error'], result)
            self.assertEqual(result['reply'], 'call [phone] \nComplete.')
            text_frames = [e['text'] for e in frames if e['type'] == 'text']
            self.assertEqual(text_frames, [result['reply']])
            self.assertEqual(session.history[-1]['text'], result['reply'])
            self.assertNotIn('555-', str(frames))
            self.assertNotIn('hidden', str(frames))
            self.assertEqual(model.calls, 2)
            native = await session._sessions.get_session(app_name='offline', user_id='console', session_id=session._session_id)
            visible = ''.join(p.text or '' for e in native.events for p in (e.content.parts if e.content else []) if not p.thought)
            self.assertNotIn('555-', visible)
            self.assertNotIn('hidden', visible)

    async def test_partial_only_stream_fails_and_disabled_filters_preserve_text(self):
        class Events:
            def __init__(self, complete):
                self.complete = complete

            async def run_async(self, **kwargs):
                yield SimpleNamespace(partial=True, content=types.Content(parts=[types.Part(text="555-")]))
                if self.complete:
                    yield SimpleNamespace(partial=False, content=types.Content(parts=[types.Part(text="555-123-4567")]))
                # A state-only event cannot turn an incomplete model response into a final one.
                yield SimpleNamespace(partial=False, content=None)

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "prompt.md").write_text("Offline test")
            (root / "agent.yaml").write_text(yaml.safe_dump({"agent": {
                "name": "offline", "model": "offline", "provider": "gemini",
                "instruction": {"template": "prompt.md"}}, "features": {"pipeline": []}}))
            for complete in (False, True):
                session = chat.ChatSession(root, env={"GOOGLE_API_KEY": "synthetic"})
                with patch.object(chat.gw, "build_model", return_value=SequenceModel()):
                    session._build()
                session._runner = Events(complete)
                frames = [e async for e in session.stream("hello")]
                result = frames[-1]["result"]
                if complete:
                    self.assertEqual(result["reply"], "555-123-4567")
                else:
                    self.assertIsNone(result["reply"])
                    self.assertIn("completed response", result["trace"]["error"])
                    self.assertFalse(any(e["type"] == "text" for e in frames))

    async def test_short_circuit_runs_without_model_and_preserves_widget(self):
        model = SequenceModel(responses=[])
        hooks, _ = runtime.build_callbacks(['short_circuit'], {'short_circuit': {'phrases': {
            'hello': {'reply': 'Hello!', 'widget_type': 'Card', 'payload': {'title': 'Welcome'}}}}})
        agent = LlmAgent(name='offline', model=model, **hooks)
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name='offline', user_id='test')
        runner = Runner(app=App(name='offline', root_agent=agent), session_service=sessions)
        events = [e async for e in runner.run_async(user_id='test', session_id=session.id,
                    new_message=types.Content(role='user', parts=[types.Part(text=' HELLO ')]))]
        self.assertEqual(model.calls, 0)
        self.assertTrue(any(p.text == 'Hello!' for e in events if e.content for p in e.content.parts))
        rendered = [w for e in events for w in (e.actions.render_ui_widgets or [])]
        self.assertEqual(rendered[0].payload['title'], 'Welcome')
        callback = hooks['before_agent_callback'][0]
        no_match = SimpleNamespace(user_content=types.Content(parts=[types.Part(text='hello world')]))
        self.assertIsNone(await callback(no_match))

    async def test_native_tool_chain_marks_trims_and_injects_contract(self):
        async def build(ctx, items, request_id):
            return []
        spec = widgets.WidgetSpec(key='card', widget_id='card', payload_type='Card',
            build=build, role=widgets.Role.DATA_PRIMARY)
        captured = []
        def lookup(tool_context) -> dict:
            """Stage synthetic results."""
            tool_context.state[spec.state_items] = [{'id': 1}]
            tool_context.state[spec.state_dirty] = True
            return {'status': 'ok', 'results': ['x' * 100 for _ in range(100)]}
        def observe(tool, args, tool_context, tool_response):
            captured.append((dict(tool_response), tool_context.state.get('temp:public_help_attempt')))
        config = features.resolve({'pipeline': ['progressive_auth', 'staged_widgets', 'tool_result_trimming'],
            'progressive_auth': {'tools': ['lookup']}, 'tool_result_trimming': {'max_chars': 400}})
        hooks, _ = runtime.build_callbacks(config.active, config.config, widget_specs=[spec])
        # Observe the transformed result within the same composed native callback.
        hooks['after_tool_callback'] = [runtime.compose_after_tool(hooks['after_tool_callback'] + [observe])]
        model = SequenceModel(responses=[[response(call=types.FunctionCall(name='lookup', args={}))], [response('Done')]])
        agent = LlmAgent(name='offline', model=model, tools=[lookup], **hooks)
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name='offline', user_id='test')
        runner = Runner(app=App(name='offline', root_agent=agent), session_service=sessions)
        _ = [e async for e in runner.run_async(user_id='test', session_id=session.id,
                new_message=types.Content(role='user', parts=[types.Part(text='go')]))]
        result, marker = captured[0]
        self.assertEqual(marker['source'], 'lookup')
        self.assertIn('_adkl_trimmed', result)
        self.assertIn('agent_instruction', result)
        self.assertNotIn('results', result)
        self.assertTrue(result['preview'])
        # Ensure ADK handed the replacement to the following model call.
        responses = [p.function_response.response for c in model.requests[-1].contents for p in c.parts if p.function_response]
        self.assertEqual(responses[0], result)

    async def test_context_false_zero_empty_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = pathlib.Path(tmp)
            (root / 'context.txt').write_text('local data')
            callback = runtime.make_state_injector({'zero': {'from_state': 'n'}, 'false': {'from_state': 'f'},
                'empty': {'from_state': 'e'}, 'file': {'from_file': 'context.txt'}}, root)
            request = LlmRequest()
            callback(SimpleNamespace(state={'n': 0, 'f': False, 'e': ''}), request)
            content = str(request.config.system_instruction)
            self.assertIn('"zero": 0', content)
            self.assertIn('"false": false', content)
            self.assertIn('"empty": ""', content)
            self.assertIn('local data', content)
            (root / 'escape').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(execution.ExecutionError):
                runtime.make_state_injector({'file': {'from_file': 'escape/secret'}}, root)

    async def test_composition_propagates_failure_without_later_transforms(self):
        seen = []
        def broken(**kwargs):
            raise ValueError('fixture failure')
        def later(**kwargs):
            seen.append(True)
        callback = runtime.compose_after_tool([broken, later])
        with self.assertRaises(ValueError):
            await callback(None, {}, None, {'private': 'data'})
        self.assertEqual(seen, [])

    async def test_output_filter_handles_identifier_across_native_parts(self):
        value = LlmResponse(content=types.Content(parts=[types.Part(text='555-'), types.Part(text='123-4567')]))
        output_filters.make_output_filter(strip=False)(llm_response=value)
        self.assertEqual(''.join(p.text for p in value.content.parts), '[phone]')
