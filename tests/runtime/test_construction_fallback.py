"""A caught factory failure stays visible to Doctor, without leaking log data.

Regression shape from a host: a repeated scoped tool name made
CapabilityBindings raise, the host logged and served its native fallback, and
inspection of that valid fallback reported nothing unusual.
"""

import logging
import sys
from types import ModuleType
from unittest.mock import patch

import yaml
from google.adk.agents import LlmAgent
from google.adk.skills import Frontmatter, Skill

from adkl import construction_log, doctor, execution
from adkl.capability_bindings import CapabilityBindings
from adkl.host import HostChatSession


def lookup(query: str) -> dict:
    """Look up a record."""
    return {"query": query}


def factory_with_caught_failure():
    log = logging.getLogger("host.capability_runtime")
    live = LlmAgent(name="live", model="gemini-test", instruction="Help.")
    skill = Skill(frontmatter=Frontmatter(name="records", description="Read records",
        metadata={"adk_additional_tools": ["lookup"]}), instructions="Call lookup.")
    try:
        binding = CapabilityBindings(skills=[skill], skill_tools=[lookup, lookup])
        return LlmAgent(name="live", model="gemini-test", **binding.agent_kwargs())
    except Exception as error:  # The host keeps serving; that is the risk.
        log.error("capability_startup_failed runtime=native error_type=%s",
                  type(error).__name__)
        return live


def test_inspect_reports_caught_capability_failure(tmp_path, monkeypatch):
    module = ModuleType("construction_log_fixture")
    module.build = factory_with_caught_failure
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump({"version": 1,
        "agent": {"name": "live", "factory": module.__name__ + ":build"}}),
        encoding="utf-8")
    original = logging.Logger.handle
    snapshot = HostChatSession(tmp_path, env={}).inspect()
    assert logging.Logger.handle is original
    assert snapshot["constructionLog"]["entries"] == [{
        "logger": "host.capability_runtime", "level": "ERROR",
        "message": "capability_startup_failed runtime=native error_type=%s",
        "exception": None}]
    # Formatted arguments (here the error type) are never retained.
    assert "ValueError" not in str(snapshot["constructionLog"])
    raw = construction_log.findings(snapshot)
    assert [(f["rule"], f["severity"]) for f in raw] == [("FACTORY_CONSTRUCTION_ERROR", "WARN")]


def test_doctor_native_check_surfaces_construction_errors(tmp_path):
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump({"version": 1,
        "agent": {"name": "live", "factory": "agent:build"}}), encoding="utf-8")
    snapshot = {"agent": {"name": "live", "adkl": [], "children": []},
                "constructionLog": {"dropped": 0, "entries": [
                    {"logger": "host", "level": "ERROR", "message": "startup_failed", "exception": None}]}}
    worker = type("W", (), {"request": lambda self, op: snapshot, "close": lambda self: None})()
    with patch.object(execution, "Worker", return_value=worker):
        report = doctor.diagnose(tmp_path, inspect=True)
    native = next(c for c in report["checks"] if c["id"] == "native")
    # Reported even when the fallback agent has no ADKL bindings at all.
    assert native["status"] == "findings"
    finding = next(f for f in report["findings"] if f["rule"] == "FACTORY_CONSTRUCTION_ERROR")
    assert finding["severity"] == "WARN" and finding["category"] == "Configuration"
    assert "startup_failed" in finding["message"]
    # Advisory: Doctor cannot tell whether the host's fallback was intended.
    assert all(f["severity"] != "ERROR" for f in report["findings"]
               if f["rule"].startswith("FACTORY_"))
