"""Shared presentation packs: native delivery and invocation isolation."""
from __future__ import annotations

import pathlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.adk.tools import ToolContext

from adkl import chat


class _Embedder:
    async def embed(self, _text):
        return [1.0, 0.0]


class _Model(BaseLlm):
    model: str = "offline"
    calls: int = 0

    async def generate_content_async(self, _request, stream=False):
        calls = [
            [types.Part(function_call=types.FunctionCall(
                name="render_steps", args={"steps": ["Draw 3 groups.", "Count 12."]}))],
            [types.Part(text="Let’s work through this together.")],
            [types.Part(text="No new card this turn.")],
        ]
        parts = calls[self.calls]
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=parts))


class TestPresentation(unittest.IsolatedAsyncioTestCase):
    async def test_bound_result_sanitizes_custom_field_and_rejects_wrong_kind(self):
        from adkl.presentation_runtime import BoundResultTool
        from adkl.presentation import QUEUE

        def question(tool_context: ToolContext, valid: bool) -> dict:
            """Return synthetic domain data without network or persistence."""
            return {"status": "success", "display": {
                "type": "quiz" if valid else "fact", "question": "2+3?", "answer": "B",
                "options": [{"label": "A", "text": "4"}, {"label": "B", "text": "5"}]}}

        tool = BoundResultTool(question, {"kind": "quiz", "result_field": "display"}, {"quiz"})
        ctx = SimpleNamespace(state={}, function_call_id="a",
                              actions=SimpleNamespace(skip_summarization=False))
        bad = await tool.run_async(args={"valid": False}, tool_context=ctx)
        self.assertEqual(bad["status"], "error")
        self.assertNotIn(QUEUE, ctx.state)
        good = await tool.run_async(args={"valid": True}, tool_context=ctx)
        self.assertEqual(good["status"], "staged")
        self.assertNotIn("answer", good["display"])
        self.assertEqual(good["card"], good["display"])

    async def test_agent_level_presentation_tool_stages_and_emits_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "prompt.md").write_text("Use tools when useful.")
            for name in ("math", "history"):
                directory = root / "skills" / name
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: Help with {name}.\n---\nTeach {name}.\n")
            (root / "vectors.json").write_text(json.dumps({
                "math": {"embedding": [1.0, 0.0], "description": "Math"},
                "history": {"embedding": [0.0, 1.0], "description": "History"}}))
            (root / "agent.yaml").write_text(yaml.safe_dump({
                "agent": {"name": "offline", "provider": "gemini", "model": "offline",
                          "instruction": {"template": "prompt.md"}},
                "features": {"staged_widgets": {"inject_contract": True},
                             "skill_retrieval": {"vectors": "vectors.json", "top_k": 1,
                                                 "min_skills_for_retrieval": 0,
                                                 "total_cap": 1, "bm25": False},
                             "pipeline": ["skill_retrieval", "staged_widgets"]},
                "skills": {"dir": "skills", "include": ["math", "history"]},
                "presentation": {"version": 1, "packs": ["adkl/tutoring@1"]},
            }))
            model = _Model()
            session = chat.ChatSession(root, env={"GOOGLE_API_KEY": "synthetic"})
            with (patch.object(chat.gw, "build_model", return_value=model),
                  patch.object(chat.gw, "GatewayEmbedder", return_value=_Embedder())):
                session._build()
            frames = [event async for event in session.stream("help me")]
            result = frames[-1]["result"]
            self.assertIsNone(result["trace"]["error"], result)
            self.assertEqual(result["reply"], "Let’s work through this together.")
            self.assertEqual(result["trace"]["picked"], "math")
            widgets = result["trace"]["widgets"]
            self.assertEqual(len(widgets), 1)
            self.assertEqual(widgets[0]["type"], "step")
            self.assertEqual(widgets[0]["a2ui"]["warnings"], [])
            self.assertTrue(widgets[0]["a2ui"]["root"])
            self.assertEqual(len([g for g in result["trace"]["widgetGates"] if g["passed"]]), 1)

            # The turn-scoped queue cannot leak a previous surface into the next turn.
            frames = [event async for event in session.stream("thanks")]
            result = frames[-1]["result"]
            self.assertIsNone(result["trace"]["error"], result)
            self.assertEqual(result["trace"]["widgets"], [])
            self.assertEqual(result["reply"], "No new card this turn.")


if __name__ == "__main__":
    unittest.main()

class EmptyCompanionModel(BaseLlm):
    model: str = "empty-companion"
    calls: int = 0

    async def generate_content_async(self, request, stream=False):
        parts = ([types.Part(function_call=types.FunctionCall(
            name="render_steps", args={"steps": ["Draw groups.", "Count them."]}))]
            if self.calls == 0 else [])
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=parts))


class TestSdkCompanion(unittest.IsolatedAsyncioTestCase):
    async def test_native_runner_emits_companion_without_console_adapter(self):
        from adkl.agent_runtime import build_agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "prompt.md").write_text("Help the learner.")
            (root / "agent.yaml").write_text(yaml.safe_dump({
                "agent": {"name": "fixture", "provider": "gemini", "model": "offline",
                          "instruction": {"template": "prompt.md"}},
                "features": {"pipeline": ["staged_widgets"]},
                "presentation": {"version": 1, "packs": ["adkl/common@1"]}}))
            agent = build_agent(root, env={}, model=EmptyCompanionModel())
            sessions = InMemorySessionService()
            session = await sessions.create_session(app_name="fixture", user_id="learner")
            runner = Runner(agent=agent, app_name="fixture", session_service=sessions)
            events = [e async for e in runner.run_async(user_id="learner", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="help")]))]
            self.assertTrue(any(e.actions.render_ui_widgets for e in events))
            self.assertTrue(any(p.text and p.text.strip() for e in events
                                for p in (getattr(e.content, "parts", None) or [])))
            await runner.close()
