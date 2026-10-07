"""Native skills and tools with shared retrieval; no files or credentials needed."""
import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.skills import models
from google.genai import types

from adkl.bindings import RuntimeBindings
from examples.sdk_smoke import FixtureModel, lookup


class FixtureEmbedder:
    async def embed(self, text):
        return [1.0, 0.0]


async def main():
    skill = models.Skill(frontmatter=models.Frontmatter(
        name="inventory", description="Look up inventory",
        metadata={"adk_additional_tools": ["lookup"]}),
        instructions="Use lookup to answer inventory questions.")
    binding = RuntimeBindings(
        skills=[skill], tools=[lookup], vectors={"inventory": [1.0, 0.0]},
        embedder=FixtureEmbedder(), feature_config={
            "pipeline": ["skill_retrieval", "history_slicing", "tool_result_projection"],
            "skill_retrieval": {"auto_activate": True, "min_skills_for_retrieval": 0},
            "tool_result_projection": {"tools": {
                "lookup": {"path": ["rows"], "fields": ["id", "available"]}}},
        })
    host_kwargs = {"name": "native_binding_demo", "model": FixtureModel(),
                   "instruction": "Answer using the inventory skill."}
    assert binding.inspect()["skills"][0]["name"] == "inventory"
    agent = LlmAgent(**binding.merge_into(host_kwargs))
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=agent.name, user_id="demo")
    runner = Runner(agent=agent, app_name=agent.name, session_service=sessions)
    try:
        async for event in runner.run_async(user_id="demo", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text="lookup")])):
            for part in event.content.parts if event.content else []:
                if part.text:
                    print(part.text)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
