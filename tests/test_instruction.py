#!/usr/bin/env python3
"""Tests for the instruction compiler.

The `TestAdkCompatibility` class is the important one: it pins behaviours read
from `google/adk/utils/instructions_utils.py:_render_with_regex`. If ADK changes
those, these tests should fail loudly rather than the prompt quietly drifting.

Run: python3 -m pytest tests/ -q   (or python3 tests/test_instruction.py)
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import instruction  # noqa: E402
from adkl.instruction import (  # noqa: E402
    CompiledInstruction,
    Slot,
    SlotError,
    TemplateError,
    build_provider,
    compile_instruction,
    render,
)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def write(self, rel: str, text: str) -> pathlib.Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestAdkCompatibility(unittest.TestCase):
    """Contract with ADK's regex engine. Each case cites the source behaviour."""

    def test_required_key_present(self):
        self.assertEqual(run(render("Hi {name}", {"name": "Lu"})), "Hi Lu")

    def test_required_key_missing_raises(self):
        with self.assertRaises(KeyError):
            run(render("Hi {name}", {}))

    def test_optional_key_missing_renders_empty(self):
        # instructions_utils.py:150 -- optional + absent -> ''
        self.assertEqual(run(render("Hi {name?}!", {})), "Hi !")

    def test_none_value_renders_empty_even_when_required(self):
        # :147-149 -- a present-but-None value returns '' before the optional
        # check, so this is NOT the same as "missing".
        self.assertEqual(run(render("Hi {name}!", {"name": None})), "Hi !")

    def test_invalid_state_name_passes_through_verbatim(self):
        # :145-146 -- returns match.group() unchanged, no error. This is why the
        # validator must reject these at config time: at runtime they are silent.
        self.assertEqual(run(render("a {my-key} b", {})), "a {my-key} b")
        self.assertEqual(run(render("{has space}", {})), "{has space}")

    def test_scope_prefixes_are_valid_state_names(self):
        state = {"app:tier": "gold", "user:id": "u1", "temp:x": "t"}
        self.assertEqual(
            run(render("{app:tier}/{user:id}/{temp:x}", state)), "gold/u1/t")

    def test_no_brace_template_returned_unchanged(self):
        # :113-115 -- early return avoids a regex scan on every LLM call.
        self.assertEqual(run(render("no placeholders", {})), "no placeholders")

    def test_non_string_values_are_stringified(self):
        self.assertEqual(run(render("{n} {b}", {"n": 42, "b": True})), "42 True")

    def test_artifact_required_and_optional(self):
        self.assertEqual(
            run(render("{artifact.f}", {}, load_artifact=lambda k: "DATA")), "DATA")
        self.assertEqual(
            run(render("[{artifact.f?}]", {}, load_artifact=lambda k: None)), "[]")
        with self.assertRaises(KeyError):
            run(render("{artifact.f}", {}, load_artifact=lambda k: None))

    def test_artifact_without_service_raises(self):
        with self.assertRaises(ValueError):
            run(render("{artifact.f}", {}))


