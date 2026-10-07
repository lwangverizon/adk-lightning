#!/usr/bin/env python3
"""Tests for the eject generator.

These run without ADK installed -- they check the *shape* of the emitted code.
The claim that it actually builds a working agent, and that its prompt matches
the compiler byte-for-byte, is verified by tests/tools/verify_eject.py against a
real ADK interpreter.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import eject  # noqa: E402

MINIMAL = (pathlib.Path(__file__).resolve().parent.parent
           / "tests" / "fixtures" / "export" / "agent.yaml")


class TestGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = eject.generate(
            MINIMAL, MINIMAL.parent / "generated" / "export_fixture.py", scaffold=True)

    def test_output_is_valid_python(self):
        ast.parse(self.code)

    def test_imports_only_the_runtime_pieces_from_this_library(self):
        # Contract change, deliberate: porting the retrieval runtime means the
        # ejected file imports adkl.retrieval rather than inlining ~600 lines
        # nobody would read. So the escape hatch is from the CONFIG, not the
        # library -- you get ordinary Python with no agent.yaml in the loop.
        # Nothing else may leak in.
        #
        # adkl.gateway joined the list for the same reason and a sharper one:
        # emitting a bare model id made the ejected agent silently use a
        # DIFFERENT backend than the one it was ejected from -- other
        # credentials, and no thinking_config forwarding. It still answered, so
        # the divergence was invisible. Constructing the model through the same
        # function the running agent uses is what makes eject a faithful copy.
        allowed = {"from adkl.retrieval import", "from adkl.gateway import"}
        for line in self.code.splitlines():
            t = line.strip()
            if t.startswith(("import adkl", "from adkl")):
                self.assertTrue(any(t.startswith(a) for a in allowed),
                                f"unexpected adkl import: {t}")

    def test_the_model_is_built_through_the_declared_provider(self):
        # A bare `model="gemini-3.6-flash"` would resolve natively and bypass
        # the provider entirely. The regression this guards is a working agent
        # that quietly talks to the wrong backend.
        self.assertIn("build_model(_settings,", self.code)
        self.assertNotIn('model="gemini', self.code)

    def test_imports_real_adk(self):
        for sym in ("from google.adk.agents import LlmAgent",
                    "from google.adk.apps import App",
                    "from google.adk.skills import load_skill_from_dir"):
            self.assertIn(sym, self.code)

    def test_says_it_is_generated_and_how_to_regenerate(self):
        self.assertIn("DO NOT EDIT", self.code)
        self.assertIn("python3 -m adkl.eject", self.code)
        # One-way; round-tripping generated code is what rots these tools, so
        # the header must not promise an import-back.
        self.assertIn("lost on the next eject", self.code)

    def test_prompt_is_embedded_not_reassembled_at_import(self):
        # Slots are substituted at eject time, so the generated file does not
        # depend on the block files still being present.
        self.assertIn("_PROMPT = ", self.code)
        self.assertNotIn("blocks/", self.code.split("_PROMPT")[1][:2000])

    def test_provider_interpolates_state_itself(self):
        # A callable instruction sets bypass_state_injection=True, so ADK will
        # not interpolate. The generated provider must do it.
        self.assertIn("def _instruction(ctx)", self.code)
        self.assertIn("_PLACEHOLDER.sub", self.code)
        self.assertIn("bypass_state_injection", self.code)

    def test_provider_matches_adk_optional_semantics(self):
        # `{key?}` renders empty when absent; a bare key raises.
        self.assertIn('endswith("?")', self.code)
        self.assertIn("Context variable not found", self.code)

    def test_invalid_state_names_pass_through_like_adk(self):
        self.assertIn("_STATE_NAME", self.code)
        self.assertIn("return m.group()", self.code)

    def test_skills_are_listed_explicitly(self):
        self.assertIn("SKILL_NAMES = ", self.code)
        self.assertIn("explain", self.code)
        self.assertIn("load_skill_from_dir", self.code)

    def test_skills_path_escapes_the_generated_subdirectory(self):
        # eject.target is generated/, but skills live beside agent.yaml. Getting
        # this wrong made the first version fail to import at all.
        self.assertIn("_ROOT = (_HERE / '..').resolve()", self.code)

    def test_active_features_appear_in_pipeline_order(self):
        i = self.code.index("SKILL_RETRIEVAL_CONFIG")
        j = self.code.index("HISTORY_SLICING_CONFIG")
        self.assertLess(i, j)

    def test_scaffold_contains_retrieval_building_blocks(self):
        self.assertIn("from adkl.retrieval import", self.code)
        self.assertIn("load_vectors", self.code)
        self.assertIn("config_from_dict", self.code)

    def test_backend_mismatch_warns_rather_than_scoring_garbage(self):
        # Cosine similarity across backends returns meaningless numbers instead
        # of failing, so the generated file has to say something.
        self.assertIn("_VECTORS.backend", self.code)
        self.assertIn("meaningless numbers", self.code)

    def test_stale_vectors_are_called_out(self):
        # Retrieval matches the OLD description until vectors are rebuilt, which
        # is silent and looks like a bad model.
        self.assertIn("Rebuild these whenever a", self.code)

    def test_the_still_unwired_part_is_stated_not_hidden(self):
        # Slicing/trimming/analytics configs are resolved but their callbacks are
        # not emitted. Say so rather than looking complete.
        self.assertIn("TODO(eject)", self.code)
        self.assertIn("yours to", self.code)

    def test_state_contract_is_documented_in_comments(self):
        self.assertIn("REQUIRED -- absent raises KeyError", self.code)


class TestPathDepth(unittest.TestCase):
    def test_target_beside_config_needs_no_hop(self):
        code = eject.generate(MINIMAL, MINIMAL.parent / "agent.py", scaffold=True)
        self.assertIn("_ROOT = (_HERE / '.').resolve()", code)

    def test_target_two_levels_down(self):
        code = eject.generate(MINIMAL, MINIMAL.parent / "a" / "b" / "agent.py", scaffold=True)
        self.assertIn("_ROOT = (_HERE / '../..').resolve()", code)


class TestExportSupport(unittest.TestCase):
    def test_rejected_export_does_not_overwrite_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "agent.py"
            target.write_text("# existing export\n")
            with patch.object(sys, "argv", ["adkl.eject", str(MINIMAL), "-o", str(target)]):
                self.assertEqual(eject.main(), 1)
            self.assertEqual(target.read_text(), "# existing export\n")

    def test_incomplete_example_is_rejected_by_default(self):
        with self.assertRaises(eject.features_mod.FeatureError) as cm:
            eject.generate(MINIMAL)
        self.assertIn("history_slicing", str(cm.exception))
        self.assertIn("skills:", str(cm.exception))

    def test_scaffold_cannot_execute_or_import_external_tools(self):
        code = eject.generate(MINIMAL, scaffold=True)
        with self.assertRaisesRegex(RuntimeError, "Export is incomplete"):
            exec(compile(code, "scaffold.py", "exec"), {})

    def test_prompt_only_agent_can_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "instruction.md").write_text("Say hello.")
            p = root / "agent.yaml"
            p.write_text("agent:\n  name: hello\n  model: fixture\n"
                         "  instruction: {template: instruction.md}\n"
                         "features: {pipeline: []}\n")
            code = eject.generate(p)
            ast.parse(code)
            self.assertNotIn("raise RuntimeError", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
