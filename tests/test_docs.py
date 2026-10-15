"""Failure and scope contracts for the offline documentation gate."""
import pathlib
import tempfile
import unittest

from scripts.ci.check_docs import check


class DocumentationCheckTests(unittest.TestCase):
    def test_valid_links_imports_and_fenced_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "docs").mkdir()
            (root / "AGENTS.md").write_text("# Shared instructions\n")
            (root / "CLAUDE.md").write_text("@AGENTS.md\n")
            (root / "docs" / "a guide.md").write_text("# Guide\n")
            (root / "README.md").write_text(
                '[guide](<docs/a guide.md>)\n[encoded](docs/a%20guide.md#heading)\n'
                '[reference]: docs/a%20guide.md "Title"\n[web](https://example.com)\n'
                '[anchor](#local)\n```markdown\n[example](missing.md)\n```\n')
            files, errors = check(root)
            self.assertEqual(len(files), 4)
            self.assertEqual(errors, [])

    def test_broken_links_and_imports_report_source_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "README.md").write_text("[broken](gone.md)\n@missing.md\n")
            _, errors = check(root)
            self.assertEqual(len(errors), 2)
            self.assertIn("README.md:1: missing target: gone.md", errors)
            self.assertIn("README.md:2: missing target: missing.md", errors)

    def test_console_readme_links_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "console").mkdir()
            (root / "console" / "README.md").write_text("[broken](missing.md)\n")
            _, errors = check(root)
            self.assertEqual(errors, ["console/README.md:1: missing target: missing.md"])

    def test_agent_text_indexes_check_links_but_ignore_unrelated_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "AGENTS.md").write_text("# Instructions\n")
            (root / "llm.txt").write_text("[index](llms.txt)\n")
            index = root / "llms.txt"
            index.write_text("[instructions](AGENTS.md)\n[contract](missing.md)\n")
            (root / "notes.txt").write_text("[not documentation](absent.md)\n")
            files, errors = check(root)
            self.assertEqual({p.name for p in files}, {"AGENTS.md", "llms.txt", "llm.txt"})
            self.assertEqual(errors, ["llms.txt:2: missing target: missing.md"])
            index.write_text("[instructions](AGENTS.md)\n")
            self.assertEqual(check(root)[1], [])

    def test_absolute_and_escaping_targets_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "README.md").write_text("[escape](../outside.md)\n[local](/tmp)\n")
            _, errors = check(root)
            self.assertEqual(len(errors), 2)
            self.assertTrue(all("must stay relative" in error for error in errors))

    def test_workflow_and_example_docs_included_user_projects_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            skill = root / ".agents" / "skills" / "example"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("[broken](missing.md)\n")
            (root / "projects").mkdir()
            (root / "projects" / "README.md").write_text("[external](missing.md)\n")
            user = root / "projects" / "user-created"
            user.mkdir()
            (user / "README.md").write_text("[private](missing.md)\n")
            files, errors = check(root)
            self.assertEqual(files, [(skill / "SKILL.md").resolve(),
                                     (root / "projects" / "README.md").resolve()])
            self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()
