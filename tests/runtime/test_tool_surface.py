"""What the console is told about the turn's tool surface, through both factories.

The trace's tool fields are a display contract: `chat_panel.dart` gates its whole
tool/LLM-turn section on `boundTools`, so leaving that list empty hides the token
cost, the widget tools and the load problems of a turn that did bind tools. The
names come from the runtime (activated skills' frontmatter) and are resolved
here, which is the only side holding a catalog and the imported callables.
"""
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import models
from google.genai import types

from adkl import projects
from adkl.bindings import RuntimeBindings
from adkl.chat import ChatSession
from adkl.host import HostChatSession
from adkl.retrieval import Match, MatchResult

TOOLS = '''
def lookup_lesson(topic: str = "") -> dict:
    """Return a synthetic lesson."""
    return {"lesson": topic}


def quiz_for_lesson() -> dict:
    """Return a synthetic question."""
    return {"question": "Which came first?"}


def render_score(score: int = 0) -> dict:
    """Return a synthetic score."""
    return {"score": score}
'''


class Embedder:
    """One fixed query vector, so activation is decided by the skill vectors."""

    def __init__(self, vector):
        self.vector = vector

    async def embed(self, text):
        return self.vector


class TextModel(BaseLlm):
    model: str = "fixture"

    async def generate_content_async(self, request, stream=False):
        yield LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text="Here is your quiz.")]))


class LoadingModel(BaseLlm):
    """Loads a second skill itself on the first call, then answers."""

    model: str = "fixture"
    calls: int = 0

    async def generate_content_async(self, request, stream=False):
        self.calls += 1
        part = (types.Part(function_call=types.FunctionCall(
                    name="load_skill", args={"skill_name": "quizzes"}, id="load"))
                if self.calls == 1 else types.Part(text="Here is your quiz."))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class TestProjectToolSurface(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "prompt.md").write_text("Teach the learner.")
        (self.root / "tool_surface_fixture.py").write_text(TOOLS)
        declared = {"lessons": ["lookup_lesson"], "quizzes": ["quiz_for_lesson", "render_score"]}
        for name, tools in declared.items():
            directory = self.root / "skills" / name
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Teach {name}.\nmetadata:\n"
                f"  adk_additional_tools: [{', '.join(tools)}]\n---\nUNIQUE_{name}_PROCEDURE\n")
        (self.root / "vectors.json").write_text(json.dumps({
            "lessons": {"embedding": [1, 0]}, "quizzes": {"embedding": [0, 1]}}))
        (self.root / "catalog.json").write_text(json.dumps({"tools": [
            {"name": "lookup_lesson", "module": "tool_surface_fixture.py",
             "estimated_input_tokens_per_turn": 120},
            {"name": "quiz_for_lesson", "module": "tool_surface_fixture.py",
             "estimated_input_tokens_per_turn": 690, "emits_widget": {"value": True}},
            {"name": "render_score", "module": "tool_surface_fixture.py",
             "estimated_input_tokens_per_turn": 40},
        ]}))
        (self.root / "agent.yaml").write_text(yaml.safe_dump({
            "agent": {"name": "fixture", "provider": "gemini", "model": "fixture",
                      "instruction": {"template": "prompt.md"}},
            "skills": {"include": ["lessons", "quizzes"]},
            "tools": {"catalog": "catalog.json", "repo": "."},
            "features": {
                # Both skills stay retrievable so the model can load the second
                # itself; only the confident top match is auto-activated.
                "skill_retrieval": {"vectors": "vectors.json", "top_k": 2, "total_cap": 2,
                                    "min_skills_for_retrieval": 0,
                                    "bm25": False, "auto_activate": True,
                                    "auto_activate_min_score": .9},
                "pipeline": ["skill_retrieval"],
            }}))

    async def test_tools_of_a_skill_the_model_loads_mid_turn_join_the_reported_surface(self):
        # `lessons` scores high enough to auto-activate before the first model
        # call; `quizzes` clears the retrieval floor, so the model can load it
        # itself during that call. Both skills' tools are then genuinely bound
        # for the second call, and a surface read from the pre-model activation
        # alone would under-report the turn the console is describing.
        session = ChatSession(self.root, env={}, model=LoadingModel(),
                              embedder=Embedder([1, .2]))
        self.assertEqual(session.preflight(), [])
        result = await session.send("Teach me fractions.")
        trace = result["trace"]
        self.assertIsNone(trace["error"], result)
        self.assertEqual(trace["activatedSkills"], ["lessons", "quizzes"])
        self.assertEqual(trace["modelCalls"], 2)
        self.assertEqual(trace["boundTools"],
                         ["lookup_lesson", "quiz_for_lesson", "render_score"])
        # Catalogued cost of exactly those tools, which is what the operator is
        # deciding about; the numbers are read from the catalog, never summed
        # over everything the project could bind.
        self.assertEqual(trace["toolTokens"], 850)
        # Read from `emits_widget`, not guessed from the name: this agent's real
        # widget tools carry no `render_`/`stage_` prefix, and `render_score`
        # here emits nothing despite having one.
        self.assertEqual(trace["widgetTools"], ["quiz_for_lesson"])
        self.assertEqual(trace["executableTools"], trace["boundTools"])
        self.assertEqual(trace["unimportableTools"], [])
        self.assertEqual(trace["unresolvedTools"], [])
        await session._runner.close()

    async def test_a_turn_that_activates_nothing_claims_no_tools(self):
        """An unactivated skill's tools are not in the prompt and cost nothing."""
        session = ChatSession(self.root, env={}, model=TextModel(),
                              embedder=Embedder([1, 1]))
        result = await session.send("Something unrelated.")
        trace = result["trace"]
        self.assertIsNone(trace["error"], result)
        self.assertEqual(trace["activatedSkills"], [])
        self.assertEqual((trace["boundTools"], trace["toolTokens"]), ([], 0))
        self.assertEqual((trace["executableTools"], trace["unimportableTools"]), ([], []))
        await session._runner.close()