class TestSlots(_Tmp):
    def test_named_slot_substituted_at_build_time(self):
        self.write("blocks/resp.md", "Be concise.")
        t = self.write("t.md", "Role.\n{RESPONSE_STANDARDS}\nEnd.")
        c = compile_instruction(t, self.root,
                                [Slot("RESPONSE_STANDARDS", pathlib.Path("blocks/resp.md"))])
        self.assertIn("Be concise.", c.text)
        self.assertNotIn("{RESPONSE_STANDARDS}", c.text)
        self.assertEqual(c.slots_filled["RESPONSE_STANDARDS"], len("Be concise."))

    def test_unfilled_slot_is_an_error_not_a_passthrough(self):
        # Deliberate divergence from ADK: shipping a literal {RESPONSE_STANDARDS}
        # to the model is never intended, so fail at build time.
        t = self.write("t.md", "{RESPONSE_STANDARDS}")
        with self.assertRaises(SlotError) as cm:
            compile_instruction(t, self.root, [])
        self.assertIn("RESPONSE_STANDARDS", str(cm.exception))

    def test_declared_but_unused_slot_is_an_error(self):
        self.write("blocks/x.md", "x")
        t = self.write("t.md", "no slots here")
        with self.assertRaises(TemplateError) as cm:
            compile_instruction(t, self.root, [Slot("UNUSED", pathlib.Path("blocks/x.md"))])
        self.assertIn("UNUSED", str(cm.exception))

    def test_missing_block_file_names_the_slot(self):
        t = self.write("t.md", "{A}")
        with self.assertRaises(SlotError) as cm:
            compile_instruction(t, self.root, [Slot("A", pathlib.Path("nope.md"))])
        self.assertIn("'A'", str(cm.exception))

    def test_variant_selection(self):
        self.write("blocks/web.md", "WEB")
        self.write("blocks/mobile.md", "MOBILE")
        t = self.write("t.md", "{RESPONSE_STANDARDS}")
        slot = Slot("RESPONSE_STANDARDS",
                    variants={"web": pathlib.Path("blocks/web.md"),
                              "mobile": pathlib.Path("blocks/mobile.md")},
                    select="mobile")
        self.assertIn("MOBILE", compile_instruction(t, self.root, [slot]).text)

    def test_variant_without_select_errors(self):
        self.write("blocks/web.md", "WEB")
        t = self.write("t.md", "{A}")
        slot = Slot("A", variants={"web": pathlib.Path("blocks/web.md")})
        with self.assertRaises(SlotError):
            compile_instruction(t, self.root, [slot])

    def test_unknown_variant_lists_the_valid_ones(self):
        self.write("blocks/web.md", "WEB")
        t = self.write("t.md", "{A}")
        slot = Slot("A", variants={"web": pathlib.Path("blocks/web.md")}, select="tablet")
        with self.assertRaises(SlotError) as cm:
            compile_instruction(t, self.root, [slot])
        self.assertIn("web", str(cm.exception))

    def test_slot_content_containing_a_placeholder_is_not_re_substituted(self):
        # Chained str.replace() re-scans substituted text. Here block
        # A's body contains the literal "{B}"; it must survive as literal text.
        self.write("blocks/a.md", "see {B}")
        t = self.write("t.md", "{A}")
        c = compile_instruction(t, self.root, [Slot("A", pathlib.Path("blocks/a.md"))])
        self.assertEqual(c.text, "see {B}")
        # {B} came from block content, not the template, so it is not treated as
        # an unfilled slot -- and it is not classified as state either.
        self.assertEqual(c.state_keys, {})

    def test_duplicate_slot_declaration_errors(self):
        self.write("blocks/a.md", "a")
        t = self.write("t.md", "{A}")
        with self.assertRaises(TemplateError):
            compile_instruction(t, self.root, [
                Slot("A", pathlib.Path("blocks/a.md")),
                Slot("A", pathlib.Path("blocks/a.md")),
            ])


class TestClassification(_Tmp):
    def test_state_keys_classified_by_optionality(self):
        t = self.write("t.md", "{today} {pageName?} {temp:cart?} {user:x}")
        c = compile_instruction(t, self.root, [])
        self.assertEqual(c.required_state, ["today", "user:x"])
        self.assertEqual(c.optional_state, ["pageName", "temp:cart"])

    def test_key_used_both_ways_is_required(self):
        # If any use is required, the prompt cannot render without it.
        t = self.write("t.md", "{k?} and {k}")
        c = compile_instruction(t, self.root, [])
        self.assertEqual(c.required_state, ["k"])

    def test_passthrough_placeholders_are_reported(self):
        t = self.write("t.md", "{my-key} {ok}")
        c = compile_instruction(t, self.root, [])
        self.assertEqual(c.passthrough, ["my-key"])
        self.assertEqual(c.required_state, ["ok"])

    def test_artifacts_classified(self):
        t = self.write("t.md", "{artifact.a} {artifact.b?}")
        c = compile_instruction(t, self.root, [])
        self.assertEqual(c.artifact_keys, {"a": False, "b": True})


