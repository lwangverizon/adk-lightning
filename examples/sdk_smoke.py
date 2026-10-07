"""Run a synthetic native ADK tool turn through ADKL, without credentials."""
import asyncio

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from adkl.adapters import build_callback_adapter


class FixtureModel(BaseLlm):
    model: str = "synthetic"

    async def generate_content_async(self, llm_request, stream=False):
        results = [part.function_response for content in llm_request.contents
                   for part in content.parts or [] if part.function_response]
        if results:
            expected = {"rows": [{"id": "demo", "available": False}]}
            if results[-1].response != expected:
                raise RuntimeError(f"Unexpected projection: {results[-1].response}")
            part = types.Part(text="PASS: the model saw id and available; internal_notes was omitted.")
        else:
            part = types.Part(function_call=types.FunctionCall(name="lookup", args={}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def lookup() -> dict:
    """Return synthetic inventory; no external systems are contacted."""
    return {"rows": [{"id": "demo", "available": False, "internal_notes": "synthetic detail"}]}


async def main():
    agent = LlmAgent(name="sdk_demo", model=FixtureModel(), tools=[lookup],
                     **build_callback_adapter({
                         "pipeline": ["tool_result_projection"],
                         "tool_result_projection": {"tools": {
                             "lookup": {"path": ["rows"], "fields": ["id", "available"]}}}}))
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
