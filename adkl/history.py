"""Request-only tool exchange repair for hosts translating ADK to chat APIs.

No tool is executed and no durable event is edited. Enable only at a completed
before-model boundary, after host history transforms, never on partial streams.
"""
from __future__ import annotations


MISSING_RESULT = (
    "Missing recorded tool result; execution outcome is unknown. Do not "
    "automatically repeat this call. Report the missing result if needed."
)


def repair_tool_history(llm_request) -> dict[str, int]:
    """Pair each model call batch with its contiguous user response batch.

    Explicit IDs (without a thinking suffix) and names must agree. When either
    ID is absent, match by name in order within that batch only. Drop unmatched
    responses, retain calls with an explicit unknown-outcome result, and assign
    unique deterministic wire IDs. Never pair across a human/model turn.
    """
    from google.genai import types

    contents = [c.model_copy(deep=True) for c in (llm_request.contents or [])]
    report = {"assigned_ids": 0, "dropped_responses": 0, "missing_results": 0}

    def normalized(value):
        return (value or "").split("__thought__", 1)[0]

    reserved = {
        normalized(obj.id)
        for c in contents for p in (c.parts or [])
        for obj in (p.function_call, p.function_response)
        if obj is not None and obj.id
    }
    used, serial = set(), 0

    def wire_id(value):
        nonlocal serial
        value = normalized(value)
        if not value or value in used:
            while f"adkl-tool-{serial}" in reserved | used:
                serial += 1
            value = f"adkl-tool-{serial}"
            serial += 1
        used.add(value)
        return value

    out, index = [], 0
    while index < len(contents):
        content = contents[index]
        calls = [p.function_call for p in (content.parts or [])
                 if p.function_call is not None]
        if not calls:
            parts = [p for p in (content.parts or []) if p.function_response is None]
            report["dropped_responses"] += len(content.parts or []) - len(parts)
            if parts or content.parts is None:
                out.append(content.model_copy(update={"parts": parts if content.parts is not None else None}))
            index += 1
            continue
        if content.role != "model" or any(not call.name for call in calls):
            raise ValueError("tool history calls require a model role and tool name")

        # A response before its call or in a later turn is never evidence that
        # this batch completed. Keep non-tool parts from mixed response messages.
        responses, companions = [], []
        end = index + 1
        while end < len(contents):
            following = contents[end]
            parts = following.parts or []
            if (following.role != "user"
                    or any(p.function_call is not None for p in parts)
                    or not any(p.function_response is not None for p in parts)):
                break
            responses.extend(p.function_response for p in parts if p.function_response is not None)
            other = [p for p in parts if p.function_response is None]
            if other:
                companions.append(following.model_copy(update={"parts": other}))
            end += 1
            if other:
                break

        # Reserve explicit matches for the entire batch before an earlier
        # id-less call can consume a later call's explicitly identified result.
        matches = {}
        for position, call in enumerate(calls):
            call_id = normalized(call.id)
            exact = [i for i, response in enumerate(responses)
                     if call_id and response.name == call.name
                     and normalized(response.id) == call_id]
            if exact:
                matches[position] = responses.pop(exact[0])

        paired = []
        for position, call in enumerate(calls):
            call_id = normalized(call.id)
            candidates = [i for i, response in enumerate(responses)
                          if response.name == call.name
                          and (not call_id or not response.id
                               or normalized(response.id) == call_id)]
            response = matches.get(position)
            if response is None and candidates:
                response = responses.pop(candidates[0])
            identifier = wire_id(call.id or (response.id if response else None))
            if call.id != identifier:
                report["assigned_ids"] += 1
            call.id = identifier  # deep request copy, never the session's call
            if response is None:
                report["missing_results"] += 1
                response = types.FunctionResponse(
                    name=call.name, response={"error": MISSING_RESULT})
            response.id = identifier
            paired.append(types.Part(function_response=response))
        report["dropped_responses"] += len(responses)
        # Malformed response parts sharing the model call message are orphans.
        parts = [p for p in (content.parts or []) if p.function_response is None]
        report["dropped_responses"] += len(content.parts or []) - len(parts)
        out.append(content.model_copy(update={"parts": parts}))
        out.append(types.Content(role="user", parts=paired))
        out.extend(companions)
        index = end
    llm_request.contents = out
    return report


def make_tool_history_repair():
    def before_model(callback_context, llm_request):
        report = repair_tool_history(llm_request)
        if callback_context is not None:
            callback_context.state["temp:adkl_tool_history"] = report
        return None
    return before_model
