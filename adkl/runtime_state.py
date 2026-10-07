"""Internal state addresses shared by native lifecycle callbacks.

ADK's temp state belongs to an invocation, not an individual agent. Agent names
are unique within a native tree, so include the owner even for temporary keys.
"""


def agent_key(key, context):
    return f"{key}:{context.agent_name}"


def record_diagnostics(context, value):
    context.state[agent_key("adkl:last_turn", context)] = value
    # Compatibility snapshot for existing single-agent trace readers only.
    # Execution must never read this last-writer-wins alias.
    context.state["adkl:last_turn"] = dict(value)


def begin_invocation(runtime, context):
    """Reset ADKL observations/presentation without touching native activation."""
    context.state[agent_key("temp:adkl_prepared_skills", context)] = []
    context.state[agent_key("temp:adkl_selection", context)] = []
    context.state["adkl:widgets:" + context.agent_name] = {}
    context.state["adkl:request:" + context.agent_name] = None
    specs = getattr(runtime, "_widget_specs", [])
    if getattr(runtime, "_widgets_active", False) and specs:
        from adkl.widgets import clear_turn_flags
        dropped = clear_turn_flags(context.state, specs)
        context.state["temp:adkl_dropped_stages"] = dropped
        context.state[agent_key("temp:adkl_dropped_stages", context)] = dropped
    record_diagnostics(context, {"matches": [], "picked": None,
        "activated": [], "model_calls": 0, "fallback": False, "margin": 0.0,
        "declared_tools": []})
