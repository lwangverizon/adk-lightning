"""Native hosts receive the same packs, audited declarations and stage diagnostics."""
import unittest

from google.adk.agents import LlmAgent
from google.adk.skills import models
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.host import describe_agent
from adkl.retrieval import Match, MatchResult
from tests.support.native import ScriptModel, run


class PackModel(ScriptModel):
    async def generate_content_async(self, request, stream=False):
        from google.adk.models.llm_response import LlmResponse
        if any(p.function_response for p in request.contents[-1].parts or []):
            part = types.Part(text="Here are the steps.")
        else:
            part = types.Part(function_call=types.FunctionCall(
                name="render_steps", args={"steps": ["Gather facts", "Review facts"]}, id="steps"))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class TestNativePresentation(unittest.IsolatedAsyncioTestCase):
    async def test_packs_inspection_and_sink_diagnostics(self):
        delivered = []
        def sink(context, widget_id, payload):
            delivered.append(payload)
        binding = RuntimeBindings(skills=[], widget_sink=sink,
            feature_config={"pipeline": ["staged_widgets"]},
            presentation={"version": 1, "packs": ["adkl/tutoring@1"], "exclude_kinds": ["quiz"]})
        agent = LlmAgent(name="host", model=PackModel(), **binding.agent_kwargs())
        report = describe_agent(agent)["adkl"][0]
        self.assertEqual(report["delivery"], "host_sink")
        self.assertNotIn("present_quiz", [t["name"] for t in report["tools"]])
        _, session = await run(agent)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["type"], "step")
        self.assertTrue(session.state["adkl:widgets:host"]["gates"])

    async def test_explicit_domain_route_is_distinct_and_exclusions_still_apply(self):
        skill = models.Skill(frontmatter=models.Frontmatter(name="support", description="Help"), instructions="Help")
        class Selector:
            async def match(self, text, state):
                return MatchResult([Match("support", 0.0, "lexical")])
        for excluded in ([], ["support"]):
            binding = RuntimeBindings(skills=[skill], selector=Selector(),
                route_skill=lambda **kwargs: "support",
                feature_config={"pipeline": ["skill_retrieval"],
                    "skill_retrieval": {"auto_activate": True, "auto_activate_exclude": excluded}})
            agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
            if excluded:
                with self.assertRaisesRegex(ValueError, "Explicit route"):
                    await run(agent)
            else:
                _, session = await run(agent)
                self.assertEqual(session.state["adkl:last_turn"]["activation_source"], "route")

    def test_pack_conflict_and_missing_bound_tool_fail_construction(self):
        def render_steps():
            return {}
        with self.assertRaisesRegex(ValueError, "conflict"):
            RuntimeBindings(skills=[], tools=[render_steps],
                feature_config={"pipeline": ["staged_widgets"]},
                presentation={"version": 1, "packs": ["adkl/common@1"]})

    def test_inspection_reports_each_staged_spec_reply_contract(self):
        """The role is why a card and its reply agree, or visibly do not.

        A snapshot listing keys alone cannot answer the question a reviewer
        brings to a staged pipeline -- why a widget arrived beside a reply that
        restates it -- and the console renders these specs, so the omission was
        invisible rather than merely terse. An adopted spec keeps whichever role
        its own registry carries, and declares no payload type at all: upstream's
        build decides that, which `null` says and `""` did not.
        """
        from unittest.mock import patch
        from types import SimpleNamespace
        from adkl import widgets as W
        async def build(context, items, request_id):
            return items
        declared = W.WidgetSpec(key="card", widget_id="card", payload_type="fact",
                                register="temp:cards", role=W.Role.DATA_PRIMARY, build=build)
        source = SimpleNamespace(key="nav", build=build, state_items="temp:nav_stage",
                                 presentation_role=W.Role.ACTION_PRIMARY,
                                 resolve_role=lambda state: None)
        with patch.object(W, "_resolve_attr", return_value=[source]):
            adopted = W.adopt("host_private:REGISTRY")[0]
        binding = RuntimeBindings(skills=[], widget_specs=[declared, adopted],
                                  feature_config={"pipeline": ["staged_widgets"]})
        agent = LlmAgent(name="host", model=ScriptModel(), **binding.agent_kwargs())
        self.assertEqual(describe_agent(agent)["adkl"][0]["widgets"], [
            {"key": "card", "register": "temp:cards", "payloadType": "fact",
             "role": "data_primary", "adoptionWarnings": []},
            {"key": "nav", "register": "temp:nav_stage", "payloadType": None,
             "role": "action_primary", "adoptionWarnings": []},
        ])

    def test_callable_instruction_is_not_executed_during_inspection(self):
        def instruction(context):
            raise AssertionError("Inspection must not execute dynamic instructions")
        binding = RuntimeBindings(skills=[])
        agent = LlmAgent(name="host", model=ScriptModel(), instruction=instruction, **binding.agent_kwargs())
        audit = describe_agent(agent)["adkl"][0]["audit"]
        self.assertFalse(audit["dynamicInstructionEvaluated"])
        self.assertTrue(audit["limitations"])

    async def test_custom_domain_result_stages_once_and_preserves_domain_payload(self):
        from google.adk.tools import ToolContext
        from adkl.staged_tool import StagedResultTool
        from adkl.widgets import WidgetSpec, Role
        async def build(context, items, request_id):
            return items
        spec = WidgetSpec(key="custom", widget_id="custom", register="temp:custom",
                          payload_type="custom", role=Role.INPUT_REQUEST, build=build)
        def render_steps(tool_context: ToolContext, steps: list[str]):
            return {"status": "success", "card": {"type": "custom", "question_id": "server-owned"}}
        tool = StagedResultTool(render_steps, spec)
        with self.assertRaisesRegex(ValueError, "requires its widget spec"):
            RuntimeBindings(skills=[], tools=[tool], feature_config={"pipeline": ["staged_widgets"]})
        delivered = []
        def sink(context, widget_id, payload):
            delivered.append(payload)
        binding = RuntimeBindings(skills=[], tools=[tool], core_tools=[tool.name],
            widget_specs=[spec], widget_sink=sink, feature_config={"pipeline": ["staged_widgets"]})
        await run(LlmAgent(name="host", model=PackModel(), **binding.agent_kwargs()))
        self.assertEqual(delivered, [{"type": "custom", "question_id": "server-owned"}])

    async def test_failed_domain_result_is_not_delivered(self):
        from google.adk.tools import ToolContext
        from adkl.staged_tool import StagedResultTool
        from adkl.widgets import WidgetSpec, Role
        async def build(context, items, request_id):
            return items
        spec = WidgetSpec(key="custom", widget_id="custom", register="temp:custom",
                          payload_type="custom", role=Role.INPUT_REQUEST, build=build)
        def render_steps(tool_context: ToolContext, steps: list[str]):
            return {"status": "error", "message": "Persistence refused"}
        delivered = []
        tool = StagedResultTool(render_steps, spec)
        binding = RuntimeBindings(skills=[], tools=[tool], core_tools=[tool.name],
            widget_specs=[spec], widget_sink=lambda context, widget_id, payload: delivered.append(payload),
            feature_config={"pipeline": ["staged_widgets"]})
        await run(LlmAgent(name="host", model=PackModel(), **binding.agent_kwargs()))
        self.assertEqual(delivered, [])
