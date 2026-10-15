"""Serving import order: a factory that builds alone can still fail at startup.

Synthetic regression shapes that direct factory inspection can miss:

- ``import api`` fails through a package ``__init__`` eagerly re-exporting a
  module that imports the still-initializing orchestrator. Factory-first
  construction succeeds but does not exercise the serving import order.
- A capability builder catches the same cycle, logs a startup failure and
  serves a native fallback instead of the expected capability.

The declared startup sequence must run before factory inspection. These
tests run a real project worker and real ADK construction, no model or network.
"""
import pathlib
import sys
import tempfile
import textwrap
import unittest

import yaml
from google.adk.agents import LlmAgent  # noqa: F401 -- required native lane
from google.adk.skills import Frontmatter
from pydantic import ValidationError

from adkl import doctor, skill_lint
from adkl.host import HostChatSession, StartupImportError

ENTRY = "import shopapp.orchestrator\n"
ORCHESTRATOR = "from shopapp import agent\nROOT = 'orchestrator'\n"
SERVICES_INIT = "from shopapp.services.runner import Runner\n"
RUNNER = "from shopapp.orchestrator import ROOT\n\nclass Runner:\n    root = ROOT\n"
AGENT_STRICT = textwrap.dedent("""\
    from google.adk.agents import LlmAgent
    from shopapp.services import Runner


    def build():
        return LlmAgent(name="shop", model="gemini-test", instruction="Help.")
    """)
AGENT_FALLBACK = textwrap.dedent("""\
    import logging

    from google.adk.agents import LlmAgent

    try:
        from shopapp.services import Runner
        MODE = "capability"
    except ImportError as error:
        logging.getLogger("shopapp.capability").error(
            "customer_capability_startup_failed error_type=%s", type(error).__name__)
        MODE = "native_fallback"


    def build():
        return LlmAgent(name="shop", model="gemini-test", instruction=MODE)
    """)


def project(root, agent_source, startup=None):
    package = root / "shopapp"
    (package / "services").mkdir(parents=True)
    (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    (root / "uv.lock").write_text("synthetic fixture lock\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'shopapp'\n")
    (root / "execution.yaml").write_text(yaml.safe_dump({
        "version": 1, "default": "local", "profiles": {"local": {
            "target": "local", "environment": "test"}}}))
    (root / "serving_entry.py").write_text(ENTRY)
    (package / "__init__.py").write_text("")
    (package / "orchestrator.py").write_text(ORCHESTRATOR)
    (package / "agent.py").write_text(agent_source)
    (package / "services" / "__init__.py").write_text(SERVICES_INIT)
    (package / "services" / "runner.py").write_text(RUNNER)
    agent = {"name": "shop", "factory": "shopapp.agent:build"}
    if startup:
        agent["startup"] = startup
    (root / "agent.yaml").write_text(yaml.safe_dump({"version": 1, "agent": agent}))
    return root


class TestStartupOrder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name).resolve()

    def diagnose(self, name, agent_source, startup=None):
        root = project(self.base / name, agent_source, startup)
        return doctor.diagnose(root, inspect=True)

    @staticmethod
    def rules(report):
        return {f["rule"]: f for f in report["findings"]}

    def test_cycle_only_on_the_serving_path_is_an_error(self):
        direct = self.diagnose("direct", AGENT_STRICT)
        native = next(c for c in direct["checks"] if c["id"] == "native")
        self.assertNotEqual(native["status"], "error", native)
        self.assertNotIn("STARTUP_IMPORT_FAILED", self.rules(direct))
        self.assertIn("declare agent.startup", native["detail"])

        served = self.diagnose("served", AGENT_STRICT, ["serving_entry"])
        native = next(c for c in served["checks"] if c["id"] == "native")
        failure = self.rules(served)["STARTUP_IMPORT_FAILED"]
        self.assertEqual(failure["severity"], "ERROR")
        self.assertIn("serving_entry raised ImportError", failure["message"])
        # Module names and exception classes only; no host exception text.
        self.assertNotIn("partially initialized", str(served))
        self.assertEqual(native["status"], "findings")
        self.assertIn("native declarations were not checked", native["detail"])
        self.assertFalse(served["passed"])

    def test_caught_fallback_on_the_serving_path_is_reported(self):
        direct = self.diagnose("direct", AGENT_FALLBACK)
        self.assertNotIn("FACTORY_CONSTRUCTION_ERROR", self.rules(direct))

        served = self.diagnose("served", AGENT_FALLBACK, ["serving_entry"])
        logged = self.rules(served)["FACTORY_CONSTRUCTION_ERROR"]
        self.assertIn("while declared startup modules were imported", logged["message"])
        self.assertIn("customer_capability_startup_failed", logged["message"])
        native = next(c for c in served["checks"] if c["id"] == "native")
        self.assertIn("Serving import order: serving_entry before the factory", native["detail"])

    def test_session_keeps_the_exception_contract_for_other_callers(self):
        root = project(self.base / "session", AGENT_STRICT, ["serving_entry"])
        sys.path.insert(0, str(root))
        self.addCleanup(lambda: [sys.modules.pop(name) for name in list(sys.modules)
                                 if name == "serving_entry" or name.startswith("shopapp")])
        self.addCleanup(sys.path.remove, str(root))
        session = HostChatSession(root, env={})
        self.assertEqual(session.preflight(), [
            "Startup module serving_entry failed to import (ImportError); "
            "reproduce the serving import order locally."])
        with self.assertRaises(StartupImportError):
            session.inspect()
        failed = session.inspect(startup_failure="report")
        self.assertIsNone(failed["agent"])
        self.assertEqual(failed["startup"], {"modules": ["serving_entry"], "imported": [],
                                             "failed": {"module": "serving_entry",
                                                        "exception": "ImportError"}})


class TestAdkDescriptionLimit(unittest.TestCase):
    """The static rule must agree with ADK's own loader, at the boundary."""

    def test_limit_matches_adk_frontmatter(self):
        limit = skill_lint.DESCRIPTION_MAX_CHARS
        Frontmatter(name="account", description="d" * limit)
        with self.assertRaises(ValidationError):
            Frontmatter(name="account", description="d" * (limit + 1))
        self.assertIsNone(skill_lint.description_too_long("d" * limit))
        self.assertIsNotNone(skill_lint.description_too_long("d" * (limit + 1)))


if __name__ == "__main__":
    unittest.main()
