#!/usr/bin/env python3
"""Tests for importing real tool callables out of a sibling agent repo.

Built against a synthetic repo, so these run anywhere. The
one test that touches a real checkout skips when it is absent.

The behaviour worth protecting is mostly about *not* failing loudly: adk-lightning
has to stay usable with no sibling repo at all, and a single broken tool module
must not cost the whole session. Both were regressions waiting to happen -- the
whole feature exists because I previously assumed tools could not run and built
around that, so the fallbacks matter as much as the happy path.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import toolbind  # noqa: E402



def _purge(prefix: str = "tools") -> None:
    """Drop cached `tools.*` modules between tests.

    Python caches packages by NAME, so once `tools` resolves to one repo every
    later `import tools.x` is served from that repo regardless of sys.path. Real
    constraint, not a test artifact: a process can only bind one agent repo, and
    `toolbind` documents that. Tests each build a fresh temp repo, so they have
    to clear the cache or the second one silently reads the first.
    """
    for name in [m for m in sys.modules
                 if m == prefix or m.startswith(prefix + ".")]:
        del sys.modules[name]


class _Repo:
    """A minimal stand-in for an agent repo: a .env and a tools package."""

    def __init__(self):
        self.path = pathlib.Path(tempfile.mkdtemp())
        (self.path / "tools").mkdir()
        (self.path / "tools" / "__init__.py").write_text("")

    def env(self, body: str):
        (self.path / ".env").write_text(body, encoding="utf-8")
        return self

    def tool(self, name: str, body: str | None = None):
        default = (f"def {name}(tool_context, q: str = ''):\n"
                   f"    return {{'status': 'ok', 'name': '{name}'}}\n")
        (self.path / "tools" / f"{name}.py").write_text(
            body if body is not None else default, encoding="utf-8")
        return self

    def catalog(self, *names):
        return {"tools": [
            {"name": n, "module": str(self.path / "tools" / f"{n}.py")}
            for n in names]}

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class TestPrepareImports(unittest.TestCase):
    def test_project_package_wins_over_script_module_without_loading_env(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "a2ui").mkdir()
            (root / "a2ui" / "__init__.py").write_text("ORIGIN = 'project'\n")
            (root / ".env").write_text("ADKL_TEST_SHOULD_NOT_LOAD=secret\n")
            script = """