class Selector:
    async def match(self, text, state):
        return MatchResult([Match("quizzes", 1.0, "embedding")], auto_activated="quizzes")


def host_factory():
    def quiz_for_lesson() -> dict:
        """Return a synthetic question."""
        return {"question": "Which came first?"}

    skill = models.Skill(frontmatter=models.Frontmatter(
        name="quizzes", description="Quiz the learner",
        metadata={"adk_additional_tools": ["quiz_for_lesson"]}),
        instructions="Quiz using the tool.")
    binding = RuntimeBindings(
        skills=[skill], tools=[quiz_for_lesson], selector=Selector(),
        feature_config={"pipeline": ["skill_retrieval"],
                        "skill_retrieval": {"auto_activate": True, "min_skills_for_retrieval": 0}})
    return LlmAgent(name="host", model=TextModel(), instruction="Quiz the learner.",
                    **binding.agent_kwargs())


class TestHostFactoryToolSurface(unittest.IsolatedAsyncioTestCase):
    """A host factory registers tools on its bindings, not through a project catalog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        projects.attach(self.root, "host_agent:build")

    def catalog(self, entries):
        (self.root / "catalog.json").write_text(json.dumps({"tools": entries}))
        doc = yaml.safe_load((self.root / "agent.yaml").read_text())
        doc["tools"] = {"catalog": "catalog.json"}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))

    async def surface(self):
        with patch.dict("sys.modules", {"host_agent": SimpleNamespace(build=host_factory)}):
            session = HostChatSession(self.root, env={})
            self.assertEqual(session.preflight(), [])
            result = await session.send("Quiz me.")
            self.assertIsNone(result["trace"]["error"], result)
            await session._runner.close()
            return result["trace"]

    async def test_a_catalogued_tool_the_host_binds_is_executable_not_unimportable(self):
        # The host never runs the console's own tool import, so `_tool_fns` is
        # absent rather than empty here. Resolving against the authoring catalog
        # alone would put every tool this factory does execute into the "could
        # not import" bucket -- a load failure the console would report for a
        # working agent.
        self.catalog([{"name": "quiz_for_lesson", "module": "",
                       "estimated_input_tokens_per_turn": 690}])
        trace = await self.surface()
        self.assertEqual(trace["boundTools"], ["quiz_for_lesson"])
        self.assertEqual(trace["executableTools"], ["quiz_for_lesson"])
        self.assertEqual(trace["unimportableTools"], [])
        self.assertEqual(trace["toolTokens"], 690)

    async def test_without_a_catalog_the_binding_registry_is_the_authority(self):
        """Nothing to resolve against, and construction already refused a gap:
        RuntimeBindings will not build when a skill declares a tool it cannot
        execute, so what the binding holds is the whole executable surface."""
        trace = await self.surface()
        self.assertEqual(trace["boundTools"], ["quiz_for_lesson"])
        self.assertEqual(trace["executableTools"], ["quiz_for_lesson"])
        self.assertEqual((trace["unresolvedTools"], trace["toolTokens"]), ([], 0))
