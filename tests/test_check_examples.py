import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_examples


class TestMaintainedExamples(unittest.TestCase):
    def test_only_manifest_projects_are_checked_and_failures_propagate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "projects/user-created").mkdir(parents=True)
            manifest = root / "projects/examples.json"
            manifest.write_text(json.dumps({"projects": ["sample"]}))
            with patch.object(check_examples, "ROOT", root), patch.object(subprocess, "run") as run:
                check_examples.main()
                self.assertEqual(run.call_count, 3)
                self.assertTrue(all("sample" in str(c.args[0]) for c in run.call_args_list))
                for failed_call in (1, 2, 3):
                    run.reset_mock()
                    run.side_effect = [None] * (failed_call - 1) + [subprocess.CalledProcessError(1, "check")]
                    with self.assertRaises(subprocess.CalledProcessError):
                        check_examples.main()
