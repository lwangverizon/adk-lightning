"""The maintained native agent detects an edited skill in the final model request."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from adkl import doctor, verification


class TestNativeVerification(unittest.TestCase):
    def test_native_skill_edit_is_visible_to_real_runner_and_report(self):
        source = Path(__file__).resolve().parents[2] / "projects/native-agent"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "host"
            shutil.copytree(source, root, ignore=shutil.ignore_patterns(
                ".venv", ".adkl", "__pycache__", "vendor", "uv.lock"))
            (root / ".venv").symlink_to(sys.prefix, target_is_directory=True)
            (root / "uv.lock").write_text("synthetic fixture lock")
            baseline = verification.run(root, "baseline")
            self.assertEqual(baseline["state"], "baseline_recorded", baseline)
            self.assertTrue(baseline["comparison"]["passed"], baseline)
            self.assertEqual(verification.run(root, "candidate")["state"], "passed")
            evidence = doctor.diagnose(root)["verificationEvidence"]
            self.assertEqual(evidence["state"], "passed")
            self.assertEqual(evidence["contracts"][0]["name"], "state_isolation")
            self.assertEqual(evidence["contracts"][0]["observations"], 4)
            skill = root / "skills/display/SKILL.md"
            skill.write_text(skill.read_text().replace("acknowledge thanks without a card", "always show a card"))
            result = verification.run(root, "candidate")
            self.assertEqual(result["state"], "regressed", result)
            self.assertEqual(result["comparison"]["regressed_cases"], ["card-v09", "card-v10"])
            evidence = doctor.diagnose(root)["verificationEvidence"]
            self.assertEqual(evidence["contracts"][0]["state"], "failed")
