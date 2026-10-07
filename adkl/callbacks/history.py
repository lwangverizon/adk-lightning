"""Request history slicing; pair repair lives in adkl.history."""
from __future__ import annotations



def make_history_slicer(recent_turns: int = 5,
                        preserve_tool_pairs: bool = True):
    """Before-model callback trimming history to the last N user turns.

    Mirrors prod's before-model narrowing. Two properties do the work:

    **Turns, not messages.** Counting messages would cut mid-exchange, leaving the
    model a reply whose question it cannot see. A "turn" starts at a user message,
    so slicing lands on an exchange boundary.

    **Tool pairs stay together, via the turn predicate.** A `function_call`
    separated from its `function_response` makes the request invalid -- ADK
    sends an orphaned result the model never asked for, and the model either
    hallucinates the question or errors. What prevents it is
    `_is_user_turn_start` refusing to treat a content carrying a
    `function_response` as a turn boundary: ADK gives those `role="user"`, so a
    bare role check WOULD cut between a call and its response. Every candidate
    cut is therefore a real user message, and no pair can straddle one.

    `preserve_tool_pairs` is consequently inert -- its walk-back tests exactly
    the predicate the cut index already excludes, so the loop never runs. It is
    kept because the feature registry accepts it and rejects unknown options,
    so removing it would break a host that sets it. Do not "restore" it by
    loosening the predicate; the predicate is the guard.

    Slicing NEVER drops the current user message. A window of 1 keeps this turn
    and nothing else; a window of 0 would mean "answer with no question", so the
    floor is 1 regardless of config.
    """
    keep_turns = max(1, int(recent_turns))

    def before_model(callback_context=None, llm_request=None, **kwargs):
        request = llm_request if llm_request is not None else \
            kwargs.get("request")
        contents = getattr(request, "contents", None)
        if not contents or len(contents) <= 1:
            return None

        # Tool responses use role=user in ADK too. They are not turn boundaries.
        starts = [i for i, c in enumerate(contents)
                  if _is_user_turn_start(c)]
        if len(starts) <= keep_turns:
            return None   # already inside the window

        cut = starts[-keep_turns]

        if preserve_tool_pairs:
            # Walk back while the first kept item is an orphaned tool response.
            # Cutting between a call and its response is the invalid-request case
            # this option exists to prevent.
            while cut > 0 and _is_orphan_response(contents, cut):
                cut -= 1

        dropped = cut
        try:
            request.contents = contents[cut:]
        except Exception:
            return None   # frozen request: leave history alone rather than fail
        _record(callback_context, dropped, len(contents))
        return None

    return before_model


def _is_orphan_response(contents: list, index: int) -> bool:
    """True when contents[index] is a tool RESPONSE whose call is before it."""
    item = contents[index]
    for part in (getattr(item, "parts", None) or []):
        if getattr(part, "function_response", None) is not None:
            return True
    return False


def _is_user_turn_start(content) -> bool:
    if getattr(content, "role", None) != "user":
        return False
    return not any(getattr(part, "function_response", None) is not None
                   for part in (getattr(content, "parts", None) or []))


def _record(callback_context, dropped: int, total: int) -> None:
    """Note the trim in state, so the console can show it happened.

    Silent trimming is indistinguishable from a model that forgot -- and "why did
    it lose context" is a question the trace should answer rather than leave to a
    bisect.
    """
    state = getattr(callback_context, "state", None)
    if state is None or not dropped:
        return
    try:
        state["temp:history_sliced"] = f"dropped {dropped} of {total} messages"
    except Exception:
        pass
