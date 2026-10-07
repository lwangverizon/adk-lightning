#!/usr/bin/env python3
"""Tests for the guards that stop feature config from lying about itself.

Every test here covers a way the registry used to agree with a host that was
wrong. The three shapes, in the order they bite:

* A `pipeline` entry means "activate on registry defaults". That is correct for
  a token trim and wrong for a row filter, so `requires_explicit_config` lets a
  feature refuse to inherit a default that changes the DATA the model sees.
* Nothing checked a declared option's TYPE, so `recent_turns: "5"` reached the
  callback factory unconverted and `recent_turns: true` sliced history to one
  turn -- `isinstance(True, int)` being True.
* A wiring call site that writes its own literal fallback makes `describe()`
  lie: the console renders the registry default while the agent runs the
  literal. Two call sites did exactly that.

This module must import NO ADK. The `components` CI lane runs `uv sync --locked`
WITHOUT `--extra runtime`, so google.adk is not installed there and an import
would be a collection error, not a skip. Anything needing ADK belongs in
tests/runtime/ instead -- see tests/test_import_layering.py for the rule.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tokenize
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import features  # noqa: E402
from adkl.features import (  # noqa: E402
    REGISTRY,
    FeatureError,
    Kind,
    Spec,
    resolve,
)


class TestRequiresExplicitConfig(unittest.TestCase):
    """A feature may refuse to inherit a default that changes DATA, not tuning."""

    def test_an_active_feature_must_configure_the_key_it_refuses_to_inherit(self):
        with self.assertRaises(FeatureError) as cm:
            resolve({"pipeline": ["tool_result_trimming"]})
        msg = str(cm.exception)
        self.assertIn("max_chars", msg)
        # The error has to be actionable in three specific ways, because the
        # host reading it has no other way to find out what to write down.
        self.assertIn("4000", msg)                  # quotes the registry default
        self.assertIn("valid answer", msg)          # says copying it is allowed
        self.assertIn("remove tool_result_trimming", msg)   # names the other exit

    def test_writing_the_registry_default_down_is_accepted(self):
        # The requirement is CONSENT, not a different value. If this failed, the
        # only way to satisfy the guard would be to detune the feature.
        r = resolve({"pipeline": ["tool_result_trimming"],
                     "tool_result_trimming": {"max_chars": 4000}})
        self.assertIn("tool_result_trimming", r.active)
        self.assertEqual(r.config["tool_result_trimming"]["max_chars"], 4000)

    def test_declaring_without_activating_is_exempt(self):
        # Declaring is not activating, so an unconfigured block that nothing
        # runs is not a lie about anything.
        r = resolve({"tool_result_trimming": {}})
        self.assertNotIn("tool_result_trimming", r.active)

    def test_the_kill_switch_exempts_it_too(self):
        # enabled: false removes it from `active`, and the requirement is scoped
        # to active features. An ops kill-switch must not need a tuning value.
        r = resolve({"pipeline": ["tool_result_trimming"],
                     "tool_result_trimming": {"enabled": False}})
        self.assertEqual(r.active, [])

    def test_the_error_accumulates_with_an_ordering_error(self):
        # resolve() collects errors and raises them together. If this one
        # short-circuited, fixing the order would reveal a second failure the
        # host had no warning about -- and vice versa.
        with self.assertRaises(FeatureError) as cm:
            resolve({"pipeline": ["tool_result_trimming", "tool_result_projection"]})
        msg = str(cm.exception)
        self.assertIn("does not configure max_chars", msg)
        self.assertIn("must run after", msg)

    def test_every_declared_key_exists_as_an_option(self):
        # A typo here disarms the guard SILENTLY: an option key that does not
        # exist can never be reported missing, so the feature quietly goes back
        # to activating on defaults. Spec.__post_init__ rejects it at import,
        # and this covers specs added later.
        for name, spec in REGISTRY.items():
            phantom = set(spec.requires_explicit_config) - set(spec.options)
            self.assertEqual(phantom, set(), f"{name} names non-existent option(s)")

    def test_a_phantom_key_is_rejected_at_construction(self):
        with self.assertRaises(FeatureError) as cm:
            Spec(name="_phantom_probe", kind=Kind.TOOLSET, summary="x",
                 requires_explicit_config=frozenset({"nope"}),
                 options={"real": ("int", 1, "help")})
        self.assertIn("nope", str(cm.exception))
        self.assertIn("do not exist", str(cm.exception))


class TestDeclaredTypes(unittest.TestCase):
    """An option's declared type is checked at load, not coerced at the call site."""

    def test_a_string_for_an_int_option_is_rejected(self):
        with self.assertRaises(FeatureError) as cm:
            resolve({"history_slicing": {"recent_turns": "5"}})
        msg = str(cm.exception)
        self.assertIn("must be int", msg)
        self.assertIn("str", msg)      # names what arrived, not just what was wanted

    def test_a_bool_is_not_an_int(self):
        # The load-bearing exclusion: isinstance(True, int) is True, so without
        # it `recent_turns: true` slices the conversation to ONE turn while
        # reading like a feature toggle.
        with self.assertRaises(FeatureError) as cm:
            resolve({"history_slicing": {"recent_turns": True}})
        self.assertIn("bool", str(cm.exception))

    def test_a_bool_is_not_a_float_either(self):
        with self.assertRaises(FeatureError):
            resolve({"skill_retrieval": {"min_score": True}})

    def test_an_int_is_accepted_for_a_float_option(self):
        # 1 for a threshold is not a type error; requiring 1.0 would be noise.
        r = resolve({"skill_retrieval": {"min_score": 1}})
        self.assertEqual(r.config["skill_retrieval"]["min_score"], 1)

    def test_enabled_must_be_a_boolean(self):
        # `enabled` is not in spec.options, so it needs its own check. A string
        # here is the worst case: "false" is truthy, and the comparison is
        # against False, so the feature silently ACTIVATES.
        with self.assertRaises(FeatureError) as cm:
            resolve({"history_slicing": {"enabled": "yes"}})
        msg = str(cm.exception)
        self.assertIn("must be a boolean", msg)
        self.assertIn("silently activates", msg)

    def test_enabled_true_and_false_are_both_fine(self):
        for value in (True, False):
            with self.subTest(enabled=value):
                resolve({"history_slicing": {"enabled": value}})

    def test_a_type_name_the_form_does_not_model_is_skipped_not_rejected(self):
        # A feature family may name a type this check has no entry for. That has
        # to pass through rather than raise, or adding a type name to one spec
        # breaks every config that declares it. No registry spec uses such a
        # name today, so the case only exists synthetically -- which is exactly
        # why it needs a test.
        spec = Spec(name="_kindless_probe", kind=Kind.TOOLSET, summary="x",
                    options={"window": ("duration", "5m", "A type kinds{} omits.")})
        REGISTRY["_kindless_probe"] = spec
        try:
            r = resolve({"_kindless_probe": {"window": 12345}})
            self.assertEqual(r.config["_kindless_probe"]["window"], 12345)
        finally:
            REGISTRY.pop("_kindless_probe")

    def test_every_registry_option_type_is_one_the_check_understands(self):
        # Not a duplicate of the skip test above: that one proves an unmodelled
        # name is tolerated, this one proves none is currently in use, so the
        # type check is actually covering the whole registry rather than
        # silently opting features out of it.
        modelled = {"str", "path", "int", "float", "bool", "list", "dict"}
        for name, spec in REGISTRY.items():
            for key, (type_name, _, _) in spec.options.items():
                self.assertIn(type_name, modelled, f"{name}.{key} is unchecked")


