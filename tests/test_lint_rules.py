#!/usr/bin/env python3
"""Tests for the cost and cross-layer lint rules.

Two things worth protecting, and the second is the one that nearly went wrong.

**Every rule must fire on a known positive.** All four conflict rules returned
zero findings against the 24 real prospect skills. That is a good result -- the
production corpus does not contain these conflicts -- but "zero findings" and "the
rule is broken" look identical from the outside. Each rule therefore has a
synthetic positive here, so silence stays trustworthy.

**Every rule must NOT fire on the things that merely look similar.** A skill that
says "never call X" AGREES with a prompt that forbids X, and flagging it would
train a reader to ignore the category. The negative cases below are the ones that
make these rules usable rather than noisy.

Thresholds are asserted against the real corpus statistics (body median ~545
tokens, tool declaration median ~506) rather than round numbers, so a future edit
that flags the median fails here.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import lint_conflict as LC  # noqa: E402
from adkl import lint_cost as LCost  # noqa: E402
from adkl import skill_lint  # noqa: E402


def rules(findings) -> set:
    """Rule names from (sev, rule, msg) or (sev, rule, skill, msg) tuples."""
    return {f[1] for f in findings}


class TestCostRules(unittest.TestCase):
    def test_a_typical_skill_is_not_flagged(self):
        # ~545 tokens is the corpus median. Flagging it would flag everything.
        body = "x" * (545 * 4)
        self.assertEqual(LCost.check_skill_cost("s", body, [], {}), [])

    def test_a_large_body_is_flagged_with_its_ratio(self):
        body = "x" * (3094 * 4)   # general-support's real size
        out = LCost.check_skill_cost("s", body, [], {})
        self.assertIn("SKILL_BODY_LARGE", rules(out))
        # The ratio must be right: an earlier version used integer division and
        # reported 5.7x as "2x", which understates the outlier it is naming.
        self.assertIn("5.7x", out[0][2])

    def test_turn_cost_counts_body_plus_tools(self):
        # The number that matters is what a model call costs, not either half.
        out = LCost.check_skill_cost(
            "s", "x" * (3000 * 4), ["a", "b"], {"a": 2500, "b": 2000})
        self.assertIn("TURN_COST_HIGH", rules(out))
        self.assertIn("7500", " ".join(f[2] for f in out))

    def test_an_unresolved_tool_contributes_zero_not_an_error(self):
        # TOOL_UNRESOLVED already reports it. Reporting the same problem in a
        # second category teaches a reader that categories mean nothing.
        out = LCost.check_skill_cost("s", "short", ["ghost"], {})
        self.assertEqual(out, [])

    def test_references_dominating_a_skill_is_flagged(self):
        out = LCost.check_skill_cost("s", "x" * 3000, [], {}, ref_bytes=27000)
        self.assertIn("REFERENCES_DOMINATE", rules(out))

    def test_small_references_are_not_flagged(self):
        # A 3x ratio on a tiny skill is noise, hence the byte floor.
        out = LCost.check_skill_cost("s", "x" * 500, [], {}, ref_bytes=2000)
        self.assertNotIn("REFERENCES_DOMINATE", rules(out))

    def test_cost_findings_are_never_errors(self):
        # Cost is a tradeoff to see, not a defect to block a save on.
        out = LCost.check_skill_cost(
            "s", "x" * 40000, ["a"], {"a": 6000}, ref_bytes=90000)
        self.assertTrue(out)
        self.assertNotIn("ERROR", {f[0] for f in out})

    def test_a_median_tool_declaration_is_not_flagged(self):
        # Corpus median is ~506 and 45 of 77 exceed 400, so the threshold has to
        # sit well above that to carry information.
        out = LCost.check_tool_cost([{
            "name": "t", "estimated_input_tokens_per_turn": 506,
            "params": [{"name": "q"}], "quality": {"undocumented_params": []}}])
        self.assertEqual(out, [])

    def test_a_zero_param_tool_with_a_big_docstring_is_flagged(self):
        # The cleanest disproportion: no parameters means no schema, so the cost
        # is entirely prose.
        out = LCost.check_tool_cost([{
            "name": "check_thing", "estimated_input_tokens_per_turn": 690,
            "params": [], "quality": {"undocumented_params": []}}])
        self.assertIn("TOOL_DOCSTRING_HEAVY", rules(out))

    def test_undocumented_params_are_flagged(self):
        out = LCost.check_tool_cost([{
            "name": "t", "estimated_input_tokens_per_turn": 100,
            "params": [{"name": "a"}],
            "quality": {"undocumented_params": ["a"]}}])
        self.assertIn("TOOL_PARAMS_UNDOCUMENTED", rules(out))


class TestWidgetPromises(unittest.TestCase):
    EMITS = {"search_x": False, "render_x": True}

    def test_a_promise_no_declared_tool_can_keep_is_flagged(self):
        out = LC.check_widget_promises(
            "s", "Render the product card for the customer.",
            ["search_x"], self.EMITS)
        self.assertIn("WIDGET_PROMISED_NOT_EMITTABLE", rules(out))

    def test_a_promise_a_declared_tool_can_keep_is_not_flagged(self):
        out = LC.check_widget_promises(
            "s", "Render the product card.", ["render_x"], self.EMITS)
        self.assertEqual(out, [])

    def test_a_skill_declaring_no_tools_is_skipped(self):
        # A pure-prose skill describing UI the customer sees elsewhere is
        # legitimate; it is not promising to emit anything itself.
        out = LC.check_widget_promises(
            "s", "Show the trade-in card.", [], self.EMITS)
        self.assertEqual(out, [])

    def test_merely_mentioning_a_card_is_not_a_promise(self):
        out = LC.check_widget_promises(
            "s", "The card the customer saw earlier explains this.",
            ["search_x"], self.EMITS)
        self.assertEqual(out, [])

    def test_unrecorded_emission_is_unknown_not_evidence_of_absence(self):
        """The whole rule was vacuous on host-factory projects.

        `emits_widget` is populated only by the static `tool_catalog` source
        scan. A native/RuntimeBindings inventory reports name + description +
        scope, so every tool arrived with the key absent -- which the old
        `.get("value", False)` turned into "provably emits nothing". The rule
        then fired on every skill with a widget claim, including ones whose
        stagers demonstrably emit at the turn boundary (3 of 26 real prospect
        skills). A rule that accuses an author of promising unrenderable UI
        must speak from recorded evidence or stay silent.
        """
        out = LC.check_widget_promises(
            "s", "Render the product card for the customer.",
            ["stage_x"], {"stage_x": None})
        self.assertEqual(out, [])

    def test_one_unknown_tool_suppresses_a_mixed_declaration(self):
        # Any declared tool that MIGHT emit is enough to make the promise
        # keepable, so a partially-scanned inventory must not accuse.
        out = LC.check_widget_promises(
            "s", "Render the product card.", ["search_x", "stage_x"],
            {"search_x": False, "stage_x": None})
        self.assertEqual(out, [])

    def test_a_recorded_false_still_fires(self):
        # The fix must not disarm the rule where evidence really exists.
        out = LC.check_widget_promises(
            "s", "Render the product card.", ["search_x"], self.EMITS)
        self.assertIn("WIDGET_PROMISED_NOT_EMITTABLE", rules(out))

    def test_a_native_catalog_yields_unknown_through_the_shared_entrypoint(self):
        """Guards the coercion site itself, not just the rule.

        `skill_lint.cross_layer_findings` builds the `emits` map. A future edit
        restoring a `False` default there would reintroduce the false positive
        with every rule-level test above still green.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills" / "card-help"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: card-help\ndescription: Show a product card.\n"
                "metadata:\n  adk_additional_tools: [stage_x]\n---\n"
                "Render the product card for the customer.\n",
                encoding="utf-8")
            findings, _skills, _known, _warnings = skill_lint.lint(
                root / "skills", None,
                # No `emits_widget` key at all -- exactly what an --inspect
                # native binding produces.
                catalog={"tools": [{"name": "stage_x"}]})
        self.assertNotIn("WIDGET_PROMISED_NOT_EMITTABLE", rules(findings))


