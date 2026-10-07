"""Native models and Runner helpers shared by runtime contract tests."""
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.skills import models
from google.genai import types


class ScriptModel(BaseLlm):
    model: str = "fixture"
    requests: list = []
    explicit: bool = False

    async def generate_content_async(self, request, stream=False):
        self.requests.append(str(request.config.system_instruction))
        if self.explicit and len(self.requests) == 1:
            part = types.Part(function_call=types.FunctionCall(
                name="load_skill", args={"skill_name": "support"}, id="load"))
        else:
            part = types.Part(text="Finished")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def plain_skill(name):
    """A skill with no state injection, for tests isolating pin/ranking
    behaviour from the unrelated `adk_inject_state` substitution mechanism."""
    return models.Skill(frontmatter=models.Frontmatter(
        name=name, description=f"Handle {name}"), instructions=f"Handle {name} requests.")


async def run(agent, state=None):
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="host", user_id="user", state=state or {})
    runner = Runner(agent=agent, app_name="host", session_service=sessions)
    try:
        events = [event async for event in runner.run_async(
            user_id="user", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="help")]))]
        current = await sessions.get_session(app_name="host", user_id="user", session_id=session.id)
        return events, current
    finally:
        await runner.close()


async def run_turns(agent, count, state=None):
    """Like `run`, but drives `count` turns against ONE persisted session --
    for behaviour (like grace-bound pin decay) that only shows up across a
    real turn boundary, not within a single invocation."""
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="host", user_id="user", state=state or {})
    runner = Runner(agent=agent, app_name="host", session_service=sessions)
    try:
        for _ in range(count):
            async for _event in runner.run_async(
                    user_id="user", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="help")])):
                pass
        return await sessions.get_session(app_name="host", user_id="user", session_id=session.id)
    finally:
        await runner.close()
