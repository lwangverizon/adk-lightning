"""Call-budget observations stay distinct from static/runtime safety claims."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from adkl import doctor, doctor_calls


class CallBudgets(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "agent.py").write_text("# no execution\n", encoding="utf-8")
        (self.root / "agent.yaml").write_text(
            "version: 1\nagent:\n  name: fixture\n  factory: agent:build\n", encoding="utf-8")
        self.cap = dict(id="device", kind="workflow", surface="web", variant="prod-default",
                        scenarios=["happy", "recovery"], evidence="calls.json")
        self.config = dict(version=1, catalog="prod", coverage="complete", budget=3,
                           inputs=["agent.py", "agent.yaml"], capabilities=[self.cap])
        self.payload = dict(version=1, capability="device", configuration="prod-default",
                            source_digest=doctor_calls.input_digest(self.root, self.config["inputs"]),
                            recorded_at=datetime.now(timezone.utc).isoformat(),
                            evidence="MEASURED_EVAL", observations=[self.row("happy", 3), self.row("recovery", 4)])

    def row(self, scenario, count):
        return dict(id=scenario, scenario=scenario, passed=scenario == "happy", attempts=[
            dict(id=f"a{i}", logical_id=f"l{i}", owner="nested" if i else "parent")
            for i in range(count)])

    def save(self):
        for name, value in ((doctor_calls.CONFIG, self.config), ("calls.json", self.payload)):
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def collect(self):
        self.save()
        return doctor_calls.collect(self.root)

    def test_three_allowed_four_warns_including_unsuccessful_nested_work(self):
        summary, findings = self.collect()
        row = summary["capabilities"][0]
        self.assertEqual(row["maximum"], 4)
        self.assertEqual(row["overBudget"], 1)
        self.assertEqual(row["successRate"], .5)
        self.assertIsNone(row["p95"])
        self.assertEqual([f["rule"] for f in findings], ["CAPABILITY_LLM_CALLS_OVER_BUDGET"])
        self.assertIn("observed, not required", findings[0]["message"])

    def test_retry_attempts_count_but_stream_duplicates_rejected(self):
        self.payload["observations"][1]["attempts"][3]["logical_id"] = "l2"
        summary, _ = self.collect()
        self.assertEqual(summary["capabilities"][0]["retryAttempts"], 1)
        self.payload["observations"][1]["attempts"][3]["id"] = "a2"
        with self.assertRaises(ValueError):
            self.collect()

    def test_explicit_zero_is_not_unknown(self):
        self.payload["observations"] = [self.row("happy", 0), self.row("recovery", 0)]
        summary, findings = self.collect()
        self.assertEqual(summary["capabilities"][0]["maximum"], 0)
        self.assertFalse(findings)
        self.cap["evidence"] = None
        summary, findings = self.collect()
        self.assertIsNone(summary["capabilities"][0]["maximum"])
        self.assertIsNone(summary["capabilities"][0]["overBudget"])
        self.assertEqual(summary["capabilities"][0]["state"], "NOT_MEASURED")
        self.assertEqual(findings[0]["rule"], "CAPABILITY_LLM_CALLS_UNMEASURED")

    def test_source_and_variant_drift_reject_measurements(self):
        (self.root / "agent.py").write_text("# changed\n", encoding="utf-8")
        summary, findings = self.collect()
        self.assertEqual(summary["capabilities"][0]["state"], "STALE")
        self.assertIsNone(summary["capabilities"][0]["maximum"])
        self.assertEqual(findings[0]["rule"], "CAPABILITY_LLM_CALLS_EVIDENCE_STALE")

    def test_incomplete_scenarios_cannot_resolve_baseline(self):
        self.collect()
        before = doctor.diagnose(self.root)
        self.payload["observations"] = [self.row("happy", 1)]
        self.save()
        after = doctor.diagnose(self.root, previous=before)
        ids = {f["id"] for f in before["findings"] if f["rule"] == "CAPABILITY_LLM_CALLS_OVER_BUDGET"}
        self.assertTrue(ids.issubset(after["comparison"]["notVerified"]))

    def test_configuration_changes_do_not_claim_resolution(self):
        self.collect()
        before = doctor.diagnose(self.root)
        self.config["budget"] = 10
        self.save()
        after = doctor.diagnose(self.root, previous=before)
        self.assertFalse(after["comparison"]["resolved"])

    def test_changed_evidence_class_does_not_resolve_measured_findings(self):
        self.collect()
        before = doctor.diagnose(self.root)
        self.payload["evidence"] = "SCRIPTED_REPLAY"
        self.payload["observations"] = [self.row("happy", 1), self.row("recovery", 1)]
        self.save()
        after = doctor.diagnose(self.root, previous=before)
        self.assertFalse(after["comparison"]["resolved"])

    def test_real_improvement_resolves_current_comparable_evidence(self):
        self.collect()
        before = doctor.diagnose(self.root)
        self.payload["observations"][1] = self.row("recovery", 2)
        self.save()
        after = doctor.diagnose(self.root, previous=before)
        self.assertTrue(after["comparison"]["resolved"])

    def test_partial_catalog_and_scripted_evidence_are_visible(self):
        self.config["coverage"] = "partial"
        self.payload["evidence"] = "SCRIPTED_REPLAY"
        self.collect()
        rendered = doctor.render(doctor.diagnose(self.root))
        self.assertIn("PRODUCTION CAPABILITIES — LLM CALL BUDGET", rendered)
        self.assertIn("SCRIPTED_REPLAY", rendered)
        self.assertIn("CAPABILITY_LLM_COVERAGE_PARTIAL", rendered)

    def test_unconfigured_still_displays_unknown(self):
        rendered = doctor.render(doctor.diagnose(self.root))
        self.assertIn("NOT_MEASURED: configure doctor-calls.json", rendered)

    def test_invalid_inputs_fail_closed_without_echoing_data(self):
        for change in (lambda: self.config.update(budget=True),
                       lambda: self.config.update(inputs=["../outside"]),
                       lambda: self.payload.update(evidence="STATIC_ESTIMATE"),
                       lambda: self.payload.update(secret="must-not-leak")):
            with self.subTest(change=change):
                original = copy.deepcopy((self.config, self.payload))
                change()
                self.save()
                report = doctor.diagnose(self.root)
                check = next(c for c in report["checks"] if c["id"] == "capability_calls")
                self.assertEqual(check["status"], "error")
                self.assertNotIn("must-not-leak", doctor.render(report))
                self.config, self.payload = original

    def test_symlink_and_evidence_self_identity_rejected(self):
        (self.root / "link.py").symlink_to(self.root / "agent.py")
        self.config["inputs"] = ["link.py"]
        with self.assertRaises(ValueError):
            self.collect()
        self.config["inputs"] = ["calls.json"]
        with self.assertRaises(ValueError):
            self.collect()

    def test_old_evidence_is_not_current_even_when_source_matches(self):
        self.payload["recorded_at"] = "2020-01-01T00:00:00+00:00"
        summary, findings = self.collect()
        self.assertEqual(summary["capabilities"][0]["state"], "STALE")
        self.assertEqual(findings[0]["rule"], "CAPABILITY_LLM_CALLS_EVIDENCE_STALE")

    def test_default_budget_and_below_budget_regression(self):
        del self.config["budget"]
        self.payload["observations"] = [self.row("happy", 1), self.row("recovery", 1)]
        self.collect()
        before = doctor.diagnose(self.root)
        self.payload["observations"] = [self.row("happy", 3), self.row("recovery", 3)]
        self.save()
        after = doctor.diagnose(self.root, previous=before)
        rules = {f["rule"] for f in after["findings"]}
        self.assertIn("CAPABILITY_LLM_CALLS_REGRESSION", rules)
        self.assertNotIn("CAPABILITY_LLM_CALLS_OVER_BUDGET", rules)

    def test_percentile_requires_twenty_samples(self):
        self.payload["observations"] = [dict(self.row("happy", i % 5), id=f"run{i}") for i in range(20)]
        summary, _ = self.collect()
        self.assertEqual(summary["capabilities"][0]["p95"], 4)


if __name__ == "__main__":
    unittest.main()
