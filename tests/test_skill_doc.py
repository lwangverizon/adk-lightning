#!/usr/bin/env python3
"""Tests for the structured skill-body parser.

The load-bearing property is `TestMinimalDiff`: editing one field must leave
every other section byte-identical. Structured editors that re-emit the whole
document are how these tools rot -- every save becomes a whole-file diff and
review stops working.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl.skill_doc import SkillDoc  # noqa: E402

REAL = (pathlib.Path(__file__).resolve().parent
        / "fixtures" / "skill_bodies")

BODY = """\
## Workflows

### Add to Cart

Use when the user wants to buy.

- [ ] Step 1: **Always** call `search_products` to find the device.
  Continues on a second line.
- [ ] Step 2: Call `render_product_cta`.

### Promo Query

Use when the user asks for a code.

- [ ] Step 1: Call `get_cart_summary`.

## Gotchas

- **Text before render:** Never split them across turns.
- Plain gotcha with no label.
"""


def load_body(name: str) -> str:
    src = (REAL / name / "SKILL.md").read_text(encoding="utf-8")
    return src.split("\n---", 2)[-1] if src.startswith("---") else src


class TestParse(unittest.TestCase):
    def setUp(self):
        self.doc = SkillDoc.parse(BODY)

    def test_sections_and_kinds(self):
        self.assertEqual([(s.title, s.kind) for s in self.doc.sections],
                         [("Workflows", "workflows"), ("Gotchas", "gotchas")])

    def test_workflows_and_steps(self):
        wf = self.doc.sections[0].workflows
        self.assertEqual([w.name for w in wf], ["Add to Cart", "Promo Query"])
        self.assertEqual(len(wf[0].steps), 2)
        self.assertEqual(wf[0].when, "Use when the user wants to buy.")

    def test_step_prefix_is_stripped_so_renumbering_is_free(self):
        # "Step 1:" is positional, not content. Storing it in the text would
        # make reordering produce wrong numbers.
        first = self.doc.sections[0].workflows[0].steps[0]
        self.assertTrue(first.text.startswith("**Always** call"))
        self.assertNotIn("Step 1", first.text)

    def test_continuation_lines_join_the_step(self):
        first = self.doc.sections[0].workflows[0].steps[0]
        self.assertIn("Continues on a second line.", first.text)

    def test_gotchas_labelled_and_plain(self):
        items = self.doc.sections[1].items
        self.assertEqual(items[0]["label"], "Text before render")
        self.assertEqual(items[1]["label"], "")
        self.assertIn("Plain gotcha", items[1]["text"])

    def test_singular_workflow_heading_recognized(self):
        # Real skills use both "## Workflow" and "## Workflows".
        d = SkillDoc.parse("## Workflow\n\n### A\n\n- [ ] Step 1: x\n")
        self.assertEqual(d.sections[0].kind, "workflows")

    def test_unknown_heading_is_prose(self):
        d = SkillDoc.parse("## Simplicity Context\n\nFree text here.\n")
        self.assertEqual(d.sections[0].kind, "prose")
        self.assertEqual(d.sections[0].prose, "Free text here.")


class TestRoundTrip(unittest.TestCase):
    def test_synthetic_body_unchanged(self):
        self.assertEqual(SkillDoc.parse(BODY).render().strip(), BODY.strip())

    def test_every_real_skill_round_trips_byte_identically(self):
        names = sorted(p.parent.name for p in REAL.glob("*/SKILL.md"))
        self.assertGreaterEqual(len(names), 3, "parser fixtures missing")
        for name in names:
            body = load_body(name)
            with self.subTest(skill=name):
                self.assertEqual(SkillDoc.parse(body).render().strip(),
                                 body.strip())


def first_workflow_step(payload):
    """Locate the first workflow step, wherever it lives.

    Indexing `sections[0]["workflows"][0]` hard-coded a shape that was true of
    one skill at one moment. Re-syncing prospect-cart-management from the current
    repo restructured it into prose sections with no workflows at all, and these
    tests raised IndexError -- reporting a broken parser when the parser was fine
    and the fixture had simply moved on.

    Returns (section_index, workflow_index) or raises with a useful message.
    """
    for si, sec in enumerate(payload["sections"]):
        for wi, wf in enumerate(sec.get("workflows") or []):
            if wf.get("steps"):
                return si, wi
    raise AssertionError("no workflow with steps in this skill; the fixture "
                         "changed shape and the test needs a different skill")



class TestMinimalDiff(unittest.TestCase):
    """Editing one field must not disturb any other section."""

    def _edit(self, body: str, mutate) -> tuple[str, str]:
        original = SkillDoc.parse(body)
        payload = original.to_json()
        mutate(payload)
        return body, SkillDoc.from_json(payload, original).render()

    def test_no_change_is_byte_identical(self):
        body, out = self._edit(BODY, lambda p: None)
        self.assertEqual(out.strip(), body.strip())

    def test_editing_a_step_leaves_gotchas_untouched(self):
        def mutate(p):
            p["sections"][0]["workflows"][0]["steps"][0]["text"] = "REPLACED"
        body, out = self._edit(BODY, mutate)
        self.assertIn("REPLACED", out)
        # The gotchas section must be present verbatim.
        gotchas = body.split("## Gotchas", 1)[1].strip()
        self.assertIn(gotchas, out)

    def test_editing_a_gotcha_leaves_workflows_untouched(self):
        def mutate(p):
            p["sections"][1]["items"][0]["text"] = "NEW TEXT"
        body, out = self._edit(BODY, mutate)
        self.assertIn("NEW TEXT", out)
        workflows = body.split("## Workflows", 1)[1].split("## Gotchas", 1)[0]
        self.assertIn(workflows.strip(), out)

    def test_authored_step_numbers_are_preserved_on_a_text_edit(self):
        # Numbers are author-controlled: real skills use "Step 0:" for a
        # conditional preamble and restart numbering inside one workflow
        # (prospect-device-exploration does both). Deriving them from position
        # rewrote author intent silently.
        body = "## Workflows\n\n### A\n\n- [ ] Step 0: pre\n- [ ] Step 1: one\n"
        original = SkillDoc.parse(body)
        payload = original.to_json()
        payload["sections"][0]["workflows"][0]["steps"][1]["text"] = "CHANGED"
        out = SkillDoc.from_json(payload, original).render()
        self.assertIn("- [ ] Step 0: pre", out)
        self.assertIn("- [ ] Step 1: CHANGED", out)

    def test_adding_a_step_renumbers_only_that_workflow(self):
        def mutate(p):
            p["sections"][0]["workflows"][0]["steps"].insert(
                1, {"text": "INSERTED", "done": False})
        _, out = self._edit(BODY, mutate)
        block = out.split("### Add to Cart", 1)[1].split("###", 1)[0]
        # A structural change forces positional renumbering, because keeping the
        # authored labels would produce two "Step 2"s.
        self.assertIn("- [ ] Step 2: INSERTED", block)
        self.assertIn("- [ ] Step 3: Call `render_product_cta`.", block)
        # The other workflow keeps its own numbering.
        other = out.split("### Promo Query", 1)[1]
        self.assertIn("- [ ] Step 1: Call `get_cart_summary`.", other)

    def test_real_skill_single_step_edit_touches_few_lines(self):
        body = load_body("multi-step")
        original = SkillDoc.parse(body)
        payload = original.to_json()
        si, wi = first_workflow_step(payload)
        payload["sections"][si]["workflows"][wi]["steps"][0]["text"] = \
            "SHORT REPLACEMENT"
        out = SkillDoc.from_json(payload, original).render()

        import difflib
        diff = [d for d in difflib.unified_diff(
            body.splitlines(), out.splitlines(), lineterm="", n=0)
            if d.startswith(("+", "-")) and not d.startswith(("+++", "---"))]
        # The edited step wraps across 3 source lines; replacing it with one line
        # yields a handful of changed lines, not a rewritten file.
        self.assertLess(len(diff), 12, f"diff too large:\n" + "\n".join(diff))
        self.assertTrue(any("SHORT REPLACEMENT" in d for d in diff))


class TestLeadProse(unittest.TestCase):
    """Prose between a `##` heading and the first `###` must survive.

    competitive-switch keeps ~20 lines of pricing policy there. An earlier
    version classified the section as workflows and re-rendered it without the
    lead, silently deleting that content -- the worst class of bug an editor can
    have, since it looks like a successful save.
    """

    BODY = """\
