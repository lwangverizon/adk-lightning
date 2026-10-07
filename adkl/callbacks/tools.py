"""Result trimming, public-help observation and an unwired identity helper."""
from __future__ import annotations

from adkl.tool_results import bounded_result, validate_budget


def make_identity_gate(arg_names: list, auth_state_key: str = "user",
                       refusal: str = ""):
    """Before-tool callback refusing model-supplied identifiers when unauthed.

    Mirrors `prospect_billing_tool_identity_gate`. Two properties are worth
    stating because both are easy to get subtly wrong:

    **Armed until identity is verified.** A session value is not evidence of
    authentication: callers must provide a mapping with ``verified: true`` from
    a trusted authorization boundary. This helper is intentionally not wired by
    ``build_callbacks`` until the runtime owns that boundary.

    **Truthy check, not presence.** A tool declaring `mtn: str = ""` passes an
    empty string on every call; refusing on presence would refuse every call to
    that tool. Only a non-empty value is a fabricated identifier.

    Returning a dict short-circuits the tool and hands the model the refusal,
    which is the point: the model should see that it was refused and why, so it
    can ask the customer to sign in rather than retrying.
    """
    wanted = {str(a) for a in (arg_names or [])}
    message = refusal or (
        "Refused: this tool was called with a customer identifier that the "
        "session has not authenticated. Ask the customer to sign in instead of "
        "supplying an account number, phone number or customer id yourself.")

    def gate(tool=None, args=None, tool_context=None, **kwargs):
        # ADK passes these by keyword and the exact names have varied across
        # versions, so accept **kwargs and read defensively rather than pinning a
        # signature that a minor upgrade would break.
        args = args or kwargs.get("tool_args") or {}
        ctx = tool_context or kwargs.get("context")
        state = getattr(ctx, "state", None)
        offending = sorted(k for k in wanted if args.get(k))
        if not offending:
            return None
        if state is None:
            return {"status": "refused", "refused_args": offending,
                    "agent_instruction": message}
        identity = state.get(auth_state_key)
        if isinstance(identity, dict) and identity.get("verified") is True:
            return None
        return {"status": "refused", "refused_args": offending,
                "agent_instruction": message}

    return gate


def make_result_trimmer(max_chars: int = 4000, keep: str = "head"):
    """Generic JSON size guard; application transforms run before this hook.

    Oversized results become explicitly incomplete envelopes, never a guessed
    subset of business rows/fields. See adkl.tool_results for the budget contract.
    """
    validate_budget(max_chars, keep)

    def trim(tool=None, args=None, tool_context=None, tool_response=None, **kwargs):
        response = tool_response if tool_response is not None else kwargs.get("response")
        result = bounded_result(response, max_chars=max_chars, keep=keep)
        return None if result is response else result

    return trim


def make_public_help_marker(tools: list, state_key: str):
    """After-tool callback recording that public help was attempted.

    Mirrors `prospect_public_help_marker_after_tool`. The marker is what a sign-in
    gate reads: without it a CTA can be offered before the agent has tried to
    answer publicly, which is the wrong order for an unauthenticated visitor.

    The outcome is classified from the response SHAPE, deterministically, and
    `empty` and `error` still count as attempts -- the contract is "try public help
    first", not "succeed at it". A tool that legitimately found nothing has still
    earned the sign-in prompt.

    Returns None always: this observes, and returning a value would replace the
    tool result and break the after-tool chain for everything registered later.
    """
    watched = {str(t) for t in (tools or [])}

    def after_tool(tool=None, args=None, tool_context=None, tool_response=None,
                   **kwargs):
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None) \
            or kwargs.get("tool_name")
        if name not in watched:
            return None
        state = getattr(tool_context, "state", None)
        if state is None:
            return None
        response = tool_response if tool_response is not None else \
            kwargs.get("response")
        state[state_key] = {"source": name,
                            "outcome": _classify_help(response)}
        return None

    return after_tool


def _classify_help(response) -> str:
    """usable / empty / error, from the response shape only."""
    if not isinstance(response, dict):
        return "usable" if response else "empty"
    status = str(response.get("status") or "").lower()
    if status in ("error", "refused", "wrong_tool") or response.get("error"):
        return "error"
    for key in ("results", "items", "content", "destinations", "offers"):
        value = response.get(key)
        if value:
            return "usable"
        if key in response:
            return "empty"
    return "usable" if len(response) > 1 else "empty"
