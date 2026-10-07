"""Tool-result transformations and host-owned identity boundaries."""

import re

from .model import FeatureError, Kind, Spec, Stage


def validate_projection(config):
    from adkl.projection import validate_rules
    try:
        validate_rules(config["tools"])
    except ValueError as exc:
        raise FeatureError(str(exc)) from exc


def validate_public_help(config):
    if not isinstance(config["tools"], list) or any(not isinstance(t, str) or not t for t in config["tools"]):
        raise FeatureError("progressive_auth.tools must be tool names")
    if not isinstance(config["state_key"], str) or not re.fullmatch(r"temp:[A-Za-z_][A-Za-z0-9_]*", config["state_key"]):
        raise FeatureError("progressive_auth.state_key must be a turn-scoped temp: key")


def validate_trimming(config):
    from adkl.tool_results import validate_budget
    try:
        validate_budget(config["max_chars"], config["keep"])
    except ValueError as exc:
        raise FeatureError(f"tool_result_trimming.{exc}") from exc


SPECS = (
    Spec(
        name="tool_result_projection",
        support_status="implemented",
        support_detail=(
            "Projects configured model-facing row fields without dropping rows or "
            "changing source data. Unknown shapes pass through with diagnostics; not "
            "a hard size limit."
        ),
        callback_adapter=True,
        validate=validate_projection,
        kind=Kind.TOOL_CALLBACK,
        summary="Project configured tool-result rows onto selected fields, retaining every row and the envelope.",
        stage=Stage.EXPERIMENTAL,
        default_on=False,
        options={"tools": ("dict", {}, "Tool name -> {path: [keys], fields: [names], max_string_chars: {field: limit}}.")},
    ),
    Spec(
        name="tool_result_trimming",
        support_status="implemented",
        support_detail=(
            "Bounds JSON tool results with an explicitly incomplete serialized "
            "preview; never selects business fields or rows. Character budget "
            "includes envelope overhead, not provider framing or later callbacks."
        ),
        callback_adapter=True,
        validate=validate_trimming,
        kind=Kind.TOOL_CALLBACK,
        summary="Bound oversized JSON results without guessing which business data matters.",
        stage=Stage.EXPERIMENTAL,
        default_on=False,
        writes=frozenset({"tool_result"}),
        # Observe raw results first, then project fields, then trim the payload.
        must_run_after=frozenset({"tool_result_projection"}),
        # Any finite preview can discard important facts. Require an explicit
        # budget; the application owns semantic reduction before this safeguard.
        requires_explicit_config=frozenset({"max_chars"}),
        options={
            "max_chars": ("int", 4000, "Explicit JSON character budget, including "
                          "the incomplete-result envelope. Minimum 200. Not a token "
                          "limit; later callbacks/provider framing add overhead."),
            "keep": ("str", "head", "Which end of the serialized preview to keep: "
                     "head | tail. Does not select business rows or fields."),
        },
    ),
    Spec(
        name="progressive_auth",
        support_status="partial",
        support_detail=(
            "Records public-help attempts only. Does not enforce sign-in ordering or "
            "authorization."
        ),
        callback_adapter=True,
        validate=validate_public_help,
        kind=Kind.TOOL_CALLBACK,
        summary="Record public-help tool attempts for an external gate to consult; "
                "does not enforce authorization or sign-in ordering.",
        stage=Stage.EXPERIMENTAL,
        # Maps to prospect_public_help_marker_after_tool. The marker is what a
        # sign-in render gate consults: without it a CTA can be offered before the
        # agent has attempted to answer publicly, which is the wrong order for an
        # unauthenticated visitor and the reason the gate exists.
        writes=frozenset({"session.state:public_help"}),
        options={
            "tools": ("list", ["search_supports", "search_products", "get_offers",
                               "get_plan_perk_info"],
                      "Tools whose use counts as a public-help attempt."),
            "state_key": ("str", "temp:public_help_attempt",
                          "Where the marker is written for a gate to read."),
        },
    ),
)
