"""Opt-in staging of a domain tool's successful custom presentation result."""
from google.adk.tools import FunctionTool

from adkl.presentation_runtime import _CaptureContext
from adkl.widgets import stage


class StagedResultTool(FunctionTool):
    """Keep domain validation and payload semantics; defer delivery to the lifecycle.

    The supplied spec must also be installed on RuntimeBindings. A successful
    tool result must carry a dict in result_field. This does not validate an
    application schema or sanitize private fields; that remains the domain tool.
    """
    def __init__(self, fn, spec, *, result_field="card"):
        if not spec.register:
            raise ValueError("StagedResultTool requires a register-backed widget spec")
        super().__init__(fn)
        self.spec, self.result_field = spec, result_field

    async def run_async(self, *, args, tool_context):
        result = await super().run_async(args=args, tool_context=_CaptureContext(tool_context))
        if not isinstance(result, dict) or result.get("status") not in {"success", "rendered"}:
            return result
        payload = result.get(self.result_field)
        if not isinstance(payload, dict):
            raise ValueError("Successful staged result must contain a presentation object")
        items = list(tool_context.state.get(self.spec.register, []))
        items.append(payload)
        stage(tool_context.state, self.spec, items)
        tool_context.actions.skip_summarization = False
        return {**result, "status": "staged",
                "agent_instruction": "Write brief companion text; the UI is staged for end-of-turn delivery."}