class TestInertOptionWarning(unittest.TestCase):
    """Configuring an option whose reader is skipped deserves a warning."""

    def _warnings(self, declared):
        return " ".join(resolve(declared).warnings)

    def test_retention_options_warn_while_continue_active_is_false(self):
        w = self._warnings({"pipeline": ["skill_retrieval"],
                            "skill_retrieval": {"max_activated_skills": 3}})
        self.assertIn("max_activated_skills", w)
        self.assertIn("inert", w)
        self.assertIn("continue_active", w)

    def test_the_warning_keys_off_the_declaration_not_the_value(self):
        # 3 IS the registry default, and it still warns. A value-delta check
        # would stay silent here -- but the option is just as inert, and the
        # host who wrote it down is just as misled.
        self.assertEqual(features.defaults("skill_retrieval")["max_activated_skills"], 3)
        self.assertIn("inert", self._warnings(
            {"pipeline": ["skill_retrieval"],
             "skill_retrieval": {"max_activated_skills": 3}}))

    def test_an_undeclared_default_does_not_warn(self):
        # Every merged config carries max_activated_skills, so keying off the
        # MERGED config would warn on every skill_retrieval pipeline ever
        # written -- noise that trains readers to ignore the channel.
        self.assertNotIn("inert", self._warnings({"pipeline": ["skill_retrieval"]}))

    def test_pin_grace_turns_warns_too(self):
        w = self._warnings({"pipeline": ["skill_retrieval"],
                            "skill_retrieval": {"pin_grace_turns": 2}})
        self.assertIn("pin_grace_turns", w)
        self.assertIn("inert", w)

    def test_state_pins_is_deliberately_not_inert(self):
        # state_pins is read by the pin path, which runs regardless of
        # continue_active. Listing it here would be a FALSE warning telling the
        # host to delete working config.
        w = self._warnings({"pipeline": ["skill_retrieval"], "skill_retrieval": {
            "state_pins": [{"state_key": "k", "skill": "s"}]}})
        self.assertNotIn("inert", w)

    def test_no_warning_once_continue_active_is_on(self):
        w = self._warnings({"pipeline": ["skill_retrieval"], "skill_retrieval": {
            "continue_active": True, "max_activated_skills": 3}})
        self.assertNotIn("inert", w)

    def test_declaring_without_activating_does_not_warn(self):
        # Not active means the retention block is not the reason it is inert,
        # and the "declare now, activate later" model would be unusable if
        # every parked block warned.
        self.assertNotIn("inert", self._warnings(
            {"skill_retrieval": {"max_activated_skills": 3}}))


