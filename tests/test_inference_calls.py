"""Recorder accounts for parallel calls, retries, zero calls and incomplete runs."""
from pathlib import Path
import tempfile
import unittest

from adkl.doctor_calls import input_digest
from adkl.inference_calls import InvocationCalls, evidence


class RecorderTests(unittest.TestCase):
    def test_parallel_nested_retry_and_streaming(self):
        capture = InvocationCalls("trial1", "happy")
        first = capture.begin(logical_id="parent", owner="parent")
        nested = capture.begin(logical_id="nested", owner="workflow")
        # Any number of stream chunks makes no new begin call.
        capture.end(nested)
        capture.end(first)
        retry = capture.begin(logical_id="parent", owner="fallback")
        capture.end(retry)
        row = capture.finish(passed=True)
        self.assertEqual(len(row["attempts"]), 3)
        self.assertEqual(len({a["logical_id"] for a in row["attempts"]}), 2)
        with self.assertRaises(ValueError):
            capture.finish(passed=True)
        with self.assertRaises(ValueError):
            capture.begin(logical_id="late", owner="parent")

    def test_unfinished_rejected_and_failed_request_counted(self):
        capture = InvocationCalls("trial1", "recovery")
        attempt = capture.begin(logical_id="parent", owner="parent")
        with self.assertRaises(ValueError):
            capture.finish(passed=False)
        capture.end(attempt)
        self.assertEqual(len(capture.finish(passed=False)["attempts"]), 1)

    def test_zero_and_invocation_isolation(self):
        first = InvocationCalls("trial1", "happy")
        second = InvocationCalls("trial2", "happy")
        first.begin(logical_id="parent", owner="parent")
        self.assertEqual(second.finish(passed=True)["attempts"], [])

    def test_evidence_rejects_source_changes_during_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "agent.py"
            source.write_text("# before\n", encoding="utf-8")
            started = input_digest(root, ["agent.py"])
            kwargs = dict(capability="billing", configuration="prod-default",
                          evidence_class="SCRIPTED_REPLAY", observations=[
                              InvocationCalls("trial", "happy").finish(passed=True)],
                          started_digest=started)
            receipt = evidence(root, ["agent.py"], **kwargs)
            self.assertEqual(receipt["evidence"], "SCRIPTED_REPLAY")
            source.write_text("# after\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                evidence(root, ["agent.py"], **kwargs)
