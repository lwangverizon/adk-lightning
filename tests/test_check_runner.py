"""A failure in any check must make the shell runner fail, regardless of output."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


class TestCheckExitStatus(unittest.TestCase):
    def test_each_failed_check_propagates_and_unit_suite_runs_once(self):
        repo = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            fake = root / "python"
            calls = root / "calls.jsonl"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['ADKL_TEST_CALLS'], 'a') as log:\n"
                "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "print('OK')  # A success-looking line cannot mask a failed exit.\n"
                "sys.exit(9 if sys.argv[2] == os.environ['ADKL_TEST_FAIL'] else 0)\n")
            fake.chmod(0o755)
            for module in ("pytest", "scripts.check_examples", "none"):
                with self.subTest(module=module):
                    calls.write_text("")
                    result = subprocess.run(["bash", "run_tests.sh"], cwd=repo,
                        env={**os.environ, "PYTHON": str(fake),
                             "ADKL_TEST_CALLS": str(calls), "ADKL_TEST_FAIL": module},
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0 if module == "none" else 1,
                                     result.stdout + result.stderr)
                    invocations = [json.loads(s) for s in calls.read_text().splitlines()]
                    self.assertEqual(sum(args[1] == "pytest" for args in invocations), 1)