class TestDefaultsHelper(unittest.TestCase):
    """features.defaults() exists so no call site writes its own literal."""

    def test_it_returns_the_registry_values(self):
        self.assertEqual(features.defaults("tool_result_trimming"),
                         {"max_chars": 4000, "keep": "head"})

    def test_mutable_defaults_are_deep_copied_per_call(self):
        # A shared mutable default is the classic version of this bug: one
        # agent's state_pins would leak into the next resolve() in the process.
        first = features.defaults("skill_retrieval")
        first["state_pins"].append({"state_key": "k", "skill": "s"})
        self.assertEqual(features.defaults("skill_retrieval")["state_pins"], [])
        self.assertEqual(REGISTRY["skill_retrieval"].options["state_pins"][1], [])

    def test_every_registry_default_survives_a_round_trip(self):
        # deepcopy on an exotic default would raise here rather than at the
        # first host that declares that feature.
        for name in REGISTRY:
            with self.subTest(feature=name):
                self.assertEqual(features.defaults(name), features.defaults(name))


class TestWiringUsesRegistryDefaults(unittest.IsolatedAsyncioTestCase):
    """build_callbacks must read the registry, not a literal of its own.

    `int(cfg.get("max_chars") or 12000)` ran 12000 against a declared default of
    4000, so three copies of the number existed and only one was the one the
    console displayed. The trimmer's marker text embeds max_chars verbatim,
    which makes the number the agent actually ran directly observable.
    """

    @staticmethod
    def _payload(rows):
        return {"status": "ok", "rows": [
            {"id": f"row-{i:03d}", "text": "x" * 50} for i in range(rows)]}

    async def _trim(self, config):
        from adkl.runtime import build_callbacks
        kwargs, _ = build_callbacks(["tool_result_trimming"], config)
        hook = kwargs["after_tool_callback"][0]
        payload = self._payload(60)
        blob = len(json.dumps(payload))
        # The discriminating window: over the declared 4000 default, under the
        # old 12000 literal. Asserted rather than assumed, because a change to
        # the fixture would otherwise turn this into a test that passes for the
        # wrong reason.
        self.assertGreater(blob, 4000)
        self.assertLess(blob, 12000)
        result = hook(None, {}, None, payload)
        if hasattr(result, "__await__"):
            result = await result
        return result

    async def test_the_trimmer_runs_the_declared_default_not_the_old_literal(self):
        result = await self._trim({})
        self.assertIsNotNone(result, "a payload over 4000 chars was not trimmed, "
                                     "so the wiring is still running 12000")
        self.assertLessEqual(len(json.dumps(result)), 4000)
        self.assertGreater(result["original_chars"], 4000)
        self.assertIn("Incomplete", result["_adkl_trimmed"])

    async def test_an_explicit_value_still_wins_over_the_default(self):
        result = await self._trim({"tool_result_trimming": {"max_chars": 12000}})
        self.assertIsNone(result, "12000 was configured explicitly, so a 4.8k "
                                  "payload must pass through untouched")

    def test_no_call_site_carries_a_literal_fallback(self):
        # The source-level half of the same guard. The runtime failure above is
        # only observable for tool_result_trimming, because history_slicing's
        # callback clamps with max(1, ...) and hides the difference -- so for
        # that one the honest check is that the literal is gone and the helper
        # is used.
        path = pathlib.Path(__file__).resolve().parent.parent / "adkl" / "runtime.py"
        source = path.read_text()
        for feature in ("history_slicing", "tool_result_trimming"):
            self.assertIn(f'features.defaults("{feature}")', source,
                          f"{feature} wiring does not read the registry")

        # COMMENTS ARE STRIPPED FIRST, and that is not a detail: both call sites
        # document the literal they replaced, so searching the raw text matches
        # the explanation and fails on correct code. Found by this test failing
        # against the fix it was written to protect.
        #
        # Comment SPANS are excised from each line rather than splitting at the
        # first '#' (which would truncate real code at a '#' inside a string) or
        # rebuilding from the token stream (which loses the adjacency that makes
        # `features.defaults` a searchable substring at all -- caught by the
        # canary assertion below, which is why it is here).
        spans: dict[int, list[tuple[int, int]]] = {}
        with tokenize.open(path) as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type == tokenize.COMMENT:
                    spans.setdefault(token.start[0], []).append(
                        (token.start[1], token.end[1]))
        kept = []
        for row, line in enumerate(source.splitlines(keepends=True), start=1):
            for start, end in sorted(spans.get(row, []), reverse=True):
                line = line[:start] + line[end:]
            kept.append(line)
        code = "".join(kept)
        self.assertIn("features.defaults", code, "comment stripping ate the code")

        # Both literals that actually shipped. A bare `or <n>` search would
        # match unrelated code, so these are the specific numbers.
        self.assertNotIn("or 12000", code)
        self.assertNotIn('"recent_turns") or 5', code)


