"""Required native ADK reference contract; no network or model credentials.

Run separately with runtime dependencies installed. Missing ADK is an error,
not a skip. This establishes the behavior Lightning's preview must adopt.
"""
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset


class TestNativeSkillContract(unittest.IsolatedAsyncioTestCase):
    async def test_loading_procedure_exposes_declared_tool_and_reference(self):
        def check_eligibility(product: str) -> dict:
            """Return deterministic eligibility for a synthetic product."""
            return {"eligible": product == "sample"}

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "eligibility"
            (root / "references").mkdir(parents=True)
            (root / "SKILL.md").write_text(
                "---\nname: eligibility\ndescription: Check synthetic product eligibility.\n"
                "metadata:\n  adk_additional_tools: [check_eligibility]\n---\n"
                "Read references/policy.md before checking eligibility.\n")
            (root / "references" / "policy.md").write_text("Only sample products qualify.")
            skill = load_skill_from_dir(root)
            toolset = SkillToolset(skills=[skill], additional_tools=[check_eligibility])
            ctx = SimpleNamespace(agent_name="fixture", invocation_id="turn-1", state={})
            try:
                before = {t.name: t for t in await toolset.get_tools(ctx)}
                self.assertNotIn("check_eligibility", before)
                loaded = await before["load_skill"].run_async(
                    args={"skill_name": "eligibility"}, tool_context=ctx)
                self.assertIn("Read references/policy.md", loaded["instructions"])
                self.assertEqual(ctx.state["_adk_activated_skill_fixture"], ["eligibility"])
                after = {t.name: t for t in await toolset.get_tools(ctx)}
                self.assertIn("check_eligibility", after)
                reference = await after["load_skill_resource"].run_async(
                    args={"skill_name": "eligibility", "file_path": "references/policy.md"},
                    tool_context=ctx)
                self.assertEqual(reference["content"], "Only sample products qualify.")
                # Tool exposure is a native skill mechanism, not authorization.
                fresh_ctx = SimpleNamespace(agent_name="fixture", invocation_id="turn-2", state={})
                self.assertNotIn("check_eligibility", {t.name for t in await toolset.get_tools(fresh_ctx)})
            finally:
                await toolset.close()
