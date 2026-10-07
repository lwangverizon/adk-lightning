#!/usr/bin/env python3
"""Tests for the tool catalog's widget-emission detection.

The console uses `emits_widget` to tell two states apart that look identical from
an empty widget list: "the agent chose not to render" and "a widget tool was
bound but nothing can execute it". Getting it wrong makes the test window lie
about the agent's behaviour, so the detection is tested rather than trusted.

The motivating case is real: on the prospect agent's eleven tools, a
`render_`/`stage_` name-prefix guess misses four -- `get_cart_summary`,
`get_self_serve_flow`, `rerank_product_cta` and `search_supports` all emit
through the staged path -- and over-reports `render_phone_deals_cta` as a direct
caller when it stages. That is why this reads the module source.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl.tool_catalog import widget_emission  # noqa: E402


class TestWidgetEmission(unittest.TestCase):
    def _mod(self, body: str) -> str:
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "tool_mod.py"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_direct_call_is_detected(self):
        m = self._mod(
            "async def render_thing(tool_context):\n"
            "    tool_context.render_ui_widget(UiWidget(payload={}))\n")
        got = widget_emission(m)
        self.assertTrue(got["value"])
        self.assertIn("render_ui_widget", got["basis"])

    def test_staged_path_is_detected_without_a_direct_call(self):
        # The case a name-prefix guess and a render_ui_widget-only grep both
        # miss. `get_cart_summary` looks like a plain getter and emits anyway.
        m = self._mod(
            "from tools.staged_widget_state import stage_cart\n"
            "async def get_cart_summary(tool_context):\n"
            "    stage_cart(tool_context, {})\n")
        got = widget_emission(m)
        self.assertTrue(got["value"])
        self.assertIn("staged", got["basis"])

    def test_framework_import_also_counts_as_staged(self):
        m = self._mod("from tools.staged_widget_framework import register\n")
        self.assertTrue(widget_emission(m)["value"])

    def test_both_signals_are_reported_together(self):
        # render_primary_action_cta is genuinely both, and the basis should say
        # so rather than picking one -- a reader deciding whether a widget should
        # have appeared at the turn boundary needs to know the staged path is
        # also in play.
        m = self._mod(
            "from tools.staged_widget_state import stage_action\n"
            "async def render_primary_action_cta(tool_context):\n"
            "    tool_context.render_ui_widget(UiWidget(payload={}))\n")
        basis = widget_emission(m)["basis"]
        self.assertIn("render_ui_widget", basis)
        self.assertIn("staged", basis)

    def test_a_plain_tool_emits_nothing(self):
        m = self._mod(
            "async def search_products(tool_context, query: str):\n"
            "    return {'results': []}\n")
        got = widget_emission(m)
        self.assertFalse(got["value"])
        self.assertIn("no widget-emitting call", got["basis"])

    def test_an_unreadable_module_is_false_and_says_why(self):
        # The catalog records absolute paths into a sibling repo. When that repo
        # is absent the answer has to be "unknown, reported as no" with a basis
        # a reader can act on -- not a crash, and not a silent False that looks
        # like evidence.
        got = widget_emission("/nonexistent/path/to/tool.py")
        self.assertFalse(got["value"])
        self.assertIn("not readable", got["basis"])

    def test_inferred_is_always_flagged(self):
        # Every field the catalog guesses at is marked inferred, because a
        # reviewer needs to know which numbers are evidence and which are
        # heuristics. This one is module-level, so it over-reports when one file
        # holds several tools and only one renders.
        m = self._mod("x = 1\n")
        self.assertTrue(widget_emission(m)["inferred"])


class TestExampleCatalogIsConsistent(unittest.TestCase):
    def test_prefix_is_not_evidence_of_widget_emission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "render_report.py"
            path.write_text("def render_report():\n    return {'status': 'ready'}\n")
            finding = widget_emission(path)
            self.assertFalse(finding["value"])
            self.assertIn("basis", finding)


if __name__ == "__main__":
    unittest.main(verbosity=0)
