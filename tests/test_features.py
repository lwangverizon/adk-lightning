#!/usr/bin/env python3
"""Tests for the feature registry.

The point of this module is that misordered or contradictory feature config
fails at load time instead of silently producing bad numbers in production. So
most of these tests assert on *errors*.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from adkl.runtime import IMPLEMENTED  # noqa: E402
from adkl.features import (  # noqa: E402
    REGISTRY,
    FeatureError,
    Kind,
    Spec,
    Stage,
    describe,
    resolve,
)


class TestDeclareThenActivate(unittest.TestCase):
    def test_declaring_does_not_activate(self):
        # The whole point of the two-phase model: config can sit in the file
        # while the feature is off, instead of being commented out.
        r = resolve({"history_slicing": {"recent_turns": 3}, "pipeline": []})
        self.assertNotIn("history_slicing", r.active)
        self.assertIn("history_slicing", r.declared)
        # ...and its config is still resolved, so turning it on needs no edit.
        self.assertEqual(r.config["history_slicing"]["recent_turns"], 3)

    def test_pipeline_order_is_the_activation_order(self):
        r = resolve({"pipeline": ["history_slicing", "tool_history_repair"]})
        self.assertEqual(r.active, ["history_slicing", "tool_history_repair"])

    def test_defaults_are_merged_under_declared_values(self):
        r = resolve({"history_slicing": {"recent_turns": 9},
                     "pipeline": ["history_slicing"]})
        cfg = r.config["history_slicing"]
        self.assertEqual(cfg["recent_turns"], 9)
        self.assertTrue(cfg["preserve_tool_pairs"])  # untouched default

    def test_omitted_pipeline_activates_only_default_on_features(self):
        r = resolve({})
        self.assertEqual(r.active, [])

    def test_enabled_false_beats_pipeline_membership(self):
        # Ops kill-switch: disable without editing the pipeline list.
        r = resolve({"history_slicing": {"enabled": False},
                     "pipeline": ["history_slicing"]})
        self.assertEqual(r.active, [])
        self.assertTrue(any("kill-switch" in w for w in r.warnings))


class TestRejections(unittest.TestCase):
    def test_pin_grace_is_a_non_negative_integer(self):
        for invalid in (-1, True, "5"):
            with self.subTest(value=invalid), self.assertRaises(FeatureError):
                resolve({"skill_retrieval": {"pin_grace_turns": invalid}})

    def test_unknown_feature_lists_the_valid_ones(self):
        with self.assertRaises(FeatureError) as cm:
            resolve({"pipeline": ["nope"]})
        self.assertIn("skill_retrieval", str(cm.exception))

    def test_unknown_option_is_an_error_not_a_silent_noop(self):
        # ADK's own plugins.yaml handler disables a feature silently when its
        # config is incomplete. That is the anti-pattern this guards against.
        with self.assertRaises(FeatureError) as cm:
            resolve({"history_slicing": {"recent_turn": 5}})
        self.assertIn("recent_turn", str(cm.exception))
        self.assertIn("recent_turns", str(cm.exception))

    def test_duplicate_pipeline_entry(self):
        with self.assertRaises(FeatureError):
            resolve({"pipeline": ["analytics", "analytics"]})

    def test_non_mapping_declaration(self):
        with self.assertRaises(FeatureError):
            resolve({"history_slicing": 5})


class TestConflictDetection(unittest.TestCase):
    def test_cache_before_slicing_is_rejected_with_a_fix(self):
        # The load-bearing case. semantic_cache keys off llm_request.contents
        # and history_slicing rewrites it, so caching first collapses the hit
        # rate -- with no exception at runtime.
        with self.assertRaises(FeatureError) as cm:
            resolve({"pipeline": ["tool_history_repair", "history_slicing"]})
        msg = str(cm.exception)
        self.assertIn("must run after", msg)
        self.assertIn("fails silently", msg)
        # The message must contain the corrected order, not just a complaint.
        self.assertIn("history_slicing, tool_history_repair", msg)

    def test_correct_order_passes(self):
        r = resolve({"pipeline": ["history_slicing", "tool_history_repair"]})
        self.assertEqual(r.active, ["history_slicing", "tool_history_repair"])

    def test_dependency_absent_from_pipeline_is_not_an_error(self):
        # Caching without slicing is legitimate -- just less effective.
        r = resolve({"pipeline": ["tool_history_repair"]})
        self.assertEqual(r.active, ["tool_history_repair"])

    def test_retired_cache_is_rejected_even_when_disabled(self):
        with self.assertRaisesRegex(FeatureError, "Retired.*semantic_cache"):
            resolve({"semantic_cache": {"enabled": False}})

    def test_write_write_clash_is_rejected(self):
        a = Spec(name="_wa", kind=Kind.BEFORE_MODEL, summary="",
                 writes=frozenset({"llm_request.contents"}))
        REGISTRY["_wa"] = a
        try:
            with self.assertRaises(FeatureError) as cm:
                resolve({"pipeline": ["history_slicing", "_wa"]})
            self.assertIn("both write", str(cm.exception))
        finally:
            REGISTRY.pop("_wa")


class TestStageDiscipline(unittest.TestCase):
    def test_non_stable_cannot_default_on(self):
        with self.assertRaises(FeatureError):
            Spec(name="_x", kind=Kind.BEFORE_MODEL, summary="",
                 stage=Stage.WIP, default_on=True)

    def test_activating_a_non_stable_feature_warns_once(self):
        r = resolve({"pipeline": ["tool_history_repair"]})
        self.assertTrue(any("experimental" in w for w in r.warnings))

    def test_unwired_analytics_does_not_default_on(self):
        # A default pipeline must not implicitly select an unwired feature.
        on = [s.name for s in REGISTRY.values() if s.default_on]
        self.assertEqual(on, [])


class TestPluginNaming(unittest.TestCase):
    def test_name_includes_a_config_hash(self):
        # ADK raises on duplicate plugin names (plugin_manager.py:118), so two
        # instances of one feature must differ.
        a = resolve({"history_slicing": {"recent_turns": 3},
                     "pipeline": ["history_slicing"]})
        b = resolve({"history_slicing": {"recent_turns": 9},
                     "pipeline": ["history_slicing"]})
        self.assertNotEqual(a.plugin_name("history_slicing"),
                            b.plugin_name("history_slicing"))
        self.assertTrue(a.plugin_name("history_slicing").startswith("history_slicing#"))


class TestDescribe(unittest.TestCase):
    def test_every_feature_is_renderable(self):
        for f in describe():
            self.assertTrue(f["name"] and f["summary"] and f["kind"])
            for opt in f["options"]:
                self.assertTrue(opt["help"], f"{f['name']}.{opt['key']} has no help")

    def test_kinds_reflect_the_real_agent(self):
        kinds = {f["name"]: f["kind"] for f in describe()}
        # Retrieval is a toolset, slicing is a
        # model callback, analytics is a runner plugin.
        self.assertEqual(kinds["skill_retrieval"], "toolset")
        self.assertEqual(kinds["history_slicing"], "before_model")
        self.assertNotIn("analytics", kinds)

    def test_before_and_after_model_are_distinct_kinds(self):
        self.assertIn("before_model", {k.value for k in Kind})
        self.assertIn("after_model", {k.value for k in Kind})



class TestDescribeIsSerializable(unittest.TestCase):
    """describe() feeds /api/agent, so a non-serializable field is fatal.

    Not a hypothetical: staged_widgets was registered with `bool` instead of
    `"bool"`, describe() emitted a Python class, /api/agent 500'd, and the console
    showed "cannot reach the adkl server" -- it loads the agent before rendering
    anything, so one bad option value took down every screen.
    """

    def test_the_whole_registry_serializes(self):
        # Guards every feature at once, including ones added later.
        json.dumps(describe())

    def test_an_option_type_must_be_a_string(self):
        with self.assertRaises(FeatureError) as cm:
            Spec(name="probe", kind=Kind.TOOLSET, summary="x",
                 options={"flag": (bool, True, "help")})
        msg = str(cm.exception)
        self.assertIn("type NAME", msg)
        self.assertIn('"bool"', msg)   # names the fix, not just the problem

    def test_every_registered_option_type_is_a_string(self):
        for spec in REGISTRY.values():
            for key, (type_name, _, _) in spec.options.items():
                self.assertIsInstance(
                    type_name, str, f"{spec.name}.{key}")

    def test_a_malformed_option_entry_is_rejected(self):
        with self.assertRaises(FeatureError):
            Spec(name="probe2", kind=Kind.TOOLSET, summary="x",
                 options={"flag": ("bool", True)})   # missing help


class TestKnownBlocks(unittest.TestCase):
    """Every top-level block must be one the library reads.

    Closes a class of bug rather than an instance: nothing validated unknown keys,
    so a typo (`feature:` for `features:`) or an aspirational block (`language:`
    before the feature exists) was silently ignored while every layer reported the
    agent as valid. Same shape as four activated features running as no-ops with
    unanimous confirmation they were on.
    """

    def test_an_unknown_block_is_an_error(self):
        import pathlib as _p
        from adkl import agent_schema
        errors, _ = agent_schema.validate(
            {"version": 1, "language": {"spanish": True}}, _p.Path("."))
        self.assertTrue(any("language" in e and "unknown" in e for e in errors))

    def test_a_near_miss_suggests_the_real_block(self):
        import pathlib as _p
        from adkl import agent_schema
        errors, _ = agent_schema.validate(
            {"version": 1, "feature": {}}, _p.Path("."))
        self.assertTrue(any("'features'" in e for e in errors))

    def test_existing_schema_blocks_remain_recognized(self):
        from adkl.agent_schema import validate
        errors, _ = validate({"version": 1, "state_pins": [], "artifacts": {}}, pathlib.Path("."))
        self.assertFalse(any("unknown top-level" in e for e in errors))

    def test_non_string_block_names_report_errors(self):
        from adkl.agent_schema import validate
        for key in (42, None):
            errors, _ = validate({"version": 1, key: {}}, pathlib.Path("."))
            self.assertTrue(any("name must be a string" in e for e in errors))

    def test_every_known_block_names_its_reader(self):
        # The map is data the check reads, so a new block has to be registered
        # here to be accepted -- and registering it means saying what consumes it.
        from adkl.agent_schema import KNOWN_BLOCKS
        for block, reader in KNOWN_BLOCKS.items():
            self.assertTrue(reader.strip(), f"{block} lists no reader")


class TestDeclaredOrderMatchesWiring(unittest.TestCase):
    """The must_run_after graph is validated at load; it is not a scheduler.

    ``build_callbacks`` appends hooks in a fixed source order rather than
    walking ``resolved.active``, so reordering ``features.pipeline`` silences
    the validator without changing execution. That is safe only while the
    source order already satisfies every declared edge. These tests are what
    keep the two from drifting -- the same job ``IMPLEMENTED`` does for support
    claims, one level down.
    """

    def _hooks(self, pipeline):
        """Hook kind -> the feature names contributing it, in wiring order."""
        from adkl.runtime import build_callbacks
        # Supply every requires_explicit_config key from the registry rather than
        # naming them here. These tests are about hook ORDER, so the values are
        # irrelevant -- but a feature that refuses to inherit a default would
        # otherwise make resolve() raise and take the whole ordering check down
        # with it. Deriving the keys means declaring another such feature never
        # needs an edit in this file, and using the registry default as the value
        # keeps the resolved config identical to what it was before.
        declared: dict = {"pipeline": list(pipeline)}
        for name in pipeline:
            spec = REGISTRY[name]
            if spec.requires_explicit_config:
                declared[name] = {key: spec.options[key][1]
                                  for key in spec.requires_explicit_config}
        resolved = resolve(declared)
        kwargs, _ = build_callbacks(
            resolved.active, resolved.config,
            widget_specs=[object()] if "staged_widgets" in pipeline else None)
        order = {}
        for kind, fns in kwargs.items():
            names = []
            for fn in fns:
                for inner in getattr(fn, "adkl_composed", [fn]):
                    names.append(getattr(inner, "adkl_features", frozenset()))
            order[kind] = names
        return order

    @staticmethod
    def _all_wired_in_declared_order():
        """Every wired feature, ordered so resolve() accepts the pipeline."""
        remaining = {n for n in REGISTRY if n in IMPLEMENTED}
        out = []
        while remaining:
            ready = sorted(n for n in remaining
                           if not (REGISTRY[n].must_run_after & remaining))
            assert ready, f"must_run_after cycle among {sorted(remaining)}"
            out.extend(ready)
            remaining -= set(ready)
        return out

    def test_every_wired_hook_declares_its_feature(self):
        # An untagged hook is invisible to the ordering check below, so it
        # would silently opt that feature out of verification.
        for kind, tags in self._hooks(self._all_wired_in_declared_order()).items():
            for i, names in enumerate(tags):
                self.assertTrue(names, f"{kind}[{i}] carries no adkl_features tag")

    def test_source_order_satisfies_every_declared_edge(self):
        for name in REGISTRY:
            if name not in IMPLEMENTED:
                continue
            for dep in sorted(REGISTRY[name].must_run_after):
                if dep not in IMPLEMENTED:
                    continue  # unsupported deps never reach the runtime
                with self.subTest(feature=name, must_run_after=dep):
                    # resolve() requires the dependency first; that is the only
                    # order it accepts, so it is the order we verify.
                    for kind, tags in self._hooks([dep, name]).items():
                        at = [i for i, s in enumerate(tags) if name in s]
                        before = [i for i, s in enumerate(tags) if dep in s]
                        if not at or not before:
                            continue  # different hook kinds: not orderable here
                        self.assertLessEqual(
                            min(before), min(at),
                            f"{kind}: {name} is wired before {dep}, but "
                            f"features.REGISTRY declares it must run after")

    def test_declared_edge_between_wired_features_is_actually_checkable(self):
        # Guards the skip conditions above: if every edge were skipped, the
        # test would pass while verifying nothing.
        checked = [(n, d) for n in REGISTRY if n in IMPLEMENTED
                   for d in REGISTRY[n].must_run_after if d in IMPLEMENTED]
        self.assertTrue(checked, "no wired must_run_after edge is being verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
