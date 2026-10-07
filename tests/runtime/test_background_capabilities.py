"""Real ADK request/dispatch and child execution, no credentials or live model."""
import asyncio
import json

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import START, Workflow
from google.genai import types
from pydantic import BaseModel
import pytest

from adkl.background_adk import ADKTaskExecutor, management_tools
from adkl.background_tasks import (
    BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity, TaskWorker,
)
from adkl.capability_bindings import CapabilityBindings
from adkl.capability_policy import CapabilityDecision
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection
from tests.support.native import run


class ParentModel(BaseLlm):
    model: str = "fixture"
    requests: list = []
    launch: bool = True

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        if self.launch and len(self.requests) == 1:
            part = types.Part(function_call=types.FunctionCall(
                id="launch", name="research", args={"payload": {"question": "fixture"}}))
        else:
            part = types.Part(text="Submitted; you may continue chatting.")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def setup(tmp_path):
    backend = SQLiteTaskBackend(tmp_path / "tasks.db")
    tasks = BackgroundTasks(backend,
        identity=lambda ctx: TaskIdentity("tenant:user", "chat"),
        authorize=lambda identity, operation, capability: True)
    cap = BackgroundCapability("research", "Research a topic privately", "v1")
    return backend, tasks, cap


def test_native_retrieval_launch_then_topic_switch_keeps_management(tmp_path):
    async def exercise():
        backend, tasks, cap = setup(tmp_path)
        catalogs = []
        def rank(ctx, catalog):
            catalogs.append(catalog)
            return CapabilitySelection(("research",))
        model = ParentModel()
        binding = CapabilityBindings(background_capabilities=[cap], tasks=tasks,
            retrieval=CapabilityRetrieval(rank))
        _, _ = await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        records = await backend.list(TaskIdentity("tenant:user", "chat"))
        assert len(records) == 1 and records[0].status == "queued"
        assert {d.name for d in catalogs[0]} == {"research"}
        assert "research" in model.requests[0].tools_dict
        assert "list_background_tasks" in model.requests[0].tools_dict
        responses = [part.function_response.response
                     for content in model.requests[-1].contents for part in content.parts or []
                     if part.function_response]
        assert responses[-1]["task_id"] == records[0].task_id

        # Fresh binding/agent as required by native ownership; same durable registry.
        switched = ParentModel(launch=False)
        new = CapabilityBindings(background_capabilities=[cap], tasks=tasks,
            retrieval=CapabilityRetrieval(lambda ctx, catalog: CapabilitySelection(())))
        await run(LlmAgent(name="next_host", model=switched, **new.agent_kwargs()))
        request = switched.requests[0]
        assert "research" not in request.tools_dict
        for name in ("get_background_task", "list_background_tasks", "cancel_background_task",
                     "get_background_task_result"):
            assert name in request.tools_dict
        assert "Research a topic privately" not in str(request.config.system_instruction)
        tool = request.tools_dict["get_background_task"]
        assert (await tool.run_async(args={"task_id": records[0].task_id},
                                    tool_context=None))["status"] == "queued"
    asyncio.run(exercise())


def test_dispatch_revocation_blocks_previously_selected_launch(tmp_path):
    async def exercise():
        backend, tasks, cap = setup(tmp_path)
        class Policy:
            def expose(self, ctx, catalog):
                return [d.name for d in catalog]
            def authorize(self, ctx, request):
                return CapabilityDecision(False, "revoked")
        model = ParentModel()
        binding = CapabilityBindings(background_capabilities=[cap], tasks=tasks,
            policy=Policy(), retrieval=CapabilityRetrieval(
                lambda ctx, catalog: CapabilitySelection(("research",))))
        await run(LlmAgent(name="host", model=model, **binding.agent_kwargs()))
        assert await backend.list(TaskIdentity("tenant:user", "chat")) == []
    asyncio.run(exercise())