import os, sys
from pathlib import Path
from adkl import toolbind
sys.path.insert(0, str(Path(toolbind.__file__).resolve().parent))
toolbind.prepare(Path(sys.argv[1]))
import a2ui
assert a2ui.ORIGIN == 'project'
assert 'ADKL_TEST_SHOULD_NOT_LOAD' not in os.environ
"""
            subprocess.run([sys.executable, "-c", script, str(root)], check=True,
                           cwd=pathlib.Path(__file__).resolve().parent.parent)


class TestLoadEnv(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        self.repo.cleanup()

    def test_values_are_loaded(self):
        self.repo.env("ADKL_TEST_PROJECT=ienv-test\nADKL_TEST_ENV=UAT\n")
        applied = toolbind.load_env(self.repo.path)
        self.assertIn("ADKL_TEST_PROJECT", applied)
        self.assertEqual(os.environ["ADKL_TEST_PROJECT"], "ienv-test")

    def test_quotes_are_stripped(self):
        # The source .env quotes inconsistently -- some values quoted, some bare.
        # A tool comparing ENV == "UAT" fails if the quotes survive.
        self.repo.env('ADKL_TEST_Q="quoted"\nADKL_TEST_B=bare\n')
        toolbind.load_env(self.repo.path)
        self.assertEqual(os.environ["ADKL_TEST_Q"], "quoted")
        self.assertEqual(os.environ["ADKL_TEST_B"], "bare")

    def test_existing_os_env_wins(self):
        # Preserve existing process settings so an operator can override one
        # var without editing the sibling repo's file.
        os.environ["ADKL_TEST_KEEP"] = "from-os"
        self.repo.env("ADKL_TEST_KEEP=from-file\n")
        applied = toolbind.load_env(self.repo.path)
        self.assertEqual(os.environ["ADKL_TEST_KEEP"], "from-os")
        self.assertNotIn("ADKL_TEST_KEEP", applied)

    def test_comments_and_blank_lines_are_skipped(self):
        self.repo.env("# a comment\n\nADKL_TEST_OK=1\n")
        applied = toolbind.load_env(self.repo.path)
        self.assertEqual(applied, ["ADKL_TEST_OK"])

    def test_a_missing_env_is_reported_but_not_fatal(self):
        # A host might require GCP_PROJECT at import, but that is host behavior,
        # not a reason for the SDK to reject tools that need no environment file.
        # while nothing was actually wrong.
        self.assertIsNone(toolbind.load_env(self.repo.path))

    def test_the_symptom_is_still_named_when_imports_fail(self):
        # The message has to survive the downgrade: naming
        # KeyError/GCP_PROJECT is what stops "no .env" reading as cosmetic
        # rather than the reason every tool import died.
        catalog = {"tools": [{"name": "nope", "module": "does_not_exist.py"}]}
        _fns, problems = toolbind.load(self.repo.path, catalog)
        self.assertTrue(problems)
        self.assertIn("no .env", problems[0])
        self.assertIn("GCP_PROJECT", problems[0])

    def test_no_env_note_when_every_tool_imported(self):
        _fns, problems = toolbind.load(self.repo.path, {"tools": []})
        self.assertEqual(problems, [])


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo().env("ADKL_TEST_X=1\n")
        self._saved = dict(os.environ)
        self._cwd = pathlib.Path.cwd()
        _purge()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)
        os.chdir(self._cwd)
        self.repo.cleanup()
        _purge()

    def test_a_tool_resolves_to_a_callable(self):
        self.repo.tool("search_things")
        fns, problems = toolbind.load(
            self.repo.path, self.repo.catalog("search_things"))
        self.assertEqual(problems, [])
        self.assertTrue(callable(fns["search_things"]))
        self.assertEqual(fns["search_things"](None)["name"], "search_things")

    def test_cwd_is_restored_after_loading(self):
        # load() chdirs into the repo because some modules read data files by
        # relative path at import. Leaking that would break every later relative
        # path in the server process.
        self.repo.tool("t_one")
        toolbind.load(self.repo.path, self.repo.catalog("t_one"))
        self.assertEqual(pathlib.Path.cwd(), self._cwd)

    def test_cwd_is_restored_even_when_a_tool_raises(self):
        self.repo.tool("t_bad", "raise RuntimeError('boom at import')\n")
        toolbind.load(self.repo.path, self.repo.catalog("t_bad"))
        self.assertEqual(pathlib.Path.cwd(), self._cwd)

    def test_one_broken_module_does_not_lose_the_others(self):
        # A version skew in the sibling repo should cost that one tool, not the
        # session. This happened for real: langchain 1.3.14 has no TracePolicy,
        # so one prospect tool cannot import while the other ten are fine.
        self.repo.tool("t_ok").tool(
            "t_broken", "from nonexistent_pkg import Thing\n")
        fns, problems = toolbind.load(
            self.repo.path, self.repo.catalog("t_ok", "t_broken"))
        self.assertIn("t_ok", fns)
        self.assertNotIn("t_broken", fns)
        self.assertEqual(len(problems), 1)
        self.assertIn("t_broken", problems[0])

    def test_a_missing_symbol_names_the_module_searched(self):
        # The registration name can differ from the symbol, so "not found" alone
        # would leave a developer guessing where to look.
        self.repo.tool("t_wrong", "def some_other_name(ctx):\n    return {}\n")
        fns, problems = toolbind.load(
            self.repo.path, self.repo.catalog("t_wrong"))
        self.assertEqual(fns, {})
        self.assertIn("tools.t_wrong", problems[0])

    def test_names_filter_binds_only_what_was_asked_for(self):
        self.repo.tool("t_a").tool("t_b")
        fns, _ = toolbind.load(
            self.repo.path, self.repo.catalog("t_a", "t_b"), names=["t_a"])
        self.assertEqual(sorted(fns), ["t_a"])

    def test_a_catalog_from_another_checkout_is_rejected_clearly(self):
        self.repo.tool("t_a")
        catalog = {"tools": [{"name": "t_a", "module": "/elsewhere/tools/t_a.py"}]}
        _, problems = toolbind.load(self.repo.path, catalog)
        self.assertIn("different checkout", problems[0])

    def test_a_missing_repo_is_an_error_not_a_silent_empty(self):
        with self.assertRaises(toolbind.ToolBindError):
            toolbind.load(pathlib.Path("/no/such/repo"), {"tools": []})


if __name__ == "__main__":
    unittest.main(verbosity=0)
