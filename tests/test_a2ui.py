#!/usr/bin/env python3
"""Tests for the A2UI converter bridge.

Two halves. The first uses a fake converter registry so it runs anywhere: what
matters there is the flattening and root derivation, plus that a bad payload
returns a *finding* rather than raising -- the console's job is to explain what
happened, and an exception replaces a diagnosable widget with a stack trace.

Built-in presentation packs and injected converter fixtures require no external checkout.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import a2ui  # noqa: E402
from unittest.mock import patch




def _ops(components):
    """Wrap components the way the converters do: a list of A2UI operations."""
    return [
        {"version": "v0.9", "createSurface": {"surfaceId": "s"}},
        {"version": "v0.9",
         "updateComponents": {"surfaceId": "s", "components": components}},
    ]


class _FakeRegistry(dict):
    pass


class TestNativeOperations(unittest.TestCase):
    def widget(self, version="v0.9", text="hello"):
        return {"id": "demo", "payload": {"type": "a2ui", "operations": [
            {"version": version, "createSurface": {"surfaceId": "same", "catalogId": "basic"}},
            {"version": version, "updateComponents": {"surfaceId": "same", "components": [
                {"id": "root", "component": "Text", "text": text}]}}
        ]}}

    def test_native_versions_need_no_converter_and_do_not_share_surface_state(self):
        with patch.object(a2ui, "converters", side_effect=AssertionError("legacy registry called")):
            results = a2ui.convert_all([self.widget(), self.widget("v1.0", "second")])
        for result, version, text in zip(results, ("v0.9", "v1.0"), ("hello", "second")):
            view = result["a2ui"]
            self.assertEqual(view["version"], version)
            self.assertEqual(view["root"], "root")
            self.assertEqual(view["components"][0]["text"], text)
            self.assertEqual(view["warnings"], [])

    def test_invalid_operations_produce_findings_without_losing_later_widgets(self):
        mismatch = self.widget()
        mismatch["payload"]["operations"][1]["version"] = "v1.0"
        for bad in (None, [], {}, ["bad"], mismatch["payload"]["operations"]):
            with self.subTest(operations=bad):
                results = a2ui.convert_all([
                    {"payload": {"type": "a2ui", "operations": bad}}, self.widget()])
                self.assertIsNone(results[0]["a2ui"]["root"])
                self.assertTrue(results[0]["a2ui"]["warnings"])
                self.assertEqual(results[1]["a2ui"]["root"], "root")


def _with_registry(registry):
    """Swap the converter lookup, so these tests need no agent repo."""
    original = a2ui.converters
    a2ui.converters = lambda channel="": registry
    return original


class TestConvert(unittest.TestCase):
    def tearDown(self):
        # Restore, or a later test in the same process sees the fake.
        if hasattr(self, "_orig"):
            a2ui.converters = self._orig

    def _use(self, registry):
        self._orig = _with_registry(registry)

    def test_components_are_flattened_out_of_the_ops(self):
        # The wire format is a list of operations; a renderer wants one component
        # list. Leaving the caller to walk ops would push this into Dart.
        self._use({"T": lambda p, s: _ops([
            {"id": "root", "component": "Card", "child": "t"},
            {"id": "t", "component": "Text", "text": "hi"},
        ])})
        r = a2ui.convert({"type": "T"})
        self.assertEqual(len(r["components"]), 2)
        self.assertEqual(r["widgetType"], "T")

    def test_the_root_is_the_component_nothing_references(self):
        # Derived rather than assumed-last. The converters do emit the root last
        # today, but relying on order would break silently the first time one
        # does not.
        self._use({"T": lambda p, s: _ops([
            {"id": "leaf", "component": "Text", "text": "x"},
            {"id": "box", "component": "Card", "child": "leaf"},
        ])})
        r = a2ui.convert({"type": "T"})
        self.assertEqual(r["root"], "box")

    def test_children_lists_count_as_references(self):
        self._use({"T": lambda p, s: _ops([
            {"id": "a", "component": "Text", "text": "a"},
            {"id": "b", "component": "Text", "text": "b"},
            {"id": "col", "component": "Column", "children": ["a", "b"]},
        ])})
        self.assertEqual(a2ui.convert({"type": "T"})["root"], "col")

    def test_a_forest_reports_every_root(self):
        # Rendering only the first would silently drop UI a customer would see.
        self._use({"T": lambda p, s: _ops([
            {"id": "one", "component": "Text", "text": "1"},
            {"id": "two", "component": "Text", "text": "2"},
        ])})
        r = a2ui.convert({"type": "T"})
        self.assertEqual(sorted(r["roots"]), ["one", "two"])
        self.assertTrue(any("2 root" in w for w in r["warnings"]))

    def test_a_typeless_payload_says_production_would_drop_it(self):
        # Not merely "invalid": on the SSE path this never reaches the customer,
        # which is the fact worth surfacing.
        r = a2ui.convert({"header": "no type here"})
        self.assertEqual(r["components"], [])
        self.assertTrue(any("drop" in w for w in r["warnings"]))

    def test_an_unregistered_type_is_reported_not_raised(self):
        self._use({})
        r = a2ui.convert({"type": "Mystery_CTA"})
        self.assertEqual(r["components"], [])
        self.assertTrue(any("Mystery_CTA" in w for w in r["warnings"]))

    def test_a_raising_converter_is_reported_not_raised(self):
        def boom(p, s):
            raise ValueError("bad payload")
        self._use({"T": boom})
        r = a2ui.convert({"type": "T"})
        self.assertTrue(any("ValueError" in w for w in r["warnings"]))

    def test_composites_are_named_in_the_warnings(self):
        # So the renderer can say WHICH component it is not reproducing, rather
        # than showing a generic placeholder.
        self._use({"T": lambda p, s: _ops([
            {"id": "r", "component": "CustomChart", "series": [1, 2]},
        ])})
        r = a2ui.convert({"type": "T"})
        self.assertTrue(any("CustomChart" in w for w in r["warnings"]))

    def test_primitives_produce_no_composite_warning(self):
        self._use({"T": lambda p, s: _ops([
            {"id": "r", "component": "Card", "child": "t"},
            {"id": "t", "component": "Text", "text": "x"},
        ])})
        self.assertEqual(a2ui.convert({"type": "T"})["warnings"], [])

    def test_an_empty_conversion_is_flagged(self):
        self._use({"T": lambda p, s: _ops([])})
        r = a2ui.convert({"type": "T"})
        self.assertTrue(any("no components" in w for w in r["warnings"]))

    def test_convert_all_keeps_going_after_one_failure(self):
        # A turn can emit several widgets; losing the readable ones because a
        # later payload is malformed would hide more than it reveals.
        self._use({"Good": lambda p, s: _ops(
            [{"id": "r", "component": "Text", "text": "ok"}])})
        out = a2ui.convert_all([
            {"id": "w1", "payload": {"type": "Good"}},
            {"id": "w2", "payload": {"type": "Missing"}},
            {"id": "w3", "payload": {"type": "Good"}},
        ])
        self.assertEqual(len(out), 3)
        self.assertEqual(len(out[0]["a2ui"]["components"]), 1)
        self.assertEqual(out[1]["a2ui"]["components"], [])
        self.assertEqual(len(out[2]["a2ui"]["components"]), 1)

    def test_convert_all_preserves_the_original_widget_fields(self):
        self._use({"Good": lambda p, s: _ops(
            [{"id": "r", "component": "Text", "text": "ok"}])})
        out = a2ui.convert_all(
            [{"id": "w1", "provider": "adkl", "payload": {"type": "Good"}}])
        self.assertEqual(out[0]["provider"], "adkl")
        self.assertEqual(out[0]["id"], "w1")


class TestPresentationPrimitiveConverters(unittest.TestCase):
    """Built-in presentation packs need no application converters."""

    def convert(self, payload):
        from adkl.presentation import PACKS
        return a2ui.convert_all([{"payload": payload}],
                               builtin_kinds=PACKS["adkl/tutoring@1"])[0]["a2ui"]

    def test_all_emitted_card_types_use_only_flutter_primitives(self):
        cases = {
            "quiz": {"question": "What is 2 + 3?", "options": [
                {"label": "A", "text": "4"}, {"label": "B", "text": "5"}]},
            "fact": {"icon": "✨", "title": "Math fact", "text": "Two plus three is five."},
            "step": {"steps": ["Count two", "Count three"]},
            "celebration": {"message": "You got it!"},
            "greeting": {"greeting": "Hi", "subtitle": "Let's learn."},
            "cpa_lesson": {"topic": "Addition", "standard": "3.OA.A.1", "stages": [
                {"stage": stage, "explanation": "Use blocks.", "visual": "🧱🧱"}
                for stage in ("concrete", "pictorial", "abstract")]},
        }
        for widget_type, payload in cases.items():
            result = self.convert({"type": widget_type, **payload})
            self.assertEqual(result["warnings"], [], widget_type)
            self.assertTrue(result["root"], widget_type)
            self.assertTrue(result["components"], widget_type)
            self.assertTrue(
                {component["component"] for component in result["components"]}
                <= a2ui.PRIMITIVES,
                widget_type,
            )

    def test_quiz_copy_and_answer_events_survive_conversion(self):
        result = self.convert({
            "type": "quiz", "question": "What is 2 + 3?", "options": [
                {"label": "A", "text": "4"}, {"label": "B", "text": "5"}],
        })
        copy = " ".join(str(component.get("text", ""))
                        for component in result["components"])
        self.assertIn("What is 2 + 3?", copy)
        self.assertIn("B. 5", copy)
        events = [component.get("action", {}).get("event", {}).get("name")
                  for component in result["components"]]
        self.assertIn("answer_quiz", events)


if __name__ == "__main__":
    unittest.main(verbosity=0)
