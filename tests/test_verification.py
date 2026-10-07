"""Saved changes, complete observations and failed runs retain honest evidence."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from adkl import verification


class TestVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
        (self.root / "agent.yaml").write_text("version: 1\nagent:\n  name: example\n")
        (self.root / "uv.lock").write_text("synthetic lock")
        (self.root / "pyproject.toml").write_text("# synthetic project")
        (self.root / "execution.yaml").write_text(yaml.safe_dump({"version": 1, "default": "local",
            "profiles": {"local": {"target": "local", "environment": "test"}}}))
        (self.root / "skill.md").write_text("expected procedure")
        (self.root / "host_eval.py").write_text('''from pathlib import Path
async def evaluate(*, case, repetition):
    return {"passed": Path("skill.md").read_text() == case["expected"],
            "latency_ms": 10, "cost_usd": 0}
''')
        (self.root / "cases.json").write_text(json.dumps({"repetitions": 2, "cases": [
            {"id": "procedure", "expected": "expected procedure"}]}))
        self.config = {"version": 1, "evaluator": "host_eval:evaluate", "evaluator_files": ["host_eval.py"],
                       "subject_files": ["skill.md"], "suite": "cases.json"}
        self.configure()

    def configure(self):
        (self.root / "verification.yaml").write_text(yaml.safe_dump(self.config))

    def test_baseline_edit_regression_and_restored_candidate_use_fresh_workers(self):
        baseline = verification.run(self.root, "baseline")
        self.assertEqual(baseline["state"], "baseline_recorded", baseline)
        self.assertEqual(verification.status(self.root)["state"], "baseline_recorded")
        (self.root / "skill.md").write_text("wrong procedure")
        stale = verification.status(self.root)
        self.assertEqual(stale["state"], "stale")
        self.assertEqual(stale["changes"], [{"path": "skill.md", "change": "modified"}])
        failed = verification.run(self.root, "candidate")
        self.assertEqual(failed["state"], "regressed", failed)
        self.assertEqual(failed["comparison"]["regressed_cases"], ["procedure"])
        (self.root / "skill.md").write_text("expected procedure")
        passed = verification.run(self.root, "candidate")
        self.assertEqual(passed["state"], "passed", passed)
        self.assertNotEqual(baseline["execution"]["pid"], passed["execution"]["pid"])
        self.assertNotIn("host_eval", sys.modules)
        self.assertEqual(verification.status(self.root)["baseline"], baseline["id"])
        self.assertEqual(len(list((self.root / ".adkl/verification").glob("*.json"))), 4)

    def test_candidate_requires_baseline_and_suite_changes_require_new_baseline(self):
        result = verification.run(self.root, "candidate")
        self.assertEqual(result["state"], "failed")
        baseline = verification.run(self.root, "baseline")
        (self.root / "cases.json").write_text(json.dumps({"repetitions": 1, "cases": [{"id": "new"}]}))
        result = verification.run(self.root, "candidate")
        self.assertEqual(result["state"], "failed")
        self.assertIn("new baseline", result["message"])
        self.assertEqual(verification.status(self.root)["baseline"], baseline["id"])

    def test_failed_attempt_does_not_leave_previous_pass_current(self):
        verification.run(self.root, "baseline")
        verification.run(self.root, "candidate")
        from adkl.execution import ExecutionError
        with patch("adkl.execution.Worker.request", side_effect=ExecutionError("Worker timed out")):
            failed = verification.run(self.root, "candidate")
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(verification.status(self.root)["state"], "failed")
        self.assertIsNone(verification.status(self.root)["comparison"])

    def test_running_status_hides_previous_report_and_missing_observation_fails(self):
        verification.run(self.root, "baseline")
        verification.run(self.root, "candidate")
        lock = self.root / ".adkl/verification/running"
        lock.mkdir()
        running = verification.status(self.root)
        self.assertEqual(running["state"], "running")
        self.assertIsNone(running["comparison"])
        self.assertIsNone(running["latest"])
        lock.rmdir()
        from adkl.execution import Worker
        request = Worker.request
        def incomplete(worker, *args, **kwargs):
            result = request(worker, *args, **kwargs)
            result["report"]["results"].pop()
            return result
        with patch.object(Worker, "request", incomplete):
            failed = verification.run(self.root, "candidate")
        self.assertEqual(failed["state"], "failed")
        self.assertIn("incomplete", failed["message"])

    def test_missing_metrics_and_host_exception_fail_without_exposing_exception_text(self):
        (self.root / "host_eval.py").write_text('def evaluate(**kwargs):\n    raise RuntimeError("private value")\n')
        result = verification.run(self.root, "baseline")
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("private value", json.dumps(result))
        (self.root / "host_eval.py").write_text('def evaluate(**kwargs):\n    return {"passed": True}\n')
        result = verification.run(self.root, "baseline")
        self.assertEqual(result["state"], "failed")
        self.assertNotIn("baseline", verification._read(self.root / ".adkl/verification/state.json"))

    def test_timeout_reaps_worker_and_retains_failure(self):
        self.config["timeout_seconds"] = 1
        self.configure()
        (self.root / "host_eval.py").write_text('import time\ndef evaluate(**kwargs):\n    time.sleep(20)\n')
        result = verification.run(self.root, "baseline")
        self.assertEqual(result["state"], "failed")
        self.assertIn("timed out", result["message"])
        self.assertFalse((self.root / ".adkl/verification/running").exists())

    def test_input_change_during_run_cannot_produce_current_pass(self):
        (self.root / "host_eval.py").write_text('''from pathlib import Path
def evaluate(**kwargs):
    Path("skill.md").write_text("changed during run")
    return {"passed": True, "latency_ms": 1, "cost_usd": 0}
''')
        result = verification.run(self.root, "baseline")
        self.assertEqual(result["state"], "failed")
        self.assertIn("changed during", result["message"])
        self.assertEqual(verification.status(self.root)["state"], "stale")

    def test_declared_identity_excludes_secrets_and_tracks_dependency_and_resource_changes(self):
        before = verification.snapshot(self.root)["identity"]
        (self.root / ".env").write_text("PRIVATE=synthetic")
        self.assertEqual(before, verification.snapshot(self.root)["identity"])
        (self.root / "uv.lock").write_text("different runtime")
        after = verification.snapshot(self.root)["identity"]
        self.assertNotEqual(before["artifact_sha256"], after["artifact_sha256"])
        self.assertNotEqual(before["evaluator_sha256"], after["evaluator_sha256"])

    def test_console_editable_skills_are_tracked_even_if_omitted_from_subject_files(self):
        skill = self.root / "skills/domain/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Original procedure")
        for declaration in ({"authoring": {"skills": {"domain": "skills/domain/SKILL.md"}}},
                            {"skills": {"dir": "skills", "include": ["domain"]}}):
            (self.root / "agent.yaml").write_text(yaml.safe_dump(declaration))
            before = verification.snapshot(self.root)["identity"]
            skill.write_text(skill.read_text() + "\nChanged procedure")
            after = verification.snapshot(self.root)["identity"]
            self.assertNotEqual(before["artifact_sha256"], after["artifact_sha256"])
            self.assertIn("skills/domain/SKILL.md", after["subject_files"])

    def test_remote_profile_cannot_run_locally(self):
        path = self.root / "execution.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["profiles"]["local"]["target"] = "remote"
        path.write_text(yaml.safe_dump(doc))
        from adkl.execution import ExecutionError
        with patch("adkl.execution.Worker") as worker, self.assertRaises(ExecutionError):
            verification.run(self.root, "baseline")
        worker.assert_not_called()
        self.assertEqual(verification.status(self.root)["state"], "unavailable")

    def test_duplicate_cases_invalid_policy_remote_and_path_escapes_fail_closed(self):
        for value in (["../skill.md"], [".env"], [], "skill.md"):
            self.config["subject_files"] = value
            self.configure()
            with self.subTest(value=value), self.assertRaises(ValueError):
                verification.snapshot(self.root)
        self.config["subject_files"] = ["skill.md"]
        self.config["policy"] = {"min_pass_rate": True}
        self.configure()
        with self.assertRaises(ValueError):
            verification.snapshot(self.root)
        self.config.pop("policy")
        self.configure()
        (self.root / "cases.json").write_text('{"repetitions": 1, "cases": [{"id": "x"}, {"id": "x"}]}')
        with self.assertRaisesRegex(ValueError, "unique"):
            verification.snapshot(self.root)

    def test_directory_inputs_ignore_placeholders_but_reject_private_files(self):
        folder = self.root / "skills"
        folder.mkdir()
        (folder / ".gitkeep").write_text("")
        (folder / "SKILL.md").write_text("procedure")
        self.config["subject_files"] = ["skills"]
        self.configure()
        files = verification.snapshot(self.root)["identity"]["subject_files"]
        self.assertIn("skills/SKILL.md", files)
        self.assertNotIn("skills/.gitkeep", files)
        (folder / ".env").write_text("not an evaluation input")
        with self.assertRaisesRegex(ValueError, "Private and generated"):
            verification.snapshot(self.root)

    def test_escaping_input_links_storage_links_and_concurrent_runs_are_rejected(self):
        # A link inside the project names bytes this project already declares.
        (self.root / "alias.md").symlink_to(self.root / "skill.md")
        self.config["subject_files"] = ["alias.md"]
        self.configure()
        files = verification.snapshot(self.root)["identity"]["subject_files"]
        self.assertEqual(files["alias.md"],
                         hashlib.sha256((self.root / "skill.md").read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory() as outside:
            unowned = Path(outside) / "skill.md"
            unowned.write_text("content this project does not own")
            (self.root / "alias.md").unlink()
            (self.root / "alias.md").symlink_to(unowned)
            with self.assertRaisesRegex(ValueError, "inside the project"):
                verification.snapshot(self.root)
        (self.root / ".adkl").mkdir(exist_ok=True)
        (self.root / ".adkl/generated.md").write_text("runtime output, not source")
        (self.root / "alias.md").unlink()
        (self.root / "alias.md").symlink_to(self.root / ".adkl/generated.md")
        with self.assertRaisesRegex(ValueError, "Private and generated"):
            verification.snapshot(self.root)
        self.config["subject_files"] = ["skill.md"]
        self.configure()
        folder = self.root / ".adkl/verification"
        folder.mkdir(parents=True)
        (folder / "running").mkdir()
        with self.assertRaisesRegex(ValueError, "already running"):
            verification.run(self.root, "baseline")
        (folder / "running").rmdir()
        folder.rmdir()
        folder.symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            verification.run(self.root, "baseline")