## Simplicity Context

Policy paragraph that must not vanish.

More policy on a second line.

### General Switch Inquiry

- [ ] Step 1: call `get_offers`.

## Gotchas

- **A:** one
"""

    def test_lead_is_captured(self):
        d = SkillDoc.parse(self.BODY)
        self.assertEqual(d.sections[0].kind, "workflows")
        self.assertIn("must not vanish", d.sections[0].lead)

    def test_lead_survives_a_step_edit(self):
        original = SkillDoc.parse(self.BODY)
        payload = original.to_json()
        payload["sections"][0]["workflows"][0]["steps"][0]["text"] = "CHANGED"
        out = SkillDoc.from_json(payload, original).render()
        self.assertIn("must not vanish", out)
        self.assertIn("More policy on a second line.", out)
        self.assertIn("CHANGED", out)

    def test_real_skill_keeps_its_lead_after_an_edit(self):
        # lead-prose, because it is the one example skill with BOTH lead
        # prose and workflow steps in the same section -- the shape this guards.
        # It was competitive-switch, which restructured into prose-only sections
        # upstream and so stopped exercising the lead path at all.
        body = load_body("lead-prose")
        original = SkillDoc.parse(body)
        payload = original.to_json()
        si, wi = first_workflow_step(payload)
        lead = (payload["sections"][si].get("lead") or "").strip()
        self.assertTrue(lead, "fixture must have lead prose to guard it")
        payload["sections"][si]["workflows"][wi]["steps"][0]["text"] = "SHORT"
        out = SkillDoc.from_json(payload, original).render()
        # The lead survives the edit. This is the regression that dropped ~20
        # lines of pricing policy when a section was reclassified by content.
        self.assertIn(lead.splitlines()[0], out)
        self.assertIn("SHORT", out)


class TestRealWorldShapes(unittest.TestCase):
    """Shapes only the real skills contained. Each was a bug."""

    def test_nested_checkboxes_are_not_steps(self):
        # prospect-device-exploration nests "- [ ] **0a — ...**" under Step 0 at
        # six-space indent. Matching the stripped line treated those as steps,
        # which polluted the label run and forced a renumber that rewrote both
        # "Step 0" and a deliberately restarted 1,2,3 sequence.
        body = ("## Workflows\n\n### A\n\n- [ ] Step 0: pre\n"
                "      - [ ] **0a — nested:** detail\n- [ ] Step 1: one\n")
        d = SkillDoc.parse(body)
        steps = d.sections[0].workflows[0].steps
        self.assertEqual([st.label for st in steps], ["Step 0", "Step 1"])
        self.assertIn("0a — nested", d.render())

    def test_leading_blank_lines_after_frontmatter_survive(self):
        # The body slice starts right after the closing fence, so it begins with
        # the author's blank lines. Dropping them shifted every later line in the
        # diff by two.
        body = "\n\n## Gotchas\n\n- **A:** one\n"
        self.assertEqual(SkillDoc.parse(body).render(), body)

    def test_missing_trailing_newline_is_preserved(self):
        body = "## Gotchas\n\n- **A:** one"
        self.assertEqual(SkillDoc.parse(body).render(), body)

    def test_section_separator_blank_lines_are_preserved(self):
        # prospect-public-qa has NO blank line before "## Answer shape". Joining
        # sections with a single newline forced one in, so every save reflowed
        # the boundary.
        body = "## A\n\ntext\n## B\n\nmore\n"
        self.assertEqual(SkillDoc.parse(body).render(), body)
        spaced = "## A\n\ntext\n\n\n## B\n\nmore\n"
        self.assertEqual(SkillDoc.parse(spaced).render(), spaced)

    def test_all_example_skills_round_trip_exactly(self):
        # Exact bytes, not stripped-equal: whitespace churn is the failure mode
        # this whole module exists to prevent.
        for name in sorted(p.parent.name for p in REAL.glob("*/SKILL.md")):
            body = load_body(name)
            with self.subTest(skill=name):
                self.assertEqual(SkillDoc.parse(body).render(), body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