class TestOptionReadsAreObservable(unittest.TestCase):
    """A wired option must be read through the mapping the host handed us.

    This exists because the fix guarded by the class above was FIRST WRITTEN in a
    shape that passed every test there and still broke a host. `defaults | cfg`
    removes the literal and calls `features.defaults(...)`, so the source-level
    guard is satisfied, and the resolved values are identical, so the behavioural
    guard is satisfied. What it destroys is OBSERVABILITY: `dict.__or__` copies
    the right operand at the C level and never calls its `__getitem__` or `get`,
    so a Mapping SUBCLASS that instruments those two methods records nothing.

    Instrumenting exactly those two methods is how a host proves the SDK really
    consults an option it declared -- the only way to tell a setting that is in
    effect from one that is decorative. This fixture replaces
    `features.resolve` with a recorder, wraps each per-feature option dict, and
    asserts every feature in its pipeline had at least one option read AND that
    the declared-but-unread set equals a reviewed constant. Under `|` both
    assertions go red naming `history_slicing`, and the tempting local fix is to
    move its keys into that constant -- which would record the FALSE claim that a
    deliberate `recent_turns: 1_000_000` is never read. Silently blind, with a
    plausible wrong repair: the worst shape a guard can fail in.

    So the rule this pins is not "use the registry" (above) but "read it one key
    at a time, through the caller's mapping."
    """

    # Measured against build_callbacks, not read off the source. Equality per
    # feature, not containment: a newly-read option means a branch went live and
    # a no-longer-read one means a setting went decorative. Either is the news.
    _OBSERVED = {
        "history_slicing": {"recent_turns", "preserve_tool_pairs"},
        "output_cleanup": {"report_removals"},
        "pii_redaction": {"kinds"},
        "progressive_auth": {"tools", "state_key"},
        "prompt_state_injection": {"slots"},
        "short_circuit": {"phrases"},
        # `request_id` is declared but belongs to the emitter, not the wiring.
        "staged_widgets": {"inject_contract"},
        "tool_history_repair": set(),
        "tool_result_projection": {"tools"},
        "tool_result_trimming": {"max_chars", "keep"},
    }

    # Options consumed outside build_callbacks -- bindings.py and the selector --
    # so this module cannot see them and must not pretend to. Listed rather than
    # skipped by default, so a NEW wired feature fails here until someone says
    # which of the two groups it is in.
    _READ_ELSEWHERE = {"skill_retrieval", "skill_state_injection"}

    class _Recorder(dict):
        """Records `get`/`__getitem__` -- the two methods `|` bypasses."""

        def __init__(self, data):
            super().__init__(data)
            self.reads: set[str] = set()

        def get(self, key, *default):
            self.reads.add(key)
            return super().get(key, *default)

        def __getitem__(self, key):
            self.reads.add(key)
            return super().__getitem__(key)

    def _reads(self, name):
        from adkl.runtime import build_callbacks
        # SEEDED WITH THE REGISTRY DEFAULTS, and that is load-bearing twice over.
        # It gives every factory a well-typed value to construct from, and it
        # keeps the mapping NON-EMPTY -- both wiring sites spell
        # `config.get(name) or {}`, which discards a falsy mapping and would swap
        # an empty recorder for a plain dict. That is a second, independent way
        # to blind a recorder, and it does not bite the real host only because a
        # resolved config is always merged and therefore truthy.
        rec = self._Recorder(features.defaults(name))
        build_callbacks([name], {name: rec},
                        on_output_finding=lambda *_: None,
                        widget_specs={"Probe": object()}, root=None)
        return rec.reads

    def test_every_wired_feature_is_classified(self):
        from adkl.runtime import IMPLEMENTED
        self.assertEqual(
            IMPLEMENTED, set(self._OBSERVED) | self._READ_ELSEWHERE,
            "a wired feature is in neither group. Add its measured option reads "
            "to _OBSERVED, or _READ_ELSEWHERE if its config is consumed outside "
            "build_callbacks -- do not leave it unclassified, which is how an "
            "unread option becomes invisible.")

    def test_each_feature_reads_its_options_through_the_caller_mapping(self):
        for name, expected in sorted(self._OBSERVED.items()):
            with self.subTest(feature=name):
                self.assertEqual(
                    self._reads(name), expected,
                    f"{name}: observed option reads changed. If this went EMPTY, "
                    "the wiring is merging with `|` (or another C-level bulk "
                    "copy) and no host can prove the option is honoured.")

    def test_the_two_default_merging_sites_do_not_use_or_merge(self):
        # The source-level half. The behavioural half above is the real guard,
        # but it only covers today's features; this one names the operator so a
        # third merge site cannot be introduced in the old shape.
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "adkl" / "runtime.py")
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            if "features.defaults(" in code:
                self.assertNotIn("|", code,
                                 "a registry default is being merged with `|`, "
                                 "which makes the read invisible to a host: "
                                 f"{line.strip()!r}")


