"""Same-session parent turns, process isolation and native completion contracts."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent
from google.adk.events import RequestInput
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, Workflow
from google.genai import types
from pydantic import Field

from adkl.background_adk import ADKTaskExecutor
from adkl.background_tasks import BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity, TaskWorker
from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection


class JourneyModel(BaseLlm):
    model: str = "fixture"
    requests: list = Field(default_factory=list)
    task_id: str = ""
    phase: str = "launch"
    call_sent: bool = False

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        responses = [p.function_response for c in request.contents for p in c.parts or []
                     if p.function_response]
        receipts = [r.response for r in responses if r.name == "research"]
        if receipts:
            self.task_id = receipts[-1]["task_id"]
        if not self.call_sent and self.phase in {"launch", "result"}:
            self.call_sent = True
            part = types.Part(function_call=types.FunctionCall(
                id=self.phase, name="research" if self.phase == "launch" else "get_background_task_result",
                args={"payload": {"question": "fixture"}} if self.phase == "launch" else {"task_id": self.task_id}))
        else:
            part = types.Part(text="Accepted" if self.phase == "launch" else "Unrelated reply" if self.phase == "chat" else "Report retrieved")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def test_same_parent_session_keeps_chatting_then_retrieves_result(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        identity = TaskIdentity("tenant:user", "parent")
        tasks = BackgroundTasks(backend, identity=lambda ctx: identity,
                                authorize=lambda identity, op, cap: True)
        model = JourneyModel()
        catalogs = []
        def rank(ctx, catalog):
            catalogs.append(catalog)
            return CapabilitySelection(("research",) if model.phase == "launch" else ())
        def unrelated_lookup() -> str:
            """Read unrelated information inline."""
            return "unrelated"
        binding = CapabilityBindings(background_capabilities=[BackgroundCapability(
            "research", "Research synthetic evidence", "v1")], tasks=tasks,
            tools=[unrelated_lookup], retrieval=CapabilityRetrieval(rank))
        runner = Runner(agent=LlmAgent(name="parent", model=model, **binding.agent_kwargs()),
                        app_name="chat", session_service=InMemorySessionService())
        await runner.session_service.create_session(app_name="chat", user_id=identity.owner,
                                                    session_id=identity.session)
        async def turn(phase):
            model.phase, model.call_sent = phase, False
            return [event async for event in runner.run_async(user_id=identity.owner,
                session_id=identity.session, new_message=types.Content(role="user", parts=[types.Part(text=phase)]))]
        started, release = asyncio.Event(), asyncio.Event()
        async def execute(task, abort):
            started.set()
            await release.wait()
            return {"report": "synthetic result"}
        worker = TaskWorker(backend, {("research", "v1"): execute}, authorize=lambda task: True)
        running = None
        try:
            await turn("launch")
            assert {d.name for d in catalogs[0]} == {"research", "unrelated_lookup"}
            initial = model.requests[0]
            declarations = [d for group in initial.config.tools or [] for d in group.function_declarations or []]
            launch = next(d for d in declarations if d.name == "research")
            assert "receipt, not the completed result" in launch.description
            assert "unrelated_lookup" not in initial.tools_dict
            assert model.task_id
            assert (await backend.get(identity.owner, model.task_id)).status == "queued"
            running = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(started.wait(), 3)
            request_index = len(model.requests)
            await asyncio.wait_for(turn("chat"), 3)
            assert not running.done()
            request = model.requests[request_index]
            assert "research" not in request.tools_dict
            declarations = [d for group in request.config.tools or [] for d in group.function_declarations or []]
            assert "research" not in {d.name for d in declarations}
            assert "get_background_task_result" in {d.name for d in declarations}
            assert "Research synthetic evidence" not in str(request.config.system_instruction)
            release.set()
            await asyncio.wait_for(running, 3)
            events = await turn("result")
            results = [p.function_response.response for e in events if e.content
                       for p in e.content.parts or [] if p.function_response
                       and p.function_response.name == "get_background_task_result"]
            assert results[-1]["status"] == "succeeded"
            assert results[-1]["result"] == {"report": "synthetic result"}
            assert len(catalogs) == 3  # one rank per invocation, same binding/session
        finally:
            if running:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await runner.close()
    asyncio.run(exercise())


def test_adk_stream_closes_before_runner_resources_on_abort():
    async def exercise():
        order = []
        abort = asyncio.Event()
        class FixtureRunner:
            app_name = "fixture"
            def __init__(self):
                self.session_service = self
            async def create_session(self, **kwargs):
                pass
            async def run_async(self, *, abort_signal, **kwargs):
                try:
                    abort_signal.set()
                    yield SimpleNamespace()
                finally:
                    await asyncio.sleep(0)
                    order.append("stream_closed")
            async def close(self):
                order.append("runner_closed")
        executor = ADKTaskExecutor(runner_factory=lambda t: FixtureRunner(),
                                  message=lambda t: None, project=lambda e: pytest.fail("aborted"))
        assert await executor(SimpleNamespace(owner="owner", task_id="task"), abort) is None
        assert order == ["stream_closed", "runner_closed"]
    asyncio.run(exercise())


@pytest.mark.parametrize("needs_input", [False, True])
def test_native_completion_and_human_input_are_distinct(tmp_path, needs_input):
    class AnswerModel(BaseLlm):
        model: str = "fixture"
        async def generate_content_async(self, request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Finished report")]))
    def ask():
        return RequestInput(message="Approval needed")
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        cap = BackgroundCapability("research", "Research", "v1")
        task = await backend.submit(TaskIdentity("owner", "chat"), cap, {}, "call")
        projected = []
        def project(events):
            projected.append(events)
            return "complete"
        def factory(task):
            agent = Workflow(name="approval", edges=[(START, ask)]) if needs_input else LlmAgent(name="researcher", model=AnswerModel())
            return Runner(agent=agent, app_name="worker", session_service=InMemorySessionService())
        executor = ADKTaskExecutor(runner_factory=factory,
            message=lambda task: types.Content(role="user", parts=[types.Part(text="work")]), project=project)
        worker = TaskWorker(backend, {("research", "v1"): executor}, authorize=lambda task: True)
        await worker.run_once()
        record = await backend.get("owner", task.task_id)
        assert record.status == ("failed" if needs_input else "succeeded")
        assert bool(projected) is not needs_input
        assert record.result == (None if needs_input else "complete")
    asyncio.run(exercise())


def test_submission_worker_and_result_survive_process_boundaries(tmp_path):
    """Submit via native parent; exit process; native worker then separate reader."""
    root = Path(__file__).resolve().parents[2]
    database = str(tmp_path / "tasks.db")
    common = '''
import asyncio, json, sys
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from adkl.background_tasks import BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity, TaskWorker
from adkl.background_adk import ADKTaskExecutor
from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection
backend = SQLiteTaskBackend(sys.argv[1])
identity = TaskIdentity("owner", "parent")
cap = BackgroundCapability("research", "Research", "v1")
tasks = BackgroundTasks(backend, identity=lambda ctx: identity, authorize=lambda i,o,c: True)
'''
    scripts = [common + '''
class Model(BaseLlm):
    model: str = "fixture"
    called: bool = False
    async def generate_content_async(self, request, stream=False):
        if not self.called:
            self.called = True
            part = types.Part(function_call=types.FunctionCall(id="launch", name="research", args={"payload": {}}))
        else:
            part = types.Part(text="Accepted")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))
async def main():
    binding = CapabilityBindings(background_capabilities=[cap], tasks=tasks,
        retrieval=CapabilityRetrieval(lambda ctx,catalog: CapabilitySelection(("research",))))
    runner = Runner(agent=LlmAgent(name="parent", model=Model(), **binding.agent_kwargs()),
        app_name="parent", session_service=InMemorySessionService())
    try:
        await runner.session_service.create_session(app_name="parent", user_id="owner", session_id="parent")
        async for event in runner.run_async(user_id="owner", session_id="parent",
            new_message=types.Content(role="user", parts=[types.Part(text="launch")])):
            pass
    finally:
        await runner.close()
    print(json.dumps((await tasks.list(None))[0]))
asyncio.run(main())
''', common + '''
class Model(BaseLlm):
    model: str = "fixture"
    async def generate_content_async(self, request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="report")]))
async def main():
    executor = ADKTaskExecutor(runner_factory=lambda t: Runner(
        agent=LlmAgent(name="researcher", model=Model()), app_name="worker", session_service=InMemorySessionService()),
        message=lambda t: types.Content(role="user", parts=[types.Part(text="work")]), project=lambda events: {"report": "done"})
    assert await TaskWorker(backend, {("research", "v1"): executor}, authorize=lambda t: True).run_once()
asyncio.run(main())
''', common + '''
async def main():
    records = await tasks.list(None)
    print(json.dumps(await tasks.get(None, records[0]["task_id"], result=True)))
asyncio.run(main())
''']
    outputs = [subprocess.run([sys.executable, "-c", code, database], cwd=root,
                             capture_output=True, text=True, timeout=30, check=True).stdout for code in scripts]
    submitted = json.loads(outputs[0].splitlines()[-1])
    result = json.loads(outputs[-1].splitlines()[-1])
    assert submitted["status"] == "queued"
    assert result["task_id"] == submitted["task_id"]
    assert result["status"] == "succeeded" and result["result"] == {"report": "done"}
