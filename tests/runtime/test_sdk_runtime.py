"""Same native Runner contracts through SDK and preview, without providers."""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import yaml
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adkl.agent_runtime import build_agent
from adkl.chat import ChatSession
from adkl.retrieval import ACTIVATED_KEY


class Embedder:
    async def embed(self, text):
        if text == "outage":
            raise OSError("synthetic outage")
        if text == "ambiguous":
            return [1, .99]
        return [0, 1] if text == "history" else [1, 0]


class Model(BaseLlm):
    model: str = "fixture"
    requests: list = []

    async def generate_content_async(self, request, stream=False):
        prompt = str(request.config.system_instruction)
        self.requests.append({"prompt": prompt, "contents": list(request.contents),
                              "tools": list(request.tools_dict)})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Done.")]))


class TestSdkRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "prompt.md").write_text("Help the learner.")
        for name in ("math", "history"):
            directory = self.root / "skills" / name
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Teach {name}.\n---\nUNIQUE_{name}_PROCEDURE\n")
        (self.root / "vectors.json").write_text(json.dumps({
            "math": {"embedding": [1, 0]}, "history": {"embedding": [0, 1]}}))
        self.doc = {
            "agent": {"name": "fixture", "provider": "gemini", "model": "fixture",
                      "instruction": {"template": "prompt.md"}},
            "skills": {"include": ["math", "history"]},
            "features": {
                "skill_retrieval": {"vectors": "vectors.json", "top_k": 1, "total_cap": 1,
                                    "min_skills_for_retrieval": 0,
                                    "bm25": False, "auto_activate": True,
                                    "auto_activate_min_score": .9},
                "history_slicing": {"recent_turns": 1},
                "skill_state_injection": {"skills": {
                    "math": {"topic": {"from_state": "topic", "type": "string", "required": True}}}},
                "pipeline": ["skill_retrieval", "history_slicing", "skill_state_injection"],
            },
        }

    def save(self):
        (self.root / "agent.yaml").write_text(yaml.safe_dump(self.doc))

    async def sdk(self, state=None):
        self.save()
        model = Model()
        agent = build_agent(self.root, env={}, model=model, embedder=Embedder())
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="fixture", user_id="student", state=state or {})
        return model, agent, sessions, session, Runner(agent=agent, app_name="fixture", session_service=sessions)

    async def run_turn(self, runner, session, text):
        return [e async for e in runner.run_async(user_id="student", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=text)]))]

    async def test_sdk_and_preview_share_activation_context_and_model_call_count(self):
        model, agent, sessions, session, runner = await self.sdk({"topic": "fractions"})
        await self.run_turn(runner, session, "math")
        self.assertIn("UNIQUE_math_PROCEDURE", model.requests[0]["prompt"])
        self.assertIn('"topic": "fractions"', model.requests[0]["prompt"])
        current = await sessions.get_session(app_name="fixture", user_id="student", session_id=session.id)
        self.assertEqual(current.state[ACTIVATED_KEY.format(agent="fixture")], ["math"])
        self.assertEqual(current.state["adkl:last_turn"]["model_calls"], 1)
        preview_model = Model()
        preview = ChatSession(self.root, env={}, model=preview_model, embedder=Embedder())
        result = await preview.send("math", state={"topic": "fractions"})
        self.assertIsNone(result["trace"]["error"], result)
        self.assertEqual(result["trace"]["modelCalls"], 1)
        self.assertEqual(result["trace"]["activatedSkills"], ["math"])
        self.assertEqual(model.requests[0]["prompt"], preview_model.requests[0]["prompt"])
        await self.run_turn(runner, session, "history")
        self.assertNotIn('"topic": "fractions"', model.requests[-1]["prompt"])
        self.assertNotIn("UNIQUE_math_PROCEDURE", model.requests[-1]["prompt"])
        self.assertIn("UNIQUE_history_PROCEDURE", model.requests[-1]["prompt"])
        current = await sessions.get_session(app_name="fixture", user_id="student", session_id=session.id)
        self.assertEqual(current.state[ACTIVATED_KEY.format(agent="fixture")], ["history"])
        self.assertGreater(len(current.events), len(model.requests[-1]["contents"]))
        await runner.close()

    async def test_pins_read_committed_state_and_expire_when_flow_clears(self):
        self.doc["features"]["skill_retrieval"]["state_pins"] = [
            {"skill": "math", "state_key": "flow", "active_when": "truthy"}]
        model, agent, sessions, session, runner = await self.sdk({"topic": "fractions", "flow": True})
        await self.run_turn(runner, session, "math")
        await self.run_turn(runner, session, "history")
        self.assertIn('"topic": "fractions"', model.requests[-1]["prompt"])
        from google.adk.events import Event, EventActions
        current = await sessions.get_session(app_name="fixture", user_id="student", session_id=session.id)
        await sessions.append_event(current, Event(author="host", actions=EventActions(state_delta={"flow": False})))
        await self.run_turn(runner, session, "history")
        self.assertNotIn('"topic": "fractions"', model.requests[-1]["prompt"])
        await runner.close()

    async def test_ambiguous_and_outage_do_not_auto_activate(self):
        # A one-candidate cap must not hide the runner-up when computing margin.
        self.doc["features"]["skill_retrieval"]["auto_activate_min_score"] = .5
        model, agent, sessions, session, runner = await self.sdk()
        await self.run_turn(runner, session, "ambiguous")
        self.assertNotIn("UNIQUE_math_PROCEDURE", model.requests[-1]["prompt"])
        await self.run_turn(runner, session, "outage")
        current = await sessions.get_session(app_name="fixture", user_id="student", session_id=session.id)
        self.assertTrue(current.state["adkl:last_turn"]["fallback"])
        self.assertIsNone(current.state["adkl:last_turn"]["picked"])
        self.assertEqual(current.state["adkl:last_turn"]["activated"], [])
        await runner.close()

    async def test_required_context_and_type_fail_before_model(self):
        for state in ({}, {"topic": 123}):
            model, agent, sessions, session, runner = await self.sdk(state)
            with self.assertRaisesRegex(ValueError, "skill context"):
                await self.run_turn(runner, session, "math")
            self.assertEqual(model.requests, [])
            await runner.close()

    async def test_disabled_auto_activation_keeps_loading_model_driven(self):
        self.doc["features"]["skill_retrieval"]["auto_activate"] = False
        model, agent, sessions, session, runner = await self.sdk()
        await self.run_turn(runner, session, "math")
        self.assertNotIn("UNIQUE_math_PROCEDURE", model.requests[0]["prompt"])
        self.assertIn("load_skill", model.requests[0]["tools"])
        await runner.close()

    async def test_short_circuit_resets_previous_turn_diagnostics(self):
        model, agent, sessions, session, runner = await self.sdk({"topic": "fractions"})
        def shortcut(callback_context):
            if callback_context.user_content.parts[0].text == "stop":
                return types.Content(role="model", parts=[types.Part(text="Stopped.")])
        agent.before_agent_callback.insert(1, shortcut)
        await self.run_turn(runner, session, "math")
        await self.run_turn(runner, session, "stop")
        current = await sessions.get_session(app_name="fixture", user_id="student", session_id=session.id)
        self.assertEqual(current.state["adkl:last_turn"]["model_calls"], 0)
        self.assertEqual(current.state["adkl:last_turn"]["matches"], [])
        await runner.close()

    async def test_shared_agent_does_not_leak_context_across_sessions(self):
        model, agent, sessions, session, runner = await self.sdk({"topic": "private fractions"})
        await self.run_turn(runner, session, "math")
        second = await sessions.create_session(app_name="fixture", user_id="student", state={})
        await self.run_turn(runner, second, "history")
        self.assertNotIn("private fractions", model.requests[-1]["prompt"])
        self.assertEqual(len(agent.tools), 1)
        await runner.close()

