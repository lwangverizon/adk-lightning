"""The incremental adapter and YAML factory execute the same shared patterns."""
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import yaml
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from adkl.adapters import build_callback_adapter
from adkl.agent_runtime import build_agent


class Model(BaseLlm):
    model: str = "offline"
    requests: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.model_copy(deep=True))
        if len(self.requests) == 1:
            part = types.Part(function_call=types.FunctionCall(name="lookup", args={}))
        else:
            part = types.Part(text="Done")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def lookup(tool_context: ToolContext) -> dict:
    """Return synthetic inventory and retain the complete state source."""
    result = {"rows": [{"id": "one", "bulk": "retained only in source"}], "status": "ok"}
    tool_context.state["full_result"] = result
    return result


class TestCallbackAdoption(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_and_factory_preserve_raw_state_and_project_model_result(self):
        config = {"pipeline": ["history_slicing", "tool_history_repair", "tool_result_projection"],
                  "history_slicing": {"recent_turns": 1},
                  "tool_result_projection": {"tools": {
                      "lookup": {"path": ["rows"], "fields": ["id"]}}}}
        for entry in ("adapter", "factory"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as directory:
                model = Model()
                if entry == "adapter":
                    agent = LlmAgent(name="fixture", model=model, tools=[lookup],
                                     **build_callback_adapter(config))
                else:
                    root = pathlib.Path(directory)
                    (root / "prompt.md").write_text("Synthetic inventory.", encoding="utf-8")
                    (root / "agent.yaml").write_text(yaml.safe_dump({
                        "agent": {"name": "fixture", "provider": "gemini", "model": "offline",
                                  "instruction": {"template": "prompt.md"}},
                        "tools": {"core": ["lookup"]}, "features": config}), encoding="utf-8")
                    with patch("adkl.agent_runtime._load_tool_fns", return_value=({"lookup": lookup}, [])):
                        agent = build_agent(root, env={}, model=model)
                sessions = InMemorySessionService()
                session = await sessions.create_session(app_name="fixture", user_id="user")
                runner = Runner(agent=agent, app_name="fixture", session_service=sessions)
                try:
                    events = [e async for e in runner.run_async(user_id="user", session_id=session.id,
                              new_message=types.Content(role="user", parts=[types.Part(text="inventory")]))]
                    self.assertTrue(events)
                    self.assertEqual(len(model.requests), 2)
                    results = [p.function_response.response for c in model.requests[-1].contents
                               for p in c.parts if p.function_response and p.function_response.name == "lookup"]
                    self.assertEqual(results, [{"rows": [{"id": "one"}], "status": "ok"}])
                    persisted = await sessions.get_session(app_name="fixture", user_id="user", session_id=session.id)
                    self.assertEqual(persisted.state["full_result"]["rows"][0]["bulk"], "retained only in source")
                finally:
                    await runner.close()

    async def test_native_refusal_still_stops_tool_execution(self):
        calls = []
        def lookup() -> dict:
            """Must not execute in this test."""
            calls.append(True)
            return {"status": "unexpected"}
        def guard(tool, args, tool_context):
            return {"status": "refused"}
        agent = LlmAgent(name="fixture", model=Model(), tools=[lookup],
                         **build_callback_adapter({"pipeline": []}, before_tool=[guard]))
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="fixture", user_id="user")
        runner = Runner(agent=agent, app_name="fixture", session_service=sessions)
        try:
            _ = [e async for e in runner.run_async(user_id="user", session_id=session.id,
                 new_message=types.Content(role="user", parts=[types.Part(text="lookup")]))]
            self.assertEqual(calls, [])
        finally:
            await runner.close()
