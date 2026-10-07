"""Real ADK execution across isolated, persistent project workers; no network."""
import pathlib
import sys
import tempfile
import unittest

import yaml
from google.adk.agents import LlmAgent  # noqa: F401 -- required native lane

from adkl import projects
from adkl.execution import Worker

HOST_FACTORY = '''from google.adk.agents import LlmAgent
from google.adk.skills import models

from adkl.bindings import RuntimeBindings


def search_products(query: str) -> dict:
    """Search the public catalog."""
    return {"query": query}


def build():
    skill = models.Skill(
        frontmatter=models.Frontmatter(
            name="shopping", description="Help a visitor shop.",
            metadata={"adk_additional_tools": ["search_products"]}),
        instructions="Answer from the public catalog.")
    binding = RuntimeBindings(skills=[skill], tools=[search_products],
                              feature_config={"pipeline": ["progressive_auth"]})
    # The binding sits on the sub-agent, as it does in the host that found this:
    # a manifest read from the root agent alone would report no features at all.
    child = LlmAgent(name="shopping", model="gemini-2.5-flash",
                     instruction="Help the visitor shop.", **binding.agent_kwargs())
    return LlmAgent(name="router", model="gemini-2.5-flash",
                    instruction="Route to the shopping agent.", sub_agents=[child])
'''


class TestNativeWorker(unittest.TestCase):
    def test_two_projects_keep_imports_secrets_and_turn_state_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            workers = []
            try:
                for label in ("alpha", "beta"):
                    root = pathlib.Path(tmp) / label
                    root.mkdir()
                    (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
                    (root / "uv.lock").write_text("synthetic fixture lock\n")
                    (root / ".env").write_text(f"GOOGLE_API_KEY={label}-fake-secret\n")
                    (root / "execution.yaml").write_text(yaml.safe_dump({
                        "version": 1, "default": "local", "profiles": {"local": {
                            "target": "local", "environment": "test", "secrets": ["GOOGLE_API_KEY"]}}}))
                    (root / "agent.yaml").write_text(yaml.safe_dump({
                        "agent": {"name": label, "model": "gemini-2.5-flash", "provider": "gemini",
                                  "instruction": {"template": "prompt.md"},
                                  "callbacks": {"before_agent_callback": ["isolated_callback.reply"]}},
                        "tools": {"repo": "."}, "features": {"pipeline": []}}))
                    (root / "prompt.md").write_text("Offline callback bypasses model.\n")
                    (root / "isolated_callback.py").write_text('''import asyncio, os
from google.genai import types
LOOP = None
async def reply(callback_context):
    global LOOP
    loop = asyncio.get_running_loop()
    assert LOOP is None or LOOP is loop
    LOOP = loop
    count = callback_context.state.get('count', 0) + 1
    callback_context.state['count'] = count
    return types.Content(role='model', parts=[types.Part(text=
        f"LABEL:{count}:{os.environ['GOOGLE_API_KEY']}:{os.getenv('DATABASE_URL', 'absent')}")])
'''.replace("LABEL", label))
                    workers.append(Worker(root))
                for worker in workers:
                    self.assertTrue(worker.request("status")["available"])
                for count in (1, 2):
                    for label, worker in zip(("alpha", "beta"), workers):
                        if count == 1:
                            result = worker.request("send", text="hello")
                        else:
                            frames = list(worker.stream("hello"))
                            result = frames[-1]["result"]
                            self.assertEqual([e["text"] for e in frames if e["type"] == "text"], [result["reply"]])
                            self.assertNotIn(f"{label}-fake-secret", str(frames))
                        self.assertIsNone(result["trace"]["error"], result["trace"])
                        self.assertEqual(result["reply"], f"{label}:{count}:[REDACTED]:absent")
                self.assertNotIn("isolated_callback", sys.modules)
            finally:
                for worker in workers:
                    worker.close()

    def test_a_host_factory_reports_the_preview_limits_of_the_features_it_runs(self):
        """This status payload is the console's only warning line for a host.

        `RuntimeBindings` refuses an unsupported feature at construction, so what
        a host can still be running unknowingly is a *partial* one:
        `progressive_auth` records a public-help attempt and enforces no sign-in
        ordering. Reporting an empty feature list dropped exactly that, while the
        same feature in an ADKL project warns in the chat panel -- and the
        descriptor's own `features:` block cannot stand in for it, because a host
        passes `feature_config` to the binding in code.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "host"
            root.mkdir()
            (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
            (root / "uv.lock").write_text("synthetic fixture lock\n")
            (root / "execution.yaml").write_text(yaml.safe_dump({
                "version": 1, "default": "local", "profiles": {"local": {
                    "target": "local", "environment": "test"}}}))
            (root / "host_agent.py").write_text(HOST_FACTORY)
            projects.attach(root, "host_agent:build")
            worker = Worker(root)
            try:
                status = worker.request("status")
            finally:
                worker.close()
            self.assertEqual(status["source"], "host_factory")
            self.assertTrue(status["available"], status)
            runtime = status["runtime"]
            self.assertEqual(runtime["target"], "host")
            self.assertEqual([e["name"] for e in runtime["features"] if e["requested"]],
                             ["progressive_auth"])
            self.assertTrue(any("does not enforce" in w.lower() for w in runtime["warnings"]),
                            runtime["warnings"])
            self.assertIn("Runtime behavior is owned by the host factory; "
                          "inspect its callback adoption.", runtime["warnings"])
            # The host's own callbacks and services stay outside this report, so
            # it may never read as complete, and nothing here blocks a preview.
            self.assertEqual((runtime["blockers"], runtime["canRun"], runtime["complete"]),
                             ([], True, False))