class TestWidgetCoverage(unittest.TestCase):
    """The inverse rule: no widget where the project has display tools.

    Its whole usability rests on the gate. Firing on a project with no display
    tools would put a WARN on every skill of every text-only agent, which is how
    a category gets ignored wholesale -- so the negative cases here matter more
    than the positive one.
    """
    EMITS = {"search_x": False, "render_x": True}
    # Same map, but recording that the emitter was *declared* by a pack rather
    # than inferred from module text. Only this one arms the rule.
    CONFIGURED = {"search_x": False, "render_x": True}
    ANNOTATED = {"metadata": {"adkl_presentation": "text_only"}}

    def test_a_skill_binding_no_display_tool_is_flagged(self):
        out = LC.check_widget_coverage("s", {}, ["search_x"], self.EMITS, self.CONFIGURED)
        self.assertIn("WIDGET_OUTPUT_MISSING", rules(out))

    def test_a_prose_only_skill_is_flagged_too(self):
        # Unlike check_widget_promises, which skips these on purpose. A skill
        # declaring nothing still produces a text-only turn, and in a
        # pack-configured project that is the case the annotation exists for.
        out = LC.check_widget_coverage("s", {}, [], self.EMITS, self.CONFIGURED)
        self.assertIn("WIDGET_OUTPUT_MISSING", rules(out))

    def test_a_skill_binding_a_display_tool_is_not_flagged(self):
        out = LC.check_widget_coverage("s", {}, ["render_x"], self.EMITS, self.CONFIGURED)
        self.assertEqual(out, [])

    def test_a_project_with_no_display_tools_is_not_flagged(self):
        # The gate. A text-only agent has nothing to answer for here, and
        # without this every one of its skills would carry a WARN.
        out = LC.check_widget_coverage("s", {}, ["search_x"], {"search_x": False},
                                        {"search_x": False})
        self.assertEqual(out, [])

    def test_inferred_emission_does_not_arm_the_rule_but_configured_does(self):
        """The gate reads declared evidence only, and this is why.

        `tool_catalog.widget_emission` decides emission by searching a module's
        text, marks the result inferred, and its own docstring calls that an
        over-report. Arming on it would mean one domain module mentioning a
        render call anywhere put a WARN on every text-only skill in the project.
        """
        emits = {"search_x": False, "domain_x": True}
        inferred = {"search_x": False, "domain_x": False}   # True but inferred
        self.assertEqual(
            LC.check_widget_coverage("s", {}, ["search_x"], emits, inferred), [])
        declared = {"search_x": False, "domain_x": True}
        self.assertIn("WIDGET_OUTPUT_MISSING", rules(
            LC.check_widget_coverage("s", {}, ["search_x"], emits, declared)))

    def test_an_intentional_text_only_skill_is_not_flagged(self):
        out = LC.check_widget_coverage("s", self.ANNOTATED, ["search_x"], self.EMITS,
                                       self.CONFIGURED)
        self.assertEqual(out, [])

    def test_unrecorded_emission_is_unknown_not_evidence_of_absence(self):
        # Same discipline as check_widget_promises: a native/host-factory
        # inventory records name+description+scope only, so a declared tool with
        # no `emits_widget` key MIGHT emit and must not be accused.
        out = LC.check_widget_coverage(
            "s", {}, ["stage_x"], {"render_x": True, "stage_x": None},
            {"render_x": True, "stage_x": False})
        self.assertEqual(out, [])

    def test_a_stale_annotation_is_reported_not_silently_honoured(self):
        out = LC.check_widget_coverage("s", self.ANNOTATED, ["render_x"], self.EMITS,
                                       self.CONFIGURED)
        self.assertIn("WIDGET_OUTPUT_ANNOTATION_STALE", rules(out))
        self.assertNotIn("WIDGET_OUTPUT_MISSING", rules(out))

    def test_a_misspelled_annotation_is_an_error_not_an_opt_out(self):
        """Read as data, 'text-only' is simply not the opt-out -- so the
        coverage rule keeps firing and the author, who believes the skill is
        annotated, reads that as the rule being broken. The value is checked on
        check_one's path, which every caller runs.
        """
        fm = {"name": "s", "description": "d",
              "metadata": {"adkl_presentation": "text-only"}}
        out = skill_lint.check_one("s", fm, "body", set())
        self.assertIn("PRESENTATION_METADATA_INVALID", rules(out))
        self.assertEqual(
            LC.check_widget_coverage("s", fm, ["search_x"], self.EMITS,
                                    self.CONFIGURED)[0][1],
            "WIDGET_OUTPUT_MISSING")

    def test_an_absent_annotation_is_not_an_invalid_one(self):
        out = skill_lint.check_one(
            "s", {"name": "s", "description": "d"}, "body", set())
        self.assertNotIn("PRESENTATION_METADATA_INVALID", rules(out))

    def _flagged_through_lint(self, inferred: bool) -> set:
        """Run the shared entrypoint over one unannotated and one annotated skill."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for skill, meta in (("chatty", ""),
                               ("quiet", "metadata:\n  adkl_presentation: text_only\n")):
                directory = root / "skills" / skill
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text(
                    f"---\nname: {skill}\ndescription: Answer a question.\n{meta}---\n"
                    "Answer in one paragraph.\n", encoding="utf-8")
            findings, _skills, _known, _warnings = skill_lint.lint(
                root / "skills", None,
                catalog={"tools": [
                    {"name": "render_x",
                     "emits_widget": {"value": True, "basis": "pack",
                                      "inferred": inferred}}]})
        return {f[2] for f in findings if f[1] == "WIDGET_OUTPUT_MISSING"}

    def test_the_rule_reaches_findings_through_the_shared_entrypoint(self):
        """Guards the wiring, not just the rule.

        cross_layer_findings builds the `emits` and `configured` maps and owns the
        per-skill loop; the rule reads frontmatter, which that loop previously
        discarded.
        """
        self.assertEqual(self._flagged_through_lint(inferred=False), {"chatty"})

    def test_the_shared_entrypoint_gate_ignores_inferred_catalog_evidence(self):
        # Same catalog, same skills, emission inferred from module text instead
        # of declared: cross_layer_findings must build `configured` such that the
        # gate stays shut. Asserted here and not only on the rule, because the
        # two maps are built in that function and only differ by this flag.
        self.assertEqual(self._flagged_through_lint(inferred=True), set())


class TestToolUsedNotDeclared(unittest.TestCase):
    """Rule 2's polarity window, which had no test until it produced a false
    positive against the real corpus.

    The rule reads an imperative "call `x`" for a tool the frontmatter never
    bound. Three real usages look identical to a naive grep and only the first
    is a defect, so the suppressions below are what make the rule usable. They
    are asserted here because the rule's own docstring listed them while
    nothing pinned them.
    """

    KNOWN = {"fetch_x", "render_x", "add_to_cart_v2", "avail_a", "avail_b"}

    def fired(self, body: str, declared=()) -> set:
        fm = {"name": "s", "description": "A skill.",
              "metadata": {"adk_additional_tools": list(declared)}}
        return {f[1] for f in skill_lint.check_one("s", fm, body, self.KNOWN)}

    def test_an_imperative_for_an_undeclared_tool_is_flagged(self):
        self.assertIn("TOOL_USED_NOT_DECLARED", self.fired("Call `fetch_x` now."))

    def test_a_declared_tool_is_not_flagged(self):
        self.assertNotIn("TOOL_USED_NOT_DECLARED",
                         self.fired("Call `fetch_x` now.", ["fetch_x"]))

    def test_a_prohibition_is_not_flagged(self):
        # "Do NOT call `render_x`" is correct as written: the tool is properly
        # absent from the tool list.
        self.assertNotIn("TOOL_USED_NOT_DECLARED",
                         self.fired("Do NOT call `render_x` here."))

    def test_a_guard_on_call_history_is_not_flagged(self):
        self.assertNotIn("TOOL_USED_NOT_DECLARED",
                         self.fired("If `add_to_cart_v2` was already called, skip."))

    def test_a_negation_that_wrapped_to_the_previous_line_still_suppresses(self):
        """The false positive this window was widened to fix.

        Skill bodies can be hard-wrapped, so a
        negation routinely sits on the line above the tool it negates. Splitting
        on physical lines left `before` holding only the indent, and the rule
        reported a correctly-written instruction -- telling the author to declare
        a tool the text explicitly forbids. The fixture exercises that line break.
        """
        body = ("2. After address submission, call `avail_a`—never\n"
                "   `avail_b`. Qualified → Step 3.\n")
        self.assertNotIn("TOOL_USED_NOT_DECLARED", self.fired(body, ["avail_a"]))

    def test_a_sentence_boundary_still_ends_the_window(self):
        # Reflowing must not let a negation reach past its own sentence, or the
        # rule would go quiet on the defect it exists to catch.
        out = skill_lint.check_one(
            "s", {"name": "s", "description": "A skill."},
            "Do not call `render_x`. Call `fetch_x` to finish.\n", self.KNOWN)
        messages = [m for _s, r, m in out if r == "TOOL_USED_NOT_DECLARED"]
        self.assertEqual(len(messages), 1)
        self.assertIn("`fetch_x`", messages[0])

    def test_a_negation_in_an_earlier_paragraph_does_not_leak(self):
        body = "Do not call `render_x`.\n\nCall `fetch_x` to finish.\n"
        self.assertIn("TOOL_USED_NOT_DECLARED", self.fired(body))


class TestToolOrder(unittest.TestCase):
    def test_a_contradicted_order_is_flagged(self):
        out = LC.check_tool_order(
            "s", "1. Call `render_x` now.\n2. Call `search_x` after.",
            ["render_x"], {"render_x": "Call search_x first before rendering."})
        self.assertIn("TOOL_ORDER_CONTRADICTED", rules(out))

    def test_a_consistent_order_is_not_flagged(self):
        out = LC.check_tool_order(
            "s", "1. Call `search_x` first.\n2. Call `render_x` after.",
            ["render_x"], {"render_x": "Call search_x first before rendering."})
        self.assertEqual(out, [])

    def test_a_skill_with_no_steps_is_skipped(self):
        # Prose skills have no order to contradict, and guessing at intent here
        # would produce noise on every skill with a workflow.
        out = LC.check_tool_order(
            "s", "Use render_x when appropriate.",
            ["render_x"], {"render_x": "Call search_x first."})
        self.assertEqual(out, [])


class TestPromptProhibitions(unittest.TestCase):
    PROMPT = "NEVER call a render tool with an empty response."

    def test_a_skill_instructing_the_forbidden_thing_is_flagged(self):
        out = LC.check_prompt_prohibitions(
            "s", "Call the render tool with an empty response when unsure.",
            self.PROMPT)
        self.assertIn("PROMPT_PROHIBITION_CONTRADICTED", rules(out))

    def test_a_skill_restating_the_prohibition_is_not_flagged(self):
        # This is the case that makes the rule usable. A skill saying "never call
        # a render tool with an empty response" AGREES with the prompt, and
        # flagging it would train a reader to ignore the whole category.
        out = LC.check_prompt_prohibitions(
            "s", "Never call the render tool with an empty response.",
            self.PROMPT)
        self.assertEqual(out, [])

    def test_a_short_prohibition_is_skipped(self):
        # "never guess" would match almost any sentence containing "guess", and
        # the finding would be worthless.
        out = LC.check_prompt_prohibitions(
            "s", "Guess the plan when unsure.", "Never guess.")
        self.assertEqual(out, [])

    def test_an_unrelated_skill_is_not_flagged(self):
        out = LC.check_prompt_prohibitions(
            "s", "Explain the activation fee.", self.PROMPT)
        self.assertEqual(out, [])

    def test_no_prompt_means_no_findings(self):
        out = LC.check_prompt_prohibitions("s", "Call anything you like.", "")
        self.assertEqual(out, [])


class TestCrossSkill(unittest.TestCase):
    def test_opposite_instructions_for_one_tool_are_flagged(self):
        out = LC.check_cross_skill(
            [("a", "Call `tool_x` to proceed.", ["tool_x"]),
             ("b", "Never call `tool_x` here.", ["tool_x"])], {})
        self.assertIn("TOOL_INSTRUCTED_DIFFERENTLY", rules(out))

    def test_one_skill_qualifying_its_own_use_is_not_flagged(self):
        # "Call X, but never with Y" is normal authoring, not a conflict.
        out = LC.check_cross_skill(
            [("a", "Call `tool_x`. Never call `tool_x` without a zip.",
              ["tool_x"])], {})
        self.assertEqual(out, [])

    def test_two_skills_agreeing_are_not_flagged(self):
        out = LC.check_cross_skill(
            [("a", "Call `tool_x` first.", ["tool_x"]),
             ("b", "Call `tool_x` when asked.", ["tool_x"])], {})
        self.assertEqual(out, [])


class TestUnusedTools(unittest.TestCase):
    def test_an_unreferenced_tool_is_reported(self):
        out = LC.check_unused_tools(
            [{"name": "ghost"}], [("a", "Call `other`.", ["other"])])
        self.assertIn("TOOL_NOT_IN_ANY_SKILL", rules(out))

    def test_a_tool_referenced_only_in_prose_counts_as_used(self):
        # Backticked in a body without being declared is already reported by
        # TOOL_USED_NOT_DECLARED; it is not also dead weight.
        out = LC.check_unused_tools(
            [{"name": "used"}], [("a", "Call `used`.", [])])
        self.assertEqual(out, [])


class TestRulesFireAtAll(unittest.TestCase):
    """The guard that makes silence trustworthy.

    All four conflict rules return zero findings against the 24 real prospect
    skills. That is a genuine result about corpus quality -- but zero findings and
    a broken rule are indistinguishable from outside, so each rule is proven
    capable of firing.
    """

    def test_every_conflict_rule_can_fire(self):
        fired = set()
        fired |= rules(LC.check_widget_promises(
            "s", "Render the card.", ["t"], {"t": False}))
        fired |= rules(LC.check_tool_order(
            "s", "1. Call `b` now.\n2. Call `a` after.",
            ["b"], {"b": "Call a first."}))
        fired |= rules(LC.check_prompt_prohibitions(
            "s", "Call a render tool with an empty response.",
            "NEVER call a render tool with an empty response."))
        fired |= rules(LC.check_cross_skill(
            [("a", "Call `t`.", ["t"]), ("b", "Never call `t`.", ["t"])], {}))
        fired |= rules(LC.check_unused_tools([{"name": "g"}], [("a", "", [])]))

        self.assertEqual(fired, {
            "WIDGET_PROMISED_NOT_EMITTABLE", "TOOL_ORDER_CONTRADICTED",
            "PROMPT_PROHIBITION_CONTRADICTED", "TOOL_INSTRUCTED_DIFFERENTLY",
            "TOOL_NOT_IN_ANY_SKILL"})


class TestSharedLintWiring(unittest.TestCase):
    def test_catalog_rules_run_through_the_shared_entrypoint(self):
        """The console and CLI call this entrypoint, not individual rules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills" / "card-help"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: card-help\ndescription: Show a product card to users.\n"
                "metadata:\n  adk_additional_tools: [search_x]\n---\n"
                "Render the product card for the customer.\n",
                encoding="utf-8")
            findings, _skills, _known, _warnings = skill_lint.lint(
                root / "skills", None,
                catalog={"tools": [{"name": "search_x",
                                    "emits_widget": {"value": False}}]})
        self.assertIn("WIDGET_PROMISED_NOT_EMITTABLE", rules(findings))


if __name__ == "__main__":
    unittest.main(verbosity=0)
