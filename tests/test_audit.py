"""Audit must inspect selected prompt blocks and resolve catalog tool names."""
import pathlib
import tempfile
import unittest

import yaml

from adkl.audit import audit
from adkl import features


class TestAudit(unittest.TestCase):
    def test_selected_prompt_and_undeclared_tool_are_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "prompt.md").write_text("{POLICY}")
            (root / "selected.md").write_text("NEVER show an automatic refund approval.")
            (root / "unused.md").write_text("Never fetch product details.")
            directory = root / "skills" / "refund"
            directory.mkdir(parents=True)
            (directory / "SKILL.md").write_text(
                "---\nname: refund\ndescription: Help request refunds.\n---\n"
                "Call `fetch_product_details`. Show an automatic refund approval.\n")
            (root / "catalog.json").write_text('{"tools": [{"name": "fetch_product_details"}]}')
            (root / "agent.yaml").write_text(yaml.safe_dump({
                "version": 1, "agent": {"name": "fixture", "model": "fixture", "provider": "gemini",
                    "instruction": {"template": "prompt.md", "slots": {"POLICY": "selected.md"}}},
                "skills": {"include": ["refund"]}, "tools": {"catalog": "catalog.json"},
                "eject": {"target": "generated.py"}}))
            report = audit(root)
            rules = {f["rule"] for f in report["findings"]}
            self.assertIn("TOOL_USED_NOT_DECLARED", rules)
            self.assertIn("PROMPT_PROHIBITION_CONTRADICTED", rules)
            self.assertEqual(report["evidence"], "static")
            self.assertEqual(report["behavioralEvaluation"], "not_run")
            before = report["sourceDigest"]
            (root / "selected.md").write_text("Help with refund requests.")
            self.assertNotEqual(before, audit(root)["sourceDigest"])

    def test_universal_prompt_tool_requires_explicit_core_binding(self):
        from adkl.lint_conflict import check_prompt_tool_scope
        skills = [("math", "Teach math", ["curriculum"]), ("practice", "Practice", ["save_progress"])]
        catalog = {"tools": [{"name": "save_progress"}]}
        prompt = "Call `save_progress` after every lesson."
        findings = check_prompt_tool_scope(prompt, skills, catalog)
        self.assertEqual(findings[0][1], "PROMPT_TOOL_SKILL_SCOPED")
        self.assertEqual(check_prompt_tool_scope(prompt, skills, catalog, ["save_progress"]), [])

    def test_invalid_state_contracts_rejected_without_adk(self):
        invalid = [
            {"state_pins": [{"skill": "math"}]},
            {"state_pins": [{"skill": "math", "state_key": "flow", "active_when": "sometimes"}]},
        ]
        for cfg in invalid:
            with self.assertRaises(features.FeatureError):
                features.resolve({"skill_retrieval": cfg})
        for field in ({"from_state": "x", "required": "yes"}, {"from_state": "x", "type": "secret"}):
            with self.assertRaises(features.FeatureError):
                features.resolve({"skill_state_injection": {"skills": {"math": {"topic": field}}}})