def test_management_tools_enforce_ownership_outside_retrieval(tmp_path):
    async def exercise():
        backend, tasks, cap = setup(tmp_path)
        task = await backend.submit(TaskIdentity("another-owner", "chat"), cap, {}, "call")
        for tool in management_tools(tasks):
            if tool.name == "list_background_tasks":
                assert await tool.run_async(args={}, tool_context=None) == {"tasks": []}
            else:
                with pytest.raises(PermissionError):
                    await tool.run_async(args={"task_id": task.task_id}, tool_context=None)
    asyncio.run(exercise())


def test_worker_runs_native_adk_graph_with_isolated_session(tmp_path):
    class Input(BaseModel):
        question: str
    calls = []
    def analyze(node_input: Input) -> str:
        calls.append(node_input.question)
        return "report:" + node_input.question

    async def exercise():
        backend, _, cap = setup(tmp_path)
        task = await backend.submit(TaskIdentity("tenant:user", "parent-chat"), cap,
                                    {"question": "synthetic"}, "call")
        sessions = InMemorySessionService()
        def runner_factory(record):
            workflow = Workflow(name="research_graph", input_schema=Input,
                                edges=[(START, analyze)])
            return Runner(agent=workflow, app_name="worker", session_service=sessions)
        executor = ADKTaskExecutor(runner_factory=runner_factory,
            message=lambda task: types.Content(role="user", parts=[
                types.Part(text=json.dumps(task.payload))]),
            project=lambda events: next(event.output for event in reversed(events)
                                         if getattr(event, "output", None) is not None))
        worker = TaskWorker(backend, {("research", "v1"): executor},
                            authorize=lambda task: True)
        await worker.run_once()
        completed = await backend.get("tenant:user", task.task_id)
        assert completed.status == "succeeded", completed
        assert completed.result == "report:synthetic"
        assert calls == ["synthetic"]
        child = await sessions.get_session(app_name="worker", user_id="tenant:user",
                                           session_id=task.task_id)
        assert child is not None and child.id != task.session
    asyncio.run(exercise())


def test_cancel_native_runner_does_not_publish_result(tmp_path):
    async def exercise():
        backend, _, cap = setup(tmp_path)
        task = await backend.submit(TaskIdentity("tenant:user", "chat"), cap, {}, "call")
        started = asyncio.Event()
        sessions = InMemorySessionService()
        class WaitingModel(BaseLlm):
            model: str = "fixture"
            async def generate_content_async(self, request, stream=False):
                started.set()
                await asyncio.Event().wait()
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="late")]))
        def runner_factory(record):
            return Runner(agent=LlmAgent(name="waiting", model=WaitingModel()),
                          app_name="worker", session_service=sessions)
        executor = ADKTaskExecutor(runner_factory=runner_factory,
            message=lambda task: types.Content(role="user", parts=[types.Part(text="work")]),
            project=lambda events: "must not publish")
        worker = TaskWorker(backend, {("research", "v1"): executor},
            authorize=lambda task: True, cancel_grace_seconds=0.1)
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 3)
        await backend.cancel("tenant:user", task.task_id)
        await asyncio.wait_for(running, 3)
        result = await backend.get("tenant:user", task.task_id)
        assert result.status == "cancelled" and result.result is None
        # On the locked ADK 2.11 path cancellation must seal retained history,
        # not merely suppress publication in the task database.
        import inspect
        if "abort_signal" in inspect.signature(Runner.run_async).parameters:
            session = await sessions.get_session(app_name="worker", user_id="tenant:user",
                                                  session_id=task.task_id)
            assert any(event.error_code == "INVOCATION_ABORTED" for event in session.events)
    asyncio.run(exercise())


def test_unsupported_config_and_name_collision_fail_early(tmp_path):
    _, tasks, cap = setup(tmp_path)
    with pytest.raises(ValueError, match="require tasks"):
        CapabilityBindings(background_capabilities=[cap])
    with pytest.raises(ValueError, match="Duplicate/reserved"):
        CapabilityBindings(tasks=tasks, background_capabilities=[
            BackgroundCapability("get_background_task", "Collision", "v1")])
