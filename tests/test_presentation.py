"""Presentation authoring stays independent of native ADK and project imports."""
import unittest
import json
import pathlib
from types import SimpleNamespace

from adkl import presentation as p


class TestPresentation(unittest.TestCase):
    def test_flutter_fixture_matches_shared_converter(self):
        from adkl.a2ui import convert_all
        path = pathlib.Path(__file__).resolve().parents[1] / "console/test/fixtures/shared_quiz.json"
        fixture = json.loads(path.read_text())
        source = {k: v for k, v in fixture.items() if k != "a2ui"}
        self.assertEqual(convert_all([source], builtin_kinds=p.PACKS["adkl/tutoring@1"])[0], fixture)

    def doc(self, **block):
        return {"features": {"pipeline": ["staged_widgets"]},
                "presentation": {"version": 1, "packs": ["adkl/tutoring@1"], **block}}

    def test_catalog_is_generated_from_selected_pack(self):
        cat = p.extend_catalog({"tools": [{"name": "draw_question"}]},
                              self.doc(bindings={"draw_question": {"kind": "quiz", "result_field": "card"}}))
        names = {t["name"] for t in cat["tools"]}
        self.assertIn("render_steps", names)
        self.assertNotIn("present_quiz", names)
        self.assertNotIn("present_products", names)
        self.assertTrue(cat["tools"][0]["emits_widget"]["value"])

    def test_catalog_collision_is_explicit(self):
        with self.assertRaisesRegex(p.PresentationError, "conflicts"):
            p.extend_catalog({"tools": [{"name": "render_steps"}]}, self.doc())

    def test_invalid_kinds_are_validation_errors(self):
        with self.assertRaises(p.PresentationError):
            p.configuration(self.doc(bindings={"draw": {"kind": [], "result_field": "card"}}))
        with self.assertRaises(p.PresentationError):
            p.validate({"type": []}, {"quiz"})
        with self.assertRaises(p.PresentationError):
            p.validate({"type": "quiz", "question": "x", "options": []}, {"quiz"})

    def test_queue_is_ordered_deduplicated_and_validated_before_mutation(self):
        ctx = SimpleNamespace(state={}, function_call_id="a")
        first = {"type": "step", "steps": ["Draw groups", "Count them"]}
        p.stage(ctx, first, {"step"})
        p.stage(ctx, first, {"step"})
        self.assertEqual(ctx.state[p.QUEUE], [first])
        with self.assertRaises(p.PresentationError):
            p.stage(ctx, {"type": "step", "steps": []}, {"step"})
        ctx.function_call_id = "b"
        second = {"type": "fact", "title": "Fact", "text": "Three groups of four is twelve."}
        p.stage(ctx, second, {"fact"})
        self.assertEqual(ctx.state[p.QUEUE], [first, second])

    def test_quiz_answer_is_not_sent_to_display(self):
        value = p.validate({"type": "quiz", "question": "2+3?", "answer": "B",
                            "options": [{"label": "A", "text": "4"}, {"label": "B", "text": "5"}]}, {"quiz"})
        self.assertNotIn("answer", value)
        result = p.convert(value, "q")
        column = next(c for c in result["components"] if c["component"] == "Column")
        buttons = [c for c in result["components"] if c["component"] == "Button"]
        self.assertTrue(all(c["child"] not in column["children"] for c in buttons))
