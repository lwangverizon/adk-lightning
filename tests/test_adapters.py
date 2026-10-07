"""Hermetic contracts for incremental host adoption and model-facing projection."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest

from adkl.adapters import build_callback_adapter
from adkl import capabilities, features
from adkl.projection import make_result_projector


class TestProjection(unittest.TestCase):
    def setUp(self):
        self.rules = {"lookup": {"path": ["data", "rows"],
                                  "fields": ["id", "title", "eligible", "cost", "extra"],
                                  "max_string_chars": {"title": 4}}}

    def test_preserves_rows_envelope_falsy_values_and_source(self):
        source = {"status": "ok", "agent_instruction": "Keep this guidance",
                  "data": {"next_page": "cursor", "rows": [
                      {"id": "a", "title": "long title", "eligible": False,
                       "cost": 0, "extra": None, "unused": "bulk"},
                      {"id": "b", "title": "", "extra": []}]}}
        before = deepcopy(source)
        findings = []
        project = make_result_projector(self.rules, findings.extend)
        result = project(SimpleNamespace(name="lookup"), {}, None, source)
        self.assertEqual(source, before)
        self.assertEqual(result["data"]["next_page"], "cursor")
        self.assertEqual(result["agent_instruction"], source["agent_instruction"])
        self.assertEqual(result["data"]["rows"], [
            {"id": "a", "title": "long…", "eligible": False, "cost": 0, "extra": None},
            {"id": "b", "title": "", "extra": []}])
        self.assertIn("2 rows retained", findings[0])
        self.assertEqual(project(SimpleNamespace(name="lookup"), {}, None, result), result)

    def test_top_level_lists_and_empty_lists_are_replacements(self):
        fn = make_result_projector({"lookup": {"path": [], "fields": ["id"]}})
        self.assertEqual(fn(SimpleNamespace(name="lookup"), {}, None, []), [])
        self.assertEqual(fn(SimpleNamespace(name="lookup"), {}, None,
                            [{"id": 0, "secret": 1}]), [{"id": 0}])

    def test_unknown_and_changed_shapes_pass_through(self):
        findings = []
        fn = make_result_projector(self.rules, findings.extend)
        for source in ({"error": "offline"}, {"data": {"rows": [{"id": 1}, None]}}, None):
            self.assertIsNone(fn(SimpleNamespace(name="lookup"), {}, None, source))
        self.assertEqual(len(findings), 3)
        self.assertIsNone(fn(SimpleNamespace(name="unwatched"), {}, None, {}))
        self.assertEqual(len(findings), 3)

    def test_rules_are_snapshotted(self):
        fn = make_result_projector(self.rules)
        self.rules["lookup"]["fields"].clear()
        result = fn(SimpleNamespace(name="lookup"), {}, None,
                    {"data": {"rows": [{"id": 1}]}})
        self.assertEqual(result["data"]["rows"], [{"id": 1}])

    def test_a_shape_mismatch_is_recorded_in_state_without_an_on_finding_channel(self):
        # RuntimeBindings has no `on_finding` wiring at all
        # (agent_runtime._record_output_findings has no RuntimeBindings
        # counterpart), so this state note is the only channel that host has.
        project = make_result_projector(self.rules)
        ctx = SimpleNamespace(state={})
        out = project(SimpleNamespace(name="lookup"), {}, ctx,
                      {"cols": [], "rows": []})
        self.assertIsNone(out)   # pass-through is deliberate
        self.assertEqual(ctx.state["temp:adkl_projection_findings"], ["lookup"])

    def test_a_missing_tool_context_is_not_a_failure(self):
        project = make_result_projector(self.rules)
        self.assertIsNone(project(SimpleNamespace(name="lookup"), {}, None,
                                  {"cols": []}))

    def test_malformed_rules_rejected_by_shared_config(self):
        for rule in ({}, {"path": [], "fields": []},
                     {"path": "rows", "fields": ["id"]},
                     {"path": [], "fields": ["id", "id"]},
                     {"path": [], "fields": ["id"], "max_string_chars": {"id": True}},
                     {"path": [], "fields": ["id"], "max_string_chars": {"other": 2}},
                     {"path": [], "fields": ["id"], "unknown": 1}):
            with self.subTest(rule=rule), self.assertRaises(features.FeatureError):
                features.resolve({"tool_result_projection": {"tools": {"lookup": rule}}})


class TestAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_raw_observers_then_latest_transforms_including_empty_result(self):
        seen = []
        async def observer(**kwargs):
            seen.append(("raw", kwargs["tool_response"]))
        async def empty(**kwargs):
            seen.append(("first", kwargs["tool_response"]))
            return {}
        def next_transform(**kwargs):
            seen.append(("second", kwargs["tool_response"]))
            return {"items": []}
        hooks = build_callback_adapter({"pipeline": []},
            after_tool_observers=[observer], after_tool_transforms=[empty, next_transform])
        result = await hooks["after_tool_callback"][0](None, {}, None, {"raw": 1})
        self.assertEqual(result, {"items": []})
        self.assertEqual(seen, [("raw", {"raw": 1}), ("first", {"raw": 1}), ("second", {})])

    async def test_observer_replacement_is_error_and_cancellation_propagates(self):
        def bad(**kwargs):
            return {}
        async def cancel(**kwargs):
            raise asyncio.CancelledError()
        for observer, error in ((bad, TypeError), (cancel, asyncio.CancelledError)):
            hook = build_callback_adapter({"pipeline": []}, after_tool_observers=[observer])
            with self.assertRaises(error):
                await hook["after_tool_callback"][0](None, {}, None, {})

    async def test_host_guard_and_hooks_are_preserved_without_list_aliasing(self):
        def guard(**kwargs):
            return {"status": "refused"}
        def model(**kwargs):
            return None
        original = [model]
        hooks = build_callback_adapter({"pipeline": ["tool_history_repair"]},
                                       before_model=original, before_tool=[guard])
        self.assertIs(hooks["before_model_callback"][0], model)
        self.assertIs(hooks["before_tool_callback"][0], guard)
        hooks["before_model_callback"].clear()
        self.assertEqual(original, [model])

    async def test_projection_composes_after_host_slimming(self):
        def host(**kwargs):
            return {"rows": [{"id": 1, "bulk": "removed"}], "status": "ok"}
        cfg = {"pipeline": ["tool_result_projection"], "tool_result_projection": {
            "tools": {"lookup": {"path": ["rows"], "fields": ["id"]}}}}
        hook = build_callback_adapter(cfg, after_tool_transforms=[host])
        result = await hook["after_tool_callback"][0](SimpleNamespace(name="lookup"), {}, None, {})
        self.assertEqual(result, {"rows": [{"id": 1}], "status": "ok"})

    async def test_unsupported_mechanisms_fail_instead_of_silent_noop(self):
        for name in ("identity_gate", "semantic_cache", "skill_retrieval",
                     "skill_state_injection", "staged_widgets", "output_cleanup"):
            with self.subTest(name=name), self.assertRaises(features.FeatureError):
                build_callback_adapter({"pipeline": [name]})

    async def test_order_validation_and_truthful_export(self):
        # max_chars is supplied so the ONLY thing wrong with the second pipeline
        # is its ORDER. tool_result_trimming refuses to inherit its registry
        # default, and resolve() accumulates that error alongside the ordering
        # one -- so without this the assertion below would still pass while
        # proving nothing about ordering, which is the whole point of the test.
        for declared in ({"pipeline": ["tool_history_repair", "history_slicing"]},
                         {"pipeline": ["tool_result_trimming", "tool_result_projection"],
                          "tool_result_trimming": {"max_chars": 4000}}):
            with self.assertRaises(features.FeatureError) as cm:
                build_callback_adapter(declared)
            self.assertIn("must run after", str(cm.exception))
        resolved = features.resolve({"pipeline": ["tool_history_repair", "tool_result_projection"]})
        self.assertTrue(capabilities.manifest(resolved, "sdk")["canRun"])
        self.assertFalse(capabilities.manifest(resolved, "export")["canRun"])


if __name__ == "__main__":
    unittest.main()
