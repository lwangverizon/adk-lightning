import pathlib
import tempfile
import unittest

from adkl.projects import create, doctor


class TestDoctor(unittest.TestCase):
    def test_reports_missing_environment_without_importing_host_or_reading_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = create(pathlib.Path(directory), "notes", "Summarize supplied notes")
            (root / ".env").write_text("GOOGLE_API_KEY=must-not-appear")
            result = doctor(root)
            self.assertFalse(result["readyForInspection"])
            self.assertIn("Missing uv.lock", result["problems"])
            self.assertFalse(result["hostImported"])
            self.assertNotIn("must-not-appear", str(result))
            (root / "agent.yaml").write_text("agent: [")
            self.assertIn("agent.yaml is not valid YAML", doctor(root)["problems"])