class TestDescribeExposesTheExplicitConfigContract(unittest.TestCase):
    """`requires_explicit_config` was unreachable from every consumer.

    It decides whether a config VALIDATES, and `describe()` -- the registry as
    JSON, which is what builds the console's option form -- did not carry it.
    Unlike `support_status`, which reaches the console on a second channel via
    `capabilities.manifest()`, this had no channel at all.
    """

    def test_the_flag_rides_on_the_option_next_to_its_default(self):
        cards = {c["name"]: c for c in features.describe()}
        opts = {o["key"]: o for c in cards.values() for o in c["options"]}
        self.assertTrue(opts["max_chars"]["requiresExplicitConfig"])
        # Every option carries the key, so a form can branch on it without
        # treating "absent" as "not required".
        for card in cards.values():
            for opt in card["options"]:
                self.assertIsInstance(opt["requiresExplicitConfig"], bool)

    def test_it_agrees_with_the_registry_in_both_directions(self):
        for card in features.describe():
            declared = REGISTRY[card["name"]].requires_explicit_config
            flagged = {o["key"] for o in card["options"]
                       if o["requiresExplicitConfig"]}
            self.assertEqual(flagged, set(declared), card["name"])


class TestValidatorsNameTheRealFix(unittest.TestCase):
    """A rejection has to say what to do instead, not just what is wrong."""

    def test_a_trim_budget_below_the_floor_is_rejected(self):
        with self.assertRaises(FeatureError) as cm:
            resolve({"tool_result_trimming": {"max_chars": 199}})
        msg = str(cm.exception)
        self.assertIn("at least 200", msg)
        self.assertIn("incomplete-result envelope", msg)

    def test_the_floor_itself_is_accepted(self):
        resolve({"tool_result_trimming": {"max_chars": 200}})

    def test_keep_both_is_rejected_rather_than_silently_meaning_tail(self):
        # "both" was documented but never implemented: callbacks/tools.py
        # compares keep against "head" only, so "both" took the TAIL branch and
        # looked like a working third mode.
        with self.assertRaises(FeatureError) as cm:
            resolve({"tool_result_trimming": {"keep": "both"}})
        self.assertIn("head or tail", str(cm.exception))

    def test_both_implemented_keep_modes_are_accepted(self):
        for keep in ("head", "tail"):
            with self.subTest(keep=keep):
                resolve({"tool_result_trimming": {"keep": keep}})

    def test_zero_recent_turns_is_rejected_and_names_the_off_switch(self):
        # 0 was swallowed twice: the wiring's `or 5` turned it into 5, and the
        # callback clamps with max(1, ...). No value of 0 does what a reader
        # expects, so the error has to point at the real off-switch.
        for invalid in (0, -1):
            with self.subTest(recent_turns=invalid):
                with self.assertRaises(FeatureError) as cm:
                    resolve({"history_slicing": {"recent_turns": invalid}})
                msg = str(cm.exception)
                self.assertIn("at least 1", msg)
                self.assertIn("features.pipeline", msg)

    def test_one_recent_turn_is_accepted(self):
        resolve({"history_slicing": {"recent_turns": 1}})

    def test_enumerated_skill_modes_name_their_allowed_set(self):
        # Two of the three are read inside a callback, where a typo falls
        # through to the default branch and looks exactly like the mode the
        # host asked for. Rejecting at load is the only place it is visible.
        for key, allowed in (("native_activation", ("replace", "merge", "own")),
                             ("skill_hook_position", ("last", "first")),
                             ("skill_catalog", ("tool", "prompt"))):
            with self.subTest(option=key):
                with self.assertRaises(FeatureError) as cm:
                    resolve({"skill_retrieval": {key: "nope"}})
                msg = str(cm.exception)
                for value in allowed:
                    self.assertIn(value, msg)
                self.assertIn("nope", msg)   # echoes what was actually written
            for value in allowed:
                with self.subTest(option=key, value=value):
                    r = resolve({"skill_retrieval": {key: value}})
                    self.assertEqual(r.config["skill_retrieval"][key], value)

    def test_a_validator_runs_even_when_the_feature_is_not_activated(self):
        # resolve() validates every registry entry, not just active ones. So
        # broken config in a parked block is caught at load rather than waiting
        # for the day someone adds it to the pipeline.
        with self.assertRaises(FeatureError):
            resolve({"pipeline": [], "skill_retrieval": {"native_activation": "nope"}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
