#!/usr/bin/env python3
"""The layering rule that makes credential-free CI possible, enforced.

`pyproject.toml` keeps the base dependency list at one entry (PyYAML) and puts
ADK/LiteLLM in the `runtime` extra, so `components` and `python-lint` can run
without the ADK wheel. That only holds while importing an adkl module does not
drag ADK in transitively.

Until now the rule lived in comments and deferred `import` statements inside
functions. Nothing checked it, and a single new module-level `from google.adk
import ...` in a widely-imported module would have moved the whole package into
the runtime extra silently -- the failure would surface as a CI install error in
an unrelated change, not as a review comment on the import that caused it.

So: import every module in a subprocess with ADK, google.genai and LiteLLM made
unimportable. Everything must succeed except the modules named in
RUNTIME_ONLY_MODULES. Adding a module to that set is the deliberate act; doing
it by accident is what this catches.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that legitimately need ADK at import time: they subclass or wrap ADK
# types, so there is nothing to defer. Every other adkl module must import with
# ADK absent. Keep this set as small as it is -- each entry is a module that
# cannot be reached by the authoring CLIs, lint or schema validation.
RUNTIME_ONLY_MODULES = {
    "adkl.presentation_runtime",   # PresentationSkillToolset(SkillToolset)
    "adkl.skill_runtime",          # SelectedSkillToolset(BaseToolset)
    "adkl.staged_tool",            # FunctionTool wrapper
}

# Import roots refused inside the subprocess. google.genai is listed separately
# from google.adk because ADK re-exports its types and a module could reach the
# SDK through either path.
BLOCKED_ROOTS = ("google.adk", "google.genai", "litellm")

PROBE = '''
import importlib, sys
from types import ModuleType

BLOCKED = %(blocked)r

# Resolve the parent namespace even in the real lightweight lane. Otherwise
# Python fails on `google` before our finder can reject `google.adk`, incorrectly
# classifying the three declared runtime modules as unrelated import failures.
# Other missing google children still fail normally; no runtime package is faked.
google_namespace = ModuleType("google")
google_namespace.__path__ = []
sys.modules["google"] = google_namespace

class _Blocker:
    """Refuse the runtime extra so a deferred import cannot silently succeed."""
    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in BLOCKED):
            raise ImportError("blocked by import-layering test: " + name)
        return None

sys.path.insert(0, %(root)r)
sys.meta_path.insert(0, _Blocker())

clean, needs_runtime, broken = [], [], []
for name in %(modules)r:
    try:
        importlib.import_module(name)
        clean.append(name)
    except ImportError as exc:
        (needs_runtime if "blocked by import-layering test" in str(exc)
         else broken).append((name, str(exc)))
    except Exception as exc:
        broken.append((name, type(exc).__name__ + ": " + str(exc)))

print("CLEAN", *sorted(clean), sep="\\n")
print("RUNTIME", *sorted(n for n, _ in needs_runtime), sep="\\n")
for name, detail in broken:
    print("BROKEN", name, detail)
'''


def adkl_modules():
    """Every importable module in the package, as dotted names."""
    names = []
    for path in sorted(ROOT.joinpath("adkl").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        dotted = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        names.append(dotted.removesuffix(".__init__"))
    return names


class TestImportLayering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        modules = adkl_modules()
        source = PROBE % {"blocked": list(BLOCKED_ROOTS),
                          "root": str(ROOT), "modules": modules}
        result = subprocess.run([sys.executable, "-c", source],
                                capture_output=True, text=True, timeout=180,
                                cwd=str(ROOT))
        if result.returncode != 0:
            raise AssertionError("probe failed:\n" + result.stderr[-2000:])
        cls.modules = modules
        cls.clean, cls.runtime, cls.broken = set(), set(), []
        section = None
        for line in result.stdout.splitlines():
            if line in ("CLEAN", "RUNTIME"):
                section = line
            elif line.startswith("BROKEN "):
                cls.broken.append(line[7:])
            elif line and section == "CLEAN":
                cls.clean.add(line)
            elif line and section == "RUNTIME":
                cls.runtime.add(line)

    def test_probe_actually_imported_the_package(self):
        # Guards every assertion below: an empty result would pass them all.
        self.assertGreater(len(self.modules), 40)
        self.assertIn("adkl.features", self.clean)
        self.assertIn("adkl.skill_lint", self.clean)

    def test_no_module_fails_for_an_unrelated_reason(self):
        self.assertEqual([], self.broken)

    def test_only_declared_modules_require_the_runtime_extra(self):
        undeclared = self.runtime - RUNTIME_ONLY_MODULES
        self.assertEqual(
            set(), undeclared,
            "these modules gained an import-time ADK/LiteLLM dependency, which "
            "moves them out of the base install. Defer the import into the "
            "function that needs it, or add the module to "
            "RUNTIME_ONLY_MODULES and say why: " + ", ".join(sorted(undeclared)))

    def test_declared_runtime_modules_still_need_the_runtime_extra(self):
        # The other direction: once a module no longer needs ADK at import
        # time, drop it from the set rather than leaving a stale exemption.
        stale = RUNTIME_ONLY_MODULES & self.clean
        self.assertEqual(
            set(), stale,
            "these no longer import ADK at module level; remove them from "
            "RUNTIME_ONLY_MODULES: " + ", ".join(sorted(stale)))

    def test_authoring_entry_points_run_without_the_runtime_extra(self):
        # The documented credential-free CLIs, specifically. These are what
        # `components` and a host's own lint job invoke.
        for name in ("adkl.skill_lint", "adkl.agent_schema", "adkl.skill_doc",
                     "adkl.tool_catalog", "adkl.audit", "adkl.explain",
                     "adkl.instruction", "adkl.embed", "adkl.features",
                     "adkl.capabilities", "adkl.widget_lint"):
            with self.subTest(module=name):
                self.assertIn(name, self.clean)


if __name__ == "__main__":
    unittest.main(verbosity=2)
