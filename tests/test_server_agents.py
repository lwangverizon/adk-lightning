#!/usr/bin/env python3
"""Tests for multi-agent discovery and switching.

The console could only ever reach the agent named by `--root`, while three valid
agents sat on disk. Switching updates one workspace selection so every existing
endpoint follows without change -- which is the cheap part. The parts worth
testing are the ones that go wrong quietly:

* a broken agent.yaml must still be LISTED, or a misconfigured agent looks like a
  missing one and nobody knows where to look
* selecting must drop the chat session, or the console answers as the previous
  agent under the new agent's name
* an unknown id must fail loudly rather than no-op, since a silent no-op shows
  the new agent as selected while serving the old one
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import server  # noqa: E402


AGENT_YAML = """version: 1
agent:
  name: {name}
  model: {model}
skills:
  dir: skills
  include: {skills}
"""


class _Tree:
    """A temp project with agents at chosen relative paths."""

    def __init__(self):
        self.base = pathlib.Path(tempfile.mkdtemp())
        # A project marker, so discovery treats this as the enclosing project
        # exactly as it would a real checkout.
        (self.base / "run_tests.sh").write_text("#!/bin/sh\n")

    def agent(self, rel: str, *, name: str, model: str = "m",
              skills=("a", "b"), broken: bool = False):
        d = self.base / rel
        d.mkdir(parents=True, exist_ok=True)
        body = ("agent: [this is not: valid yaml\n" if broken
                else AGENT_YAML.format(name=name, model=model,
                                       skills=list(skills)))
        (d / "agent.yaml").write_text(body, encoding="utf-8")
        return d

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tree = _Tree()

    def tearDown(self):
        self.tree.cleanup()

    def test_agents_at_several_depths_are_found(self):
        # The real layout: one at the project root, two nested. Scanning only the
        # immediate parent of the selected agent found the two examples and
        # missed workspace/ -- the agent a user is most likely iterating on.
        self.tree.agent("workspace", name="ws")
        self.tree.agent("projects/one", name="one")
        self.tree.agent("projects/two", name="two")
        found = server.discover_agents(self.tree.base)
        self.assertEqual(sorted(a["id"] for a in found),
                         ["projects/one", "projects/two", "workspace"])

    def test_an_agent_at_the_base_gets_a_dot_id(self):
        (self.tree.base / "agent.yaml").write_text(
            AGENT_YAML.format(name="root_agent", model="m", skills=["x"]))
        found = server.discover_agents(self.tree.base)
        self.assertEqual(found[0]["id"], ".")
        self.assertEqual(found[0]["name"], "root_agent")

    def test_metadata_the_picker_shows_is_read(self):
        self.tree.agent("w", name="pretty_name", model="gemini-3.6-flash",
                        skills=["a", "b", "c"])
        entry = server.discover_agents(self.tree.base)[0]
        self.assertEqual(entry["name"], "pretty_name")
        self.assertEqual(entry["model"], "gemini-3.6-flash")
        self.assertEqual(entry["skills"], 3)
        self.assertIsNone(entry["error"])

    def test_a_broken_agent_is_listed_with_its_error(self):
        # Listed, not hidden. "I can see it is broken" is debuggable; "my agent
        # vanished" is not.
        self.tree.agent("good", name="good")
        self.tree.agent("bad", name="bad", broken=True)
        found = {a["id"]: a for a in server.discover_agents(self.tree.base)}
        self.assertIn("bad", found)
        self.assertTrue(found["bad"]["error"])
        self.assertIsNone(found["good"]["error"])

    def test_a_broken_agent_still_reports_a_usable_name(self):
        # Falls back to the directory name, so the picker row is not blank.
        self.tree.agent("my-agent", name="ignored", broken=True)
        entry = server.discover_agents(self.tree.base)[0]
        self.assertEqual(entry["name"], "my-agent")

    def test_scanning_stops_at_the_declared_depth(self):
        # Deeper would offer test fixtures and generated output as real agents.
        self.tree.agent("a/b/c/deep", name="too_deep")
        self.assertEqual(server.discover_agents(self.tree.base), [])

    def test_an_empty_tree_is_empty_not_an_error(self):
        self.assertEqual(server.discover_agents(self.tree.base), [])

    def test_each_agent_appears_once(self):
        self.tree.agent("only", name="only")
        found = server.discover_agents(self.tree.base)
        self.assertEqual(len(found), 1)


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.tree = _Tree()
        self._root, self._chat, self._agents = (
            server._workspace.root, server._workspace.chat, server._workspace.agents)

    def tearDown(self):
        server._workspace.root, server._workspace.chat, server._workspace.agents = (
            self._root, self._chat, self._agents)
        self.tree.cleanup()

    def _load(self):
        server._workspace.agents = server.discover_agents(self.tree.base)

    def test_selecting_rebinds_root(self):
        # One workspace selection, so every endpoint follows without being told.
        a = self.tree.agent("first", name="first")
        self.tree.agent("second", name="second")
        self._load()
        entry = server.select_agent("second")
        self.assertIsNotNone(entry)
        self.assertEqual(server._workspace.root.name, "second")
        self.assertNotEqual(server._workspace.root, a)

    def test_selecting_drops_the_chat_session(self):
        # The session holds a compiled prompt, a bound toolset and a live Runner
        # for the PREVIOUS agent. Keeping it would answer as the old agent while
        # the UI showed the new one.
        self.tree.agent("first", name="first")
        self.tree.agent("second", name="second")
        self._load()
        server._workspace.chat = object()
        server.select_agent("second")
        self.assertIsNone(server._workspace.chat)

    def test_an_unknown_id_changes_nothing(self):
        # Must fail loudly. A silent no-op is the worst outcome: the console shows
        # the new agent selected while every endpoint serves the old one.
        self.tree.agent("first", name="first")
        self._load()
        server.select_agent("first")
        before = server._workspace.root
        self.assertIsNone(server.select_agent("nope"))
        self.assertEqual(server._workspace.root, before)

    def test_selecting_the_current_agent_is_harmless(self):
        self.tree.agent("only", name="only")
        self._load()
        server.select_agent("only")
        first = server._workspace.root
        server.select_agent("only")
        self.assertEqual(server._workspace.root, first)

    def test_a_broken_agent_can_still_be_selected_by_the_api(self):
        """Selectable server-side even though the UI disables it.

        The API must not decide policy the UI already handles: a caller that
        deliberately switches to a broken agent should get the validation errors
        from /api/agent, which is more useful than a refusal.
        """
        self.tree.agent("bad", name="bad", broken=True)
        self._load()
        self.assertIsNotNone(server.select_agent("bad"))


if __name__ == "__main__":
    unittest.main(verbosity=0)


class TestDeclaredFeatureConfig(unittest.TestCase):
    """What the console's feature toggle is allowed to write back.

    The toggle rebuilt the features block from the DEFAULTS-MERGED `config`, so
    every save turned inherited defaults into declared ones and dropped
    `enabled: false`. Both are silent: the file reads as if the author wrote
    those values, and a kill switch simply stops existing.
    """

    def test_an_enabled_false_kill_switch_survives_a_round_trip(self):
        doc = {"features": {"pipeline": ["output_cleanup"],
                            "output_cleanup": {"enabled": False}}}
        block = server.declared_feature_config(doc)
        self.assertEqual(block["output_cleanup"], {"enabled": False})

    def test_inherited_defaults_are_not_promoted_to_declared_ones(self):
        from adkl import features
        doc = {"features": {"pipeline": ["history_slicing"],
                            "history_slicing": {"recent_turns": 2}}}
        block = server.declared_feature_config(doc)
        merged = features.resolve(doc["features"]).config["history_slicing"]
        self.assertEqual(block["history_slicing"], {"recent_turns": 2})
        self.assertLess(len(block["history_slicing"]), len(merged))

    def test_pipeline_is_excluded_because_the_caller_supplies_it(self):
        doc = {"features": {"pipeline": ["output_cleanup"]}}
        self.assertNotIn("pipeline", server.declared_feature_config(doc))

    def test_a_project_with_no_features_block_is_empty_not_an_error(self):
        for doc in (None, {}, {"features": None}):
            self.assertEqual(server.declared_feature_config(doc), {})