class FollowupModel(BaseLlm):
    model: str = "followup"
    calls: int = 0
    observed_tools: list = []

    async def generate_content_async(self, request, stream=False):
        self.observed_tools.append(list(request.tools_dict))
        if self.calls == 0:
            part = types.Part(text="Here is your lesson.")
        elif self.calls == 1:
            part = types.Part(function_call=types.FunctionCall(name="quiz_for_lesson", args={}))
        else:
            part = types.Part(text="Here is your quiz.")
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class TestContinuation(unittest.IsolatedAsyncioTestCase):
    setUp = TestSdkRuntime.setUp
    save = TestSdkRuntime.save

    async def test_ambiguous_followup_preserves_previously_loaded_domain_tool(self):
        # First activate history. The next query's cap excludes it from raw
        # retrieval. The model still calls its quiz tool from the prior lesson.
        self.doc["features"]["skill_retrieval"]["continue_active"] = True
        self.doc["features"]["skill_state_injection"]["skills"] = {}
        skill = self.root / "skills/history/SKILL.md"
        skill.write_text(skill.read_text().replace('description: Teach history.',
            'description: Teach history.\nmetadata:\n  adk_additional_tools: [quiz_for_lesson]'))
        self.doc["tools"] = {"catalog": "catalog.json", "repo": "."}
        (self.root / "catalog.json").write_text(json.dumps({"tools": [
            {"name": "quiz_for_lesson", "module": "continuation_fixture.py"}]}))
        (self.root / "continuation_fixture.py").write_text(
            'def quiz_for_lesson() -> dict:\n    """Return a fixture question."""\n    return {"question": "Which came first?"}\n')
        self.save()
        model = FollowupModel()
        session = ChatSession(self.root, env={}, model=model, embedder=Embedder())
        first = await session.send("history")
        self.assertIsNone(first["trace"]["error"], first)
        followup = await session.send("ambiguous")
        self.assertIsNone(followup["trace"]["error"], followup)
        self.assertEqual(followup["reply"], "Here is your quiz.")
        self.assertIn({"skill": "history", "score": 0, "source": "continuation"}, followup["trace"]["matches"])
        self.assertTrue(any(x["name"] == "quiz_for_lesson" for x in followup["trace"]["toolCalls"]))
        changed = await session.send("math")
        self.assertIsNone(changed["trace"]["error"], changed)
        self.assertEqual(changed["trace"]["activatedSkills"], ["math"])
        self.assertNotIn("quiz_for_lesson", model.observed_tools[-1])
        await session._runner.close()
