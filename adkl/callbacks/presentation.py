"""Current widget guidance in model-facing tool results."""
from __future__ import annotations



def make_contract_channel(specs: list, on_finding=None):
    """Attach current widget guidance to the result for a subsequent model call.

    A skip_summarization tool has no subsequent model call; this cannot reshape
    an already completed reply. No invocation-wide flag suppresses newer state.
    """
    from adkl import widgets as widgets_mod

    def after_tool(tool=None, args=None, tool_context=None, tool_response=None,
                   **kwargs):
        response = tool_response if tool_response is not None else \
            kwargs.get("response")
        if not isinstance(response, dict):
            return None
        state = getattr(tool_context, "state", None)
        if state is None:
            return None

        # Gate 1: a contract already reached the model this turn. Without this the
        # turn can carry TWO blocks -- the injected one and this one -- giving the
        # model competing depth instructions. The guard was dropped somewhere in
        # the divergence; restored because two contracts is worse than none.
        if state.get("temp:adkl_contract_injected"):
            return None

        resolution = widgets_mod.resolve_contract(state, specs)
        if resolution.contract is None:
            return None   # gate 2: nothing staged, so nothing to shape

        block = widgets_mod.instruction_for(resolution.contract)
        if not block.strip():
            return None

        # Appended to the tool's OWN instruction field, which is what the model
        # reads. A separate key would be ignored -- the model sees the result, not
        # our conventions about it.
        merged = dict(response)
        existing = str(merged.get("agent_instruction") or "").strip()
        merged["agent_instruction"] = (
            f"{existing}\n\n{block.strip()}" if existing else block.strip())

        # Mark the turn so the before-model injector stands down if it runs
        # later, and so a second tool result does not attach another block.
        try:
            state["temp:adkl_contract_injected"] = True
        except Exception:
            pass
        if on_finding:
            on_finding(resolution.contract.value, list(resolution.sources))
        return merged

    return after_tool
