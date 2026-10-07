"""Native ADK adapters for independently hosted background workers.

Importable without ADK; native dependencies are loaded only on construction/use.
"""
from __future__ import annotations

from collections import Counter
from contextlib import aclosing
import inspect
from typing import Any

from adkl.background_tasks import BackgroundCapability, BackgroundTasks, TaskRecord
from adkl.background_tasks import resolve


def submission_tool(capability: BackgroundCapability, tasks: BackgroundTasks):
    """Build a named launch operation; normal capability guards control discovery."""
    from google.adk.tools.base_tool import BaseTool
    from google.genai import types

    class SubmitTool(BaseTool):
        def _get_declaration(self):
            return types.FunctionDeclaration(
                name=self.name, description=self.description,
                parameters_json_schema={"type": "object", "properties": {
                    "payload": {"type": "object", "description": "Task input."}},
                    "required": ["payload"]})

        async def run_async(self, *, args, tool_context):
            payload = args.get("payload")
            if not isinstance(payload, dict) or set(args) != {"payload"}:
                raise ValueError("Expected a payload object")
            call_id = tool_context.function_call_id
            invocation = tool_context.invocation_id
            if not call_id or not invocation:
                raise ValueError("Background submission requires invocation and call IDs")
            record = await tasks.submit(tool_context, capability, payload,
                                        key=f"{invocation}:{call_id}")
            return record.receipt()

    return SubmitTool(name=capability.name, description=(
        capability.description + " Starts independent background work and returns a task "
        "receipt, not the completed result. Use task status/result operations later."))


def management_tools(tasks: BackgroundTasks):
    """Task access is owner/policy controlled, independent of capability ranking."""
    from google.adk.tools.base_tool import BaseTool
    from google.genai import types

    descriptions = {
        "get_background_task": "Read current status of an owned background task.",
        "list_background_tasks": "List accessible tasks for this conversation.",
        "cancel_background_task": "Request cancellation of an owned task; running work may still be stopping.",
        "get_background_task_result": "Read the result of an owned task, if available. Check status before claiming completion.",
    }

    class ManageTool(BaseTool):
        def _get_declaration(self):
            listing = self.name == "list_background_tasks"
            return types.FunctionDeclaration(name=self.name, description=self.description,
                parameters_json_schema={"type": "object", "properties": {} if listing else {
                    "task_id": {"type": "string"}}, "required": [] if listing else ["task_id"]})

        async def run_async(self, *, args, tool_context):
            if self.name == "list_background_tasks":
                if args:
                    raise ValueError("List takes no arguments")
                return {"tasks": await tasks.list(tool_context)}
            if set(args) != {"task_id"} or not isinstance(args["task_id"], str):
                raise ValueError("Expected task_id")
            if self.name == "cancel_background_task":
                return await tasks.cancel(tool_context, args["task_id"])
            return await tasks.get(tool_context, args["task_id"],
                                   result=self.name == "get_background_task_result")

    return [ManageTool(name=name, description=description)
            for name, description in descriptions.items()]


class ADKTaskExecutor:
    """Run a fresh, host-configured native Runner in a dedicated task session.

    runner_factory(task) must return a fresh Runner owned by this execution.
    message(task) constructs native input; project(events) extracts JSON output.
    No parent context, credentials, tools or callbacks are implicitly inherited.
    ADK 2.11 abort_signal is used when available; older ADK is cancelled by the
    worker at the async task boundary, without promising 2.11 history sealing.
    """

    def __init__(self, *, runner_factory, message, project):
        self.runner_factory, self.message, self.project = runner_factory, message, project

    async def __call__(self, task: TaskRecord, abort) -> Any:
        runner = await resolve(self.runner_factory(task))
        try:
            await runner.session_service.create_session(
                app_name=runner.app_name, user_id=task.owner, session_id=task.task_id)
            kwargs = dict(user_id=task.owner, session_id=task.task_id,
                          new_message=await resolve(self.message(task)))
            if "abort_signal" in inspect.signature(runner.run_async).parameters:
                kwargs["abort_signal"] = abort
            events = []
            async with aclosing(runner.run_async(**kwargs)) as stream:
                async for event in stream:
                    events.append(event)
                    if abort.is_set():
                        break
            if abort.is_set():
                return None
            pending = Counter()
            for event in events:
                if event.error_code or event.interrupted:
                    raise RuntimeError("ADK execution emitted an error or interruption")
                if event.partial:
                    continue
                # Unanswered native tool/input requests are not completed jobs.
                # Use public event parts, not private workflow state or imports.
                for part in (event.content.parts or ()) if event.content else ():
                    if part.function_call:
                        call = part.function_call
                        pending[(call.id, call.name)] += 1
                    if part.function_response:
                        response = part.function_response
                        key = (response.id, response.name)
                        pending[key] = max(0, pending[key] - 1)
            if not events or any(pending.values()):
                raise RuntimeError("ADK execution is incomplete; task resumption is unsupported")
            return await resolve(self.project(events))
        finally:
            await runner.close()
