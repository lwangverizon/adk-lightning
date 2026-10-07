"""Capability reporting must agree with enforcement, without model calls."""
import asyncio
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

# Same as every other file in tests/: run_tests.sh invokes these directly
# (`python tests/test_x.py`), so the repo root is not on sys.path by default.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import capabilities, features, server  # noqa: E402
from adkl.chat import ChatSession  # noqa: E402


class TestCapabilities(unittest.TestCase):
    def test_unknown_implementation_defaults_to_unsupported(self):
        spec = features.Spec("future", features.Kind.TOOLSET, "A future feature")
        with patch.dict(features.REGISTRY, {"future": spec}):
            with self.assertRaisesRegex(features.FeatureError, "future"):
                capabilities.require_supported(features.resolve({"pipeline": ["future"]}))

    def test_retired_feature_declarations_are_not_silently_ignored(self):
        for name in features.RETIRED:
            for config in ({name: {}, "pipeline": []},
                           {name: {"enabled": False}, "pipeline": [name]}):
                with self.assertRaisesRegex(features.FeatureError, "Retired"):
                    features.resolve(config)
            self.assertNotIn(name, {row["name"] for row in features.describe()})

    def test_partial_preview_is_never_reported_as_complete(self):
        config = features.resolve({"pipeline": ["prompt_state_injection"]})
        report = capabilities.require_supported(config)
        self.assertTrue(report["canRun"])
        self.assertFalse(report["complete"])
        self.assertIn("placeholders", report["warnings"][0])
        with self.assertRaises(features.FeatureError):
            capabilities.require_supported(config, "export")

    def test_wired_runtime_features_do_not_block_preview(self):
        # max_chars is spelled out because tool_result_trimming refuses to
        # inherit its registry default -- it changes the DATA the model sees, so
        # a pipeline entry alone is not consent. The value IS the registry
        # default, which keeps this test's configuration identical to what it
        # checked before that requirement existed; the point here is support
        # reporting, not tuning.
        config = features.resolve({"pipeline": ["history_slicing",
                                                 "tool_result_trimming",
                                                 "output_cleanup",
                                                 "pii_redaction"],
                                   "tool_result_trimming": {"max_chars": 4000}})
        report = capabilities.require_supported(config)
        self.assertTrue(report["canRun"])
        statuses = {item["name"]: item["status"] for item in report["features"]}
        self.assertEqual(statuses["history_slicing"], "implemented")
        self.assertEqual(statuses["pii_redaction"], "implemented")


class TestExecutionBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.doc = {"agent": {"name": "fixture", "model": "fixture"},
                    "features": {"pipeline": ["identity_gate"]}}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(self.doc))

    def test_direct_send_refuses_before_model_or_tool_construction(self):
        session = ChatSession(self.root, env={"GEMINI_API_KEY": "test-only"})
        with patch("adkl.chat.gw.build_model") as model, patch("adkl.chat._load_tool_fns") as tools:
            result = asyncio.run(session.send("Look up account 123"))
        self.assertIsNone(result["reply"])
        self.assertIn("identity_gate", result["trace"]["error"])
        model.assert_not_called()
        tools.assert_not_called()
        self.assertTrue(any("identity_gate" in p for p in session.preflight()))

    def test_http_send_cannot_bypass_preflight(self):
        handler = server.Handler.__new__(server.Handler)
        handler._body = lambda: {"text": "Look up account 123"}
        handler._chat = Mock()
        handler._send = Mock()
        with patch.object(server._workspace, "root", self.root):
            handler._chat_send()
        self.assertEqual(handler._send.call_args.args[0], 409)
        handler._chat.assert_not_called()

    def test_retired_feature_save_is_rejected_without_mutating_source(self):
        handler = server.Handler.__new__(server.Handler)
        handler._body = lambda: {"features": self.doc["features"]}
        handler._send = Mock()
        before = (self.root / "agent.yaml").read_bytes()
        with patch.object(server._workspace, "root", self.root):
            handler._features_put()
        self.assertEqual(handler._send.call_args.args[0], 409)
        self.assertEqual(before, (self.root / "agent.yaml").read_bytes())

    def test_retired_config_is_reported_by_agent_endpoint(self):
        handler = server.Handler.__new__(server.Handler)
        handler._send = Mock()
        with patch.object(server._workspace, "root", self.root):
            handler._agent(None)
        self.assertIn("identity_gate", str(handler._send.call_args))


if __name__ == "__main__":
    unittest.main()
