"""Unified Capability Selection: native catalog without retrieval.

For shared shortlisting, see examples.capability_selection.
Run: uv run --no-sync python -m examples.native_capabilities
"""

import asyncio

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel, Field

from adkl.capability_bindings import CapabilityBindings


class SearchInput(BaseModel):
    query: str


def lookup(ctx, node_input: SearchInput):
    return {"query": node_input.query, "items": ["fixture phone"]}


class DemoModel(BaseLlm):
    calls: int = Field(default=0)

    async def generate_content_async(self, request, stream=False):
        self.calls += 1
        part = (types.Part(function_call=types.FunctionCall(
            name="discover_devices", args={"query": "camera phones"}))
            if self.calls == 1 else types.Part(text="Found a fixture phone."))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def build_agent():
    skill = Skill(frontmatter=Frontmatter(name="shopping-help", description="Explain shopping policies"),
                  instructions="Answer from the supplied shopping policy.")
    workflow = Workflow(
        name="discover_devices", description="Find devices matching shopping constraints",
        input_schema=SearchInput,
        edges=[("START", FunctionNode(name="lookup", func=lookup))],
    )
    specialist = LlmAgent(name="support", description="Diagnose a service problem",
                          instruction="Use the support procedure.", model=DemoModel(model="fixture"))
    binding = CapabilityBindings(skills=[skill], workflows=[workflow], sub_agents=[specialist])
    # merge_into preserves host model/configuration. Register tools and agents
    # on the binding; it installs native execution and the shared lifecycle once.
    return LlmAgent(**binding.merge_into({
        "name": "prospect", "model": DemoModel(model="fixture"),
        "instruction": "Help users shop.",
    }))


async def main():
    runner = InMemoryRunner(agent=build_agent(), app_name="capabilities_demo")
    try:
        session = await runner.session_service.create_session(app_name="capabilities_demo", user_id="demo")
        async for event in runner.run_async(user_id="demo", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="Find camera phones")])):
            for part in event.content.parts or [] if event.content else []:
                if part.text:
                    print(part.text)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