class TestRealProspectShape(_Tmp):
    """A multi-slot prompt fixture: 2 named slots, 12 placeholders."""

    def test_end_to_end(self):
        self.write("blocks/simplicity.md", "Simplicity is current.")
        self.write("blocks/response_web.md", "Max 5 bullets.")
        t = self.write("t.md", """\
<environment>
Current Date: {today}
{pageName?}
{geoDetails?}
{detectedCarrier?}
{language_instruction?}
{pageWiki?}
</environment>
<user_state>
{temp:active_cart_context?}
{current_cart_summary?}
{user:customer_insights?}
{temp:myplan_promotion_note?}
</user_state>
{SIMPLICITY_SOURCE_OF_TRUTH}
{RESPONSE_STANDARDS}
""")
        c = compile_instruction(t, self.root, [
            Slot("SIMPLICITY_SOURCE_OF_TRUTH", pathlib.Path("blocks/simplicity.md")),
            Slot("RESPONSE_STANDARDS",
                 variants={"web": pathlib.Path("blocks/response_web.md")},
                 select="web"),
        ])

        self.assertEqual(c.required_state, ["today"])
        self.assertEqual(len(c.optional_state), 9)
        self.assertEqual(c.passthrough, [])
        self.assertIn("Simplicity is current.", c.text)
        self.assertIn("Max 5 bullets.", c.text)

        # Renders with only the one required key -- the optional nine drop out.
        out = run(render(c.text, {"today": "2026-09-03"}))
        self.assertIn("2026-09-03", out)
        self.assertNotIn("{", out)


class TestProvider(_Tmp):
    def test_provider_interpolates_from_context_state(self):
        t = self.write("t.md", "Hi {name}, {missing?}")
        c = compile_instruction(t, self.root, [])

        class Ctx:
            state = {"name": "Lu"}
            agent_name = "test_agent"

        self.assertEqual(run(build_provider(c)(Ctx())), "Hi Lu, ")

    def test_provider_carries_the_compiled_artifact_for_explain(self):
        t = self.write("t.md", "{today}")
        c = compile_instruction(t, self.root, [])
        p = build_provider(c)
        self.assertIsInstance(p.__adkl_compiled__, CompiledInstruction)

    def test_provider_error_names_the_agent(self):
        t = self.write("t.md", "{needed}")
        c = compile_instruction(t, self.root, [])

        class Ctx:
            state: dict = {}
            agent_name = "example_agent"

        with self.assertRaises(KeyError) as cm:
            run(build_provider(c)(Ctx()))
        self.assertIn("example_agent", str(cm.exception))



class TestLiteralBraces(unittest.TestCase):
    """`{{...}}` documents a shape without looking like a mistyped state key."""

    def _compile(self, text):
        d = pathlib.Path(tempfile.mkdtemp())
        (d / "t.md").write_text(text, encoding="utf-8")
        return instruction.compile_instruction(d / "t.md", d, [])

    def test_escaped_braces_compile_to_one_pair(self):
        c = self._compile('options, each {{label: "A"|"B", text}}')
        # One pair in the finished text: ADK has no escape of its own and
        # returns a non-state-name verbatim, so this is what the model sees.
        self.assertIn('{label: "A"|"B", text}', c.text)
        self.assertNotIn("{{", c.text)

    def test_escaped_braces_are_not_reported_as_passthrough(self):
        c = self._compile("shape: {{a: 1}}")
        self.assertEqual(c.passthrough, [])
        self.assertEqual(sorted(c.state_keys), [])

    def test_a_real_typo_is_still_reported(self):
        # The rule has to keep earning its keep: escaping must not become a way
        # to silence the check for single-braced mistakes.
        c = self._compile("hello {my-key}")
        self.assertEqual(c.passthrough, ["my-key"])

    def test_state_keys_still_interpolate_beside_an_escape(self):
        c = self._compile("{{literal}} and {user:name}")
        self.assertIn("{literal}", c.text)
        self.assertEqual(sorted(c.state_keys), ["user:name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
