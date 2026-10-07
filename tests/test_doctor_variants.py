"""Same-named skill variants: a fix applied to one copy must surface as drift."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from adkl import doctor, execution
from adkl.doctor_variants import collect


def skill(root, relative, name, tools, **metadata):
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {"name": name, "description": f"{name} help",
                   "metadata": {"adk_additional_tools": tools, **metadata}}
    path.write_text("---\n" + yaml.safe_dump(frontmatter) + "---\nUse the tools.\n")
    return path


class TestSkillVariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.doc = {"version": 1, "agent": {"name": "host", "factory": "agent:build"},
                    "authoring": {"skills": {"network": "skills/network/SKILL.md"}}}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(self.doc))
        (self.root / "agent.py").write_text('raise RuntimeError("must not import")')
        tools = ["diagnose", "insights"]
        skill(self.root, "skills/network", "network", tools)
        skill(self.root, "skills/mva/network", "network", tools)
        skill(self.root, "skills/support-page-network", "support-page-network", tools,
              adkl_variant_of="network")
        # Hidden directories (worktrees, caches) are not served variants.
        skill(self.root, "skills/.cache/network", "network", ["stale"])

    def test_identical_variants_pass(self):
        raw, note = collect(self.root, self.doc)
        self.assertEqual(raw, [])
        self.assertIn("1 same-named skill families (3 SKILL.md files)", note)

    def test_partial_fix_is_a_new_finding_until_every_variant_matches(self):
        with patch.object(execution, "Worker", side_effect=AssertionError("no host import")):
            before = doctor.diagnose(self.root)
            skill(self.root, "skills/network", "network", ["diagnose"])
            partial = doctor.diagnose(self.root, previous=before)
            drift = [f for f in partial["findings"] if f["rule"] == "SKILL_VARIANT_TOOLS_DIFFER"]
            self.assertEqual(len(drift), 1)
            finding = drift[0]
            self.assertEqual(finding["severity"], "INFO")
            self.assertEqual(finding["location"], {"path": "skills/network/SKILL.md", "line": None})
            self.assertIn("insights missing from skills/network/SKILL.md", finding["message"])
            self.assertIn(finding["id"], partial["comparison"]["new"])
            skill(self.root, "skills/mva/network", "network", ["diagnose"])
            # A different remaining gap is a different finding, not "remaining".
            narrower = doctor.diagnose(self.root, previous=partial)
            changed = next(f for f in narrower["findings"] if f["rule"] == "SKILL_VARIANT_TOOLS_DIFFER")
            self.assertIn("insights only in skills/support-page-network/SKILL.md", changed["message"])
            self.assertIn(changed["id"], narrower["comparison"]["new"])
            self.assertIn(finding["id"], narrower["comparison"]["resolved"])
            skill(self.root, "skills/support-page-network", "support-page-network", ["diagnose"],
                  adkl_variant_of="network")
            fixed = doctor.diagnose(self.root, previous=narrower)
        self.assertNotIn("SKILL_VARIANT_TOOLS_DIFFER", {f["rule"] for f in fixed["findings"]})
        self.assertIn(changed["id"], fixed["comparison"]["resolved"])
        check = next(c for c in fixed["checks"] if c["id"] == "skill_variants")
        self.assertEqual(check["status"], "passed")

    def test_no_skill_roots_is_not_checked(self):
        doc = {"version": 1, "agent": {"name": "host", "factory": "agent:build"}}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(doc))
        report = doctor.diagnose(self.root)
        check = next(c for c in report["checks"] if c["id"] == "skill_variants")
        self.assertEqual(check["status"], "not_checked")

    def test_project_skill_dir_and_symlinks(self):
        doc = {"version": 1, "agent": {"name": "notes"}, "skills": {"dir": "skills"}}
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside))
        skill(outside, "network", "network", ["other"])
        (self.root / "skills/linked").symlink_to(outside / "network", target_is_directory=True)
        raw, _ = collect(self.root, doc)
        self.assertEqual(raw, [])


if __name__ == "__main__":
    unittest.main()
