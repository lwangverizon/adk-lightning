"""Audit a real Runner request after a late callback, without a live model."""
import unittest

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from adkl.instruction_audit import InstructionSpan, inspect_request

KERNEL = "Answer from verified evidence."
LATE = "For this device task, first search products and then rank every result."


class CaptureModel(BaseLlm):
    model: str = "offline"
    reports: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        spans = [InstructionSpan("system", 0, len(KERNEL), "system", "universal", "kernel")]
        # Known authored base only: the callback's later addition must be flagged.
        for group in llm_request.config.tools or []:
            for declaration in group.function_declarations or []:
                text = declaration.description or ""
                if text:
                    spans.append(InstructionSpan("tool:" + declaration.name, 0,
                        len(text), "tool", "interface", declaration.name))
        before = llm_request.model_dump_json()
        self.reports.append(inspect_request(llm_request, spans=spans,
            active_owners=[], required_owners={"search_supports": ["support"]}, phase="cold"))
        assert before == llm_request.model_dump_json()
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Clarify scope.")]))


class TestInstructionAuditNative(unittest.IsolatedAsyncioTestCase):
    async def test_final_model_boundary_sees_unattributed_callback_and_bypass(self):
        def search_supports() -> dict:
            """Read public support evidence; no mutations."""
            raise AssertionError("No domain tool should execute")

        def late_callback(callback_context, llm_request):
            llm_request.append_instructions([LATE])

        model = CaptureModel()
        agent = LlmAgent(name="fixture", instruction=KERNEL, model=model,
            tools=[search_supports], before_model_callback=late_callback)
        runner = InMemoryRunner(agent=agent, app_name="layer_fixture")
        try:
            session = await runner.session_service.create_session(app_name="layer_fixture", user_id="synthetic")
            events = [event async for event in runner.run_async(user_id="synthetic", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="How do I remove the fee?")]))]
            self.assertTrue(events)
            self.assertEqual(len(model.reports), 1)
            rules = {row["rule"] for row in model.reports[0]["findings"]}
            self.assertIn("INSTRUCTION_PROVENANCE_INCOMPLETE", rules)
            self.assertIn("INSTRUCTION_TOOL_OWNER_BYPASS", rules)
            self.assertNotIn(LATE, str(model.reports))
        finally:
            await runner.close()
