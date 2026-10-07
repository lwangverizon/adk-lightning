"""Evaluation evidence cannot silently omit cases or hide local regressions."""
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from adkl.evaluation import EvaluationError, compare


def run():
    return {"version": 1, "suite_sha256": "a" * 64, "evaluator_sha256": "b" * 64,
            "subject": {"revision": "fixture", "artifact_sha256": "c" * 64},
            "results": [{"case_id": "lookup", "repetition": i, "passed": True,
                         "latency_ms": i * 100, "cost_usd": .01} for i in (1, 2)]}


class TestEvaluation(unittest.TestCase):
    def test_improvement_records_identity_policy_and_metrics_without_mutation(self):
        baseline, candidate = run(), run()
        candidate["subject"] = {"revision": "new", "artifact_sha256": "d" * 64}
        for row in candidate["results"]:
            row["latency_ms"] /= 2
            row["cost_usd"] /= 2
        original = copy.deepcopy(candidate)
        report = compare(baseline, candidate, max_cost_ratio=1, max_latency_ratio=1)
        self.assertTrue(report["passed"])
        self.assertEqual(report["candidate"]["p95_latency_ms"], 100)
        self.assertEqual(report["candidate"]["cost_per_success_usd"], .005)
        self.assertEqual(report["subjects"]["candidate"]["revision"], "new")
        self.assertEqual(candidate, original)

    def test_incompatible_or_missing_evidence_is_rejected(self):
        for change in (lambda x: x["results"].pop(),
                       lambda x: x["results"].append(x["results"][0]),
                       lambda x: x.update(suite_sha256="e" * 64),
                       lambda x: x.update(evaluator_sha256="e" * 64),
                       lambda x: x.update(results=[]),
                       lambda x: x["results"][0].update(skipped=True)):
            candidate = run()
            change(candidate)
            with self.assertRaises(EvaluationError):
                compare(run(), candidate)

    def test_new_success_cannot_hide_regression_on_another_case(self):
        baseline = run()
        baseline["results"][1].update(case_id="other", passed=False)
        candidate = copy.deepcopy(baseline)
        candidate["results"][0]["passed"] = False
        candidate["results"][1]["passed"] = True
        report = compare(baseline, candidate, min_pass_rate=.5)
        self.assertFalse(report["passed"])
        self.assertEqual(report["regressed_cases"], ["lookup"])

    def test_repetitions_are_aggregated_per_case(self):
        baseline = run()
        baseline["results"][0]["passed"] = False
        candidate = copy.deepcopy(baseline)
        candidate["results"][0]["passed"] = True
        candidate["results"][1]["passed"] = False
        self.assertTrue(compare(baseline, candidate, min_pass_rate=.5)["passed"])
        self.assertFalse(compare(baseline, candidate)["passed"])

    def test_zero_baselines_and_no_successes(self):
        baseline = run()
        for row in baseline["results"]:
            row.update(latency_ms=0, cost_usd=0, passed=False)
        report = compare(baseline, baseline, min_pass_rate=0, max_cost_ratio=1, max_latency_ratio=1)
        self.assertTrue(report["passed"])
        self.assertIsNone(report["baseline"]["cost_per_success_usd"])
        candidate = copy.deepcopy(baseline)
        candidate["results"][0].update(latency_ms=1, cost_usd=.01)
        report = compare(baseline, candidate, min_pass_rate=0, max_cost_ratio=1, max_latency_ratio=1)
        self.assertEqual(len(report["failures"]), 2)

    def test_invalid_metrics_types_and_policy_fail_closed(self):
        for value in (True, -1, float("nan"), float("inf"), "0.1", None, 10**400):
            candidate = run()
            candidate["results"][0]["cost_usd"] = value
            with self.subTest(value=str(value)), self.assertRaises(EvaluationError):
                compare(run(), candidate)
        for kwargs in ({"min_pass_rate": 1.1}, {"min_pass_rate": True},
                       {"max_cost_ratio": float("nan")}, {"max_latency_ratio": -1}):
            with self.assertRaises(EvaluationError):
                compare(run(), run(), **kwargs)
        for field, value in (("passed", 1), ("repetition", True), ("case_id", "")):
            candidate = run()
            candidate["results"][0][field] = value
            with self.assertRaises(EvaluationError):
                compare(run(), candidate)

    def test_cli_codes_and_exact_input_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory) / "base.json"
            candidate = pathlib.Path(directory) / "candidate.json"
            raw = json.dumps(run()).encode()
            base.write_bytes(raw)
            candidate.write_bytes(raw)
            def invoke():
                return subprocess.run([sys.executable, "-m", "adkl.evaluation", str(base), str(candidate)],
                                      text=True, capture_output=True)
            result = invoke()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["input_sha256"]["baseline"], hashlib.sha256(raw).hexdigest())
            failed = run()
            failed["results"][0]["passed"] = False
            candidate.write_text(json.dumps(failed))
            self.assertEqual(invoke().returncode, 1)
            candidate.write_text('{"version": 1, "version": 1}')
            result = invoke()
            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate JSON field", result.stderr)
            candidate.unlink()
            self.assertEqual(invoke().returncode, 2)
