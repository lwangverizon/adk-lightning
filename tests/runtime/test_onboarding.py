import pathlib
import tempfile
import unittest
from unittest.mock import patch

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adkl import projects
from adkl.chat import ChatSession
from adkl.host import HostChatSession


class OnboardingModel(BaseLlm):
    model: str = "fixture"
    async def generate_content_async(self, request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Your notes are ready.")]))


class TestOnboarding(unittest.IsolatedAsyncioTestCase):
    async def test_new_agent_runs_through_shared_runtime_without_a_customer_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = projects.create(directory, "notes_agent", "Help organize notes supplied by the user.")
            session = ChatSession(root, env={}, model=OnboardingModel())
            self.assertEqual(session.preflight(), [])
            result = await session.send("Organize my notes.")
            self.assertIsNone(result["trace"]["error"], result)
            self.assertEqual(result["reply"], "Your notes are ready.")
            self.assertGreater(result["trace"]["modelCalls"], 0)

    async def test_host_factory_keeps_native_callbacks_and_tools_in_inspection_and_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            projects.attach(root, "existing_agent:build")
            called = []
            async def before_agent(callback_context):
                called.append(callback_context.agent_name)
            def lookup_notes(query: str) -> str:
                """Look up notes supplied by the user."""
                return query
            def factory():
                return LlmAgent(name="existing", model=OnboardingModel(), tools=[lookup_notes],
                                instruction="Answer using supplied notes.", before_agent_callback=before_agent)
            from types import SimpleNamespace
            with patch.dict("sys.modules", {"existing_agent": SimpleNamespace(build=factory)}):
                session = HostChatSession(root, env={})
                report = session.inspect()
                self.assertEqual(report["agent"]["tools"], ["lookup_notes"])
                self.assertEqual(report["agent"]["callbacks"]["before_agent_callback"], 1)
                result = await session.send("Use my notes.")
                self.assertIsNone(result["trace"]["error"], result)
                self.assertEqual(result["reply"], "Your notes are ready.")
                self.assertEqual(called, ["existing"])
