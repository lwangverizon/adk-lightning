"""Conversation history, prompt context and exact replies."""

import pathlib
import re

from .model import FeatureError, Kind, Spec, Stage


def validate_prompt_state(config):
    slots = config["slots"]
    if not isinstance(slots, dict):
        raise FeatureError("prompt_state_injection.slots must be a mapping")
    for name, source in slots.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise FeatureError("Context names must be identifiers")
        if not isinstance(source, dict) or len(source) != 1 or not set(source) <= {"from_file", "from_state"}:
            raise FeatureError("Context sources require exactly one from_file or from_state")
        value = next(iter(source.values()))
        if not isinstance(value, str) or not value:
            raise FeatureError("Context sources must be non-empty strings")
        if "from_file" in source and (pathlib.Path(value).is_absolute() or ".." in pathlib.Path(value).parts):
            raise FeatureError("Context files must stay inside the project")


def validate_short_circuit(config):
    phrases = config["phrases"]
    if not isinstance(phrases, dict):
        raise FeatureError("short_circuit.phrases must be a mapping")
    seen = set()
    for phrase, entry in phrases.items():
        if not isinstance(phrase, str) or not phrase.strip() or phrase.strip().lower() in seen:
            raise FeatureError("short_circuit phrases must be non-empty and distinct ignoring case")
        seen.add(phrase.strip().lower())
        if not isinstance(entry, dict) or set(entry) - {"reply", "widget_type", "payload"}:
            raise FeatureError("short_circuit entries accept reply, widget_type and payload")
        if not isinstance(entry.get("reply"), str) or not entry["reply"].strip():
            raise FeatureError("short_circuit entries require a non-empty reply")
        if "widget_type" in entry and (not isinstance(entry["widget_type"], str) or not entry["widget_type"]):
            raise FeatureError("short_circuit.widget_type must be a non-empty string")
        if "payload" in entry and (not isinstance(entry["payload"], dict) or not entry.get("widget_type")):
            raise FeatureError("short_circuit.payload requires a mapping and widget_type")


def validate_history_slicing(config):
    # 0 used to be swallowed twice over: the wiring read
    # `int(cfg.get("recent_turns") or 5)`, so a deliberate 0 silently became 5,
    # and even reaching the callback it clamps to `max(1, recent_turns)`. There is
    # no value of 0 that does what a reader would expect, so make it an error and
    # name the actual way to turn slicing off.
    if config["recent_turns"] < 1:
        raise FeatureError(
            "history_slicing.recent_turns must be at least 1. To keep the whole "
            "conversation, drop history_slicing from features.pipeline rather "
            "than configuring 0.")


SPECS = (
    Spec(
        name="history_slicing",
        support_status="implemented",
        support_detail=(
            "Preview retains a bounded number of actual user turns before model "
            "calls."
        ),
        callback_adapter=True,
        validate=validate_history_slicing,
        kind=Kind.BEFORE_MODEL,
        summary="Trim the conversation to the last N turns before each model call.",
        stage=Stage.STABLE,
        default_on=False,
        writes=frozenset({"llm_request.contents"}),
        options={
            "recent_turns": ("int", 5, "User turns to keep."),
            "preserve_tool_pairs": ("bool", True,
                                    "Deprecated and inert. Pairs are kept whole by the "
                                    "turn-start predicate, which never treats a "
                                    "function_response content as a boundary; "
                                    "this walk-back tests a condition that "
                                    "index already excludes. Retained for "
                                    "config compatibility."),
        },
    ),
    Spec(
        name="tool_history_repair",
        support_status="implemented",
        support_detail=(
            "Opt-in request-only tool pairing, unique wire IDs and explicit "
            "unknown-outcome results. Does not retry tools or edit stored events; "
            "requires a completed before-model request."
        ),
        callback_adapter=True,
        kind=Kind.BEFORE_MODEL,
        summary="Repair completed tool exchanges in the outgoing request without editing session events.",
        stage=Stage.EXPERIMENTAL,
        default_on=False,
        must_run_after=frozenset({"history_slicing"}),
        options={},
    ),
    Spec(
        name="prompt_state_injection",
        support_status="partial",
        support_detail=(
            "Appends named context from state or project-contained files; does not "
            "replace prompt placeholders."
        ),
        callback_adapter=True,
        validate=validate_prompt_state,
        kind=Kind.BEFORE_MODEL,
        summary="Append named context from session state or a project file, so "
                "an answerable question costs no tool call.",
        stage=Stage.EXPERIMENTAL,
        # Maps to inject_travelpass_coverage_before_load_skill and the page/filter
        # context arm of before_prospect_model_callback. Generalised: prod has one
        # callback per injected thing, which is why there are several.
        reads=frozenset({"session.state"}),
        writes=frozenset({"llm_request.instructions"}),
        options={
            "slots": ("dict", {},
                      "Context name -> {from_state: key} or {from_file: path}. "
                      "Missing sources are reported; existing prompt placeholders are not replaced."),
        },
    ),
    Spec(
        name="short_circuit",
        support_status="implemented",
        support_detail=(
            "Exact configured phrases return native ADK content and optional widgets "
            "before the model."
        ),
        validate=validate_short_circuit,
        kind=Kind.AGENT_CALLBACK,
        summary="Answer an exact phrase deterministically, skipping the model.",
        stage=Stage.EXPERIMENTAL,
        # Exact-match phrases can bypass the model and render a widget. These
        # phrases never reach a skill, so keep the mapping in configuration.
        may_short_circuit=True,
        reads=frozenset({"user_content"}),
        # Genuinely shares the widget channel with staged_widgets, but writes its own
        # state keys. Declared after it: a short-circuit ENDS the turn, so anything
        # the emit loop staged would never render -- running last is the only order
        # that cannot lose a widget.
        writes=frozenset({"event.actions.render_ui_widgets",
                          "session.state:short_circuit"}),
        must_run_after=frozenset({"staged_widgets"}),
        options={
            "phrases": ("dict", {},
                        "Exact lowercase phrase -> {reply, widget_type, payload}. "
                        "Matched on the whole message: a substring match would "
                        "hijack a longer question that merely contains the phrase."),
        },
    ),
)
