"""Unified Capability Selection: one shortlist, parent choice, native execution.

Run: uv run --no-sync python -m examples.capability_selection
The ranker and model are synthetic fixtures, not a production routing policy.
"""

import asyncio
import json

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel

from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection


class SearchInput(BaseModel):
    query: str


class DemoModel(BaseLlm):
    invoke_workflow: bool
    calls: int = 0

    async def generate_content_async(self, request, stream=False):
        self.calls += 1
        # Check the actual request, not just binding inspection declarations.
        instructions = str(request.config.system_instruction)
        catalog = json.loads(instructions.split("<capabilities>\n", 1)[1]
                             .split("\n</capabilities>", 1)[0])
        assert {d["name"] for d in catalog} == (
            {"discover_devices"} if self.invoke_workflow else set())
        assert "support" not in request.tools_dict
        assert "PRIVATE_SKILL_BODY" not in instructions
        assert "PRIVATE_SPECIALIST_PROMPT" not in instructions
        if self.invoke_workflow:
            assert "discover_devices" in request.tools_dict
            if self.calls == 1:
                part = types.Part(function_call=types.FunctionCall(
                    name="discover_devices", args={"query": "camera phones"}))
            else:
                assert "fixture phone" in str(request.contents)
                part = types.Part(text="Found a fixture phone.")
        else:
            assert "discover_devices" not in request.tools_dict
            part = types.Part(text="What would you like help with?")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def run_case(query: str, *, expect_workflow: bool):
    """Run one independent native session and assert exposure and execution."""
    ranked_catalogs, selections, workflow_calls = [], [], []

    def rank(context, catalog):
        ranked_catalogs.append(catalog)
        # The host supplies request text here via session state. Real hosts own
        # embeddings/indexes, history weighting and calibrated ranking policy.
        text = context.state["demo_query"].lower()
        names = tuple(d.name for d in catalog
                      if "devices" in text and d.kind == "workflow")
        result = CapabilitySelection(names, fallback=not names)
        selections.append(result)
        return result

    def lookup(ctx, node_input: SearchInput):
        workflow_calls.append(node_input.query)
        return {"items": ["fixture phone"]}

    skill = Skill(frontmatter=Frontmatter(
        name="shopping-help", description="Explain shopping policies"),
        instructions="PRIVATE_SKILL_BODY")
    workflow = Workflow(
        name="discover_devices", description="Find devices matching constraints",
        input_schema=SearchInput,
        edges=[("START", FunctionNode(name="lookup", func=lookup))],
    )
    child_model = DemoModel(model="fixture", invoke_workflow=False)
    specialist = LlmAgent(
        name="support", description="Diagnose service problems", mode="single_turn",
        instruction="PRIVATE_SPECIALIST_PROMPT", model=child_model,
    )
    binding = CapabilityBindings(
        skills=[skill], workflows=[workflow], sub_agents=[specialist],
        retrieval=CapabilityRetrieval(rank),
    )
    model = DemoModel(model="fixture", invoke_workflow=expect_workflow)
    agent = LlmAgent(**binding.merge_into({
        "name": "parent", "model": model,
        "instruction": "Choose a relevant capability or ask for clarification.",
    }))
    runner = InMemoryRunner(agent=agent, app_name="selection_demo")
    replies = []
    try:
        session = await runner.session_service.create_session(
            app_name="selection_demo", user_id="demo", state={"demo_query": query})
        async for event in runner.run_async(
            user_id="demo", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=query)]),
        ):
            replies.extend(part.text for part in
                           (event.content.parts or [] if event.content else []) if part.text)
        # All kinds share one ranking, even across subsequent model/tool calls.
        assert len(ranked_catalogs) == 1
        assert {d.kind for d in ranked_catalogs[0]} == {"skill", "workflow", "agent"}
        assert child_model.calls == 0
        if expect_workflow:
            assert selections == [CapabilitySelection(("discover_devices",))]
            assert workflow_calls == ["camera phones"]
            assert "Found a fixture phone." in replies
            assert model.calls == 2  # Workflow data returns to the parent.
        else:
            assert selections == [CapabilitySelection((), fallback=True)]
            assert workflow_calls == []
            assert "What would you like help with?" in replies
            assert model.calls == 1  # Empty fallback does not expose everything.
    finally:
        await runner.close()


async def main():
    await run_case("Find devices with a good camera", expect_workflow=True)
    await run_case("Hello", expect_workflow=False)
    print("PASS: Unified Capability Selection — mixed shortlist, native workflow, "
          "unrelated operations hidden, explicit empty fallback.")


if __name__ == "__main__":
    asyncio.run(main())
