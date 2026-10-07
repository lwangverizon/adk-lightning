"""Maintained examples execute SDK staging and A2UI without customer code."""
import pathlib
import unittest

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from adkl.chat import ChatSession
from adkl.host import HostChatSession

PROJECTS = pathlib.Path(__file__).resolve().parents[2] / "projects"


class StepsModel(BaseLlm):
    model: str = "synthetic"

    async def generate_content_async(self, request, stream=False):
        last = request.contents[-1]
        if any(p.function_response for p in last.parts or []):
            part = types.Part(text="Here are the steps.")
        elif "thanks" in " ".join(p.text or "" for p in last.parts or []):
            part = types.Part(text="You're welcome.")
        else:
            part = types.Part(function_call=types.FunctionCall(
                name="render_steps", args={"steps": ["Gather your notes.", "Summarize the key points."]}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class TestMinimalExamples(unittest.IsolatedAsyncioTestCase):
    async def test_pack_stages_emits_and_clears_on_followup(self):
        session = ChatSession(PROJECTS / "staged-ui", env={}, model=StepsModel())
        try:
            result = await session.send("Show steps")
            self.assertIsNone(result["trace"]["error"], result)
            self.assertEqual(result["trace"]["modelCalls"], 2)
            widgets = result["trace"]["widgets"]
            self.assertEqual(len(widgets), 1)
            self.assertEqual(widgets[0]["type"], "step")
            self.assertEqual(widgets[0]["a2ui"]["warnings"], [])
            self.assertTrue(widgets[0]["a2ui"]["root"])
            followup = await session.send("thanks")
            self.assertEqual(followup["trace"]["widgets"], [])
        finally:
            if session._runner:
                await session._runner.close()

    async def test_host_factory_inspection_staging_both_versions_and_clear(self):
        session = HostChatSession(PROJECTS / "native-agent", env={})
        try:
            report = session.inspect()
            self.assertEqual(report["source"], "host_factory")
            self.assertEqual(report["agent"]["model"], "offline-demo")
            for version in ("v0.9", "v1.0"):
                result = await session.send("Show " + version)
                self.assertIsNone(result["trace"]["error"], result)
                widgets = result["trace"]["widgets"]
                self.assertEqual(len(widgets), 1)
                view = widgets[0]["a2ui"]
                self.assertEqual(view["version"], version)
                self.assertEqual(view["warnings"], [])
                self.assertEqual(view["root"], "root")
                self.assertIn("Staged UI demo", str(view))
            result = await session.send("thanks")
            self.assertEqual(result["trace"]["widgets"], [])
            result = await session.send("Show shared pack")
            self.assertEqual(result["trace"]["widgets"][0]["type"], "step")
            self.assertEqual(result["trace"]["widgets"][0]["a2ui"]["warnings"], [])
            self.assertTrue(result["trace"]["widgetGates"])
            self.assertTrue(session.inspect()["lastRequest"])
            old = session._session_id
            session.clear()
            self.assertIsNone(session.inspect()["lastRequest"])
            await session.send("Show v0.9")
            self.assertNotEqual(old, session._session_id)
        finally:
            if session._runner:
                await session._runner.close()
