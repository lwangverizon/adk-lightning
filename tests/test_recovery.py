"""Recovery metadata survives every chat transport without executing stale code."""
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from adkl import execution, server
from test_execution import make_project


class TestRecovery(unittest.TestCase):
    def handler(self):
        handler = object.__new__(server.Handler)
        handler.headers = {"Host": "localhost"}
        handler._send = lambda status, payload: (status, payload)
        handler._body = lambda: {"text": "hello"}
        return handler

    def test_stale_worker_returns_action_and_clear_replaces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            old = execution.Worker(root)
            (root / "agent.py").write_text("changed = True")
            handler = self.handler()
            with patch.object(server._workspace, "root", root), patch.object(server._workspace, "chat", old):
                for accept in ("application/json", "text/event-stream"):
                    handler.headers["Accept"] = accept
                    status, data = handler._chat_send()
                    self.assertEqual(status, 409)
                    self.assertEqual(data["code"], "stale_conversation")
                    self.assertTrue(data["recoverable"])
                    self.assertEqual(data["action"], "clear_conversation")
                    self.assertNotIn("traceback", data)
                _, data = handler._chat_status()
                self.assertFalse(data["available"])
                self.assertEqual(data["action"], "clear_conversation")
                self.assertEqual(handler._chat_clear()[0], 200)
                replacement = handler._chat()
                self.assertIsNot(replacement, old)
                self.assertNotEqual(replacement.identity["sourceDigest"], old.identity["sourceDigest"])

    def test_started_stream_preserves_typed_terminal_error(self):
        def stream(*args):
            yield {"version": 1, "type": "progress"}
            raise execution.StaleConversationError("Clear to reload")
        handler = self.handler()
        handler._chat = lambda: Mock(stream=stream)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.connection = Mock()
        handler.wfile = io.BytesIO()
        handler._chat_stream("hello", None)
        handler.send_response.assert_called_once_with(200)
        frames = [json.loads(line[6:]) for line in handler.wfile.getvalue().decode().splitlines() if line]
        self.assertEqual(frames[-1]["type"], "error")
        self.assertEqual(frames[-1]["action"], "clear_conversation")

    def test_generic_execution_error_does_not_offer_false_recovery(self):
        handler = self.handler()
        for error in (execution.ExecutionError("Invalid worker response"),
                      execution.StaleConversationError("Clear to reload")):
            result = handler._guard(Mock(side_effect=error))
            # _guard writes through _send even outside the chat routes.
            self.assertIsNone(result)
            handler._send = Mock(return_value=None)
            handler._guard(Mock(side_effect=error))
            status, data = handler._send.call_args.args
            self.assertEqual(status, error.status)
            self.assertEqual(data["recoverable"], isinstance(error, execution.StaleConversationError))

    def test_factory_summary_reports_unknown_without_starting_runtime(self):
        handler = self.handler()
        doc = {"version": 1, "agent": {"name": "host", "factory": "agent:build"}}
        with patch.object(server, "_load_agent", return_value=(doc, [], [])), patch.object(
                server, "_catalog", side_effect=AssertionError("Must not read declarative tools")):
            status, data = handler._agent(None)
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "factory")
        self.assertIsNone(data["skillCount"])
        self.assertIsNone(data["toolCount"])
