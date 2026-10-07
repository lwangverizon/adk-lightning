"""Real ADK request objects; no external host imports or provider calls."""
import unittest

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from adkl.history import MISSING_RESULT, repair_tool_history


def call(name="lookup", identifier=None):
    return types.Part(function_call=types.FunctionCall(name=name, id=identifier, args={"input": 1}))


def response(name="lookup", identifier=None, value=1):
    return types.Part(function_response=types.FunctionResponse(name=name, id=identifier, response={"value": value}))


def content(role, *parts):
    return types.Content(role=role, parts=list(parts))


class TestHistoryRepair(unittest.TestCase):
    def test_idless_parallel_pairs_and_source_events_are_untouched(self):
        original = [content("model", call(), call()),
                    content("user", response(value=1)), content("user", response(value=2))]
        before = [c.model_dump() for c in original]
        request = LlmRequest(contents=original)
        repair_tool_history(request)
        self.assertEqual([c.model_dump() for c in original], before)
        self.assertEqual(len(request.contents), 2)
        calls = [p.function_call for p in request.contents[0].parts]
        responses = [p.function_response for p in request.contents[1].parts]
        self.assertEqual([c.id for c in calls], [r.id for r in responses])
        self.assertEqual(len({c.id for c in calls}), 2)
        self.assertEqual([r.response for r in responses], [{"value": 1}, {"value": 2}])

    def test_suffix_normalization_preserves_thinking_and_multimodal_parts(self):
        thought = types.Part(text="reason", thought=True, thought_signature=b"signature")
        media = types.Part(inline_data=types.Blob(mime_type="image/png", data=b"synthetic"))
        original = [content("model", thought, call(identifier="id__thought__sig")),
                    content("user", response(identifier="id"), media)]
        request = LlmRequest(contents=original)
        repair_tool_history(request)
        self.assertEqual(request.contents[0].parts[1].function_call.id, "id")
        self.assertEqual(original[0].parts[1].function_call.id, "id__thought__sig")
        self.assertEqual(request.contents[0].parts[0], thought)
        self.assertEqual(request.contents[2].parts[0], media)

    def test_no_matching_against_future_call_or_across_human_turn(self):
        request = LlmRequest(contents=[content("user", response(identifier="same")),
            content("model", call(identifier="same")),
            content("user", types.Part(text="new question")),
            content("user", response(identifier="same"))])
        report = repair_tool_history(request)
        self.assertEqual(report["dropped_responses"], 2)
        self.assertEqual(report["missing_results"], 1)
        self.assertEqual(request.contents[1].parts[0].function_response.response, {"error": MISSING_RESULT})
        self.assertEqual(request.contents[-1].parts[0].text, "new question")

    def test_partial_parallel_results_get_unknown_outcome_in_same_response_batch(self):
        request = LlmRequest(contents=[content("model", call(identifier="a"), call(identifier="b")),
            content("user", response(identifier="b", value=2))])
        report = repair_tool_history(request)
        self.assertEqual(report["missing_results"], 1)
        result = request.contents[1].parts
        self.assertIn("unknown", result[0].function_response.response["error"])
        self.assertEqual(result[1].function_response.response, {"value": 2})

    def test_mismatched_names_and_explicit_ids_are_not_paired(self):
        for part in (response(name="other", identifier="a"), response(identifier="b")):
            request = LlmRequest(contents=[content("model", call(identifier="a")), content("user", part)])
            report = repair_tool_history(request)
            self.assertEqual(report["dropped_responses"], 1)
            self.assertEqual(report["missing_results"], 1)

    def test_one_sided_ids_collision_and_idempotence(self):
        request = LlmRequest(contents=[content("model", call(), call(identifier="adkl-tool-0")),
            content("user", response(identifier="x"), response()),
            content("model", call(identifier="x")), content("user", response(identifier="x"))])
        repair_tool_history(request)
        ids = [p.function_call.id for c in request.contents for p in c.parts if p.function_call]
        self.assertEqual(len(ids), len(set(ids)))
        before = request.model_dump()
        report = repair_tool_history(request)
        self.assertEqual(request.model_dump(), before)
        self.assertEqual(report, {"assigned_ids": 0, "dropped_responses": 0, "missing_results": 0})

    def test_exact_id_wins_over_name_only_candidate(self):
        request = LlmRequest(contents=[content("model", call(identifier="a"), call()),
            content("user", response(value=0), response(identifier="a", value=1))])
        repair_tool_history(request)
        self.assertEqual([p.function_response.response["value"] for p in request.contents[1].parts], [1, 0])

    def test_invalid_call_does_not_partially_mutate_request(self):
        request = LlmRequest(contents=[content("user", call())])
        before = request.model_dump()
        with self.assertRaises(ValueError):
            repair_tool_history(request)
        self.assertEqual(request.model_dump(), before)

    def test_idless_call_cannot_consume_later_explicit_calls_result(self):
        request = LlmRequest(contents=[content("model", call(), call(identifier="b")),
            content("user", response(identifier="b", value=2), response(value=1))])
        repair_tool_history(request)
        self.assertEqual([p.function_response.response["value"] for p in request.contents[1].parts], [1, 2])
