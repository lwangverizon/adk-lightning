"""Public callback factories and explicit shared runtime wiring.

Implementations live in callbacks/ by responsibility. Native skill and staged
widget lifecycle integration remain in agent_runtime.py and skill_runtime.py.
Keep hook ordering visible here: metadata is not executable registration.
"""
from __future__ import annotations

from typing import Any

from adkl import features
from adkl.callbacks.composition import compose_after_tool
from adkl.callbacks.context import make_short_circuit, make_state_injector
from adkl.callbacks.history import make_history_slicer
from adkl.callbacks.presentation import make_contract_channel
from adkl.callbacks.tools import make_identity_gate, make_public_help_marker, make_result_trimmer


__all__ = [
    "build_callbacks", "compose_after_tool", "make_contract_channel",
    "make_history_slicer", "make_identity_gate", "make_public_help_marker",
    "make_result_trimmer", "make_short_circuit", "make_state_injector",
    "IMPLEMENTED", "NOT_IMPLEMENTED",
]

# --------------------------------------------------------------- wiring

# Wiring inventory, independent of declaration metadata. Includes factory-owned
# skills/presentation hooks. Tests compare this with advertised support so adding
# a declaration alone cannot silently claim runtime support.
IMPLEMENTED = {
    "history_slicing", "tool_history_repair", "prompt_state_injection", "short_circuit",
    "tool_result_projection", "tool_result_trimming", "progressive_auth",
    "output_cleanup", "pii_redaction", "staged_widgets",
    "skill_retrieval", "skill_state_injection",
}

# Compatibility inventory; the explanation lives with the declaration.
NOT_IMPLEMENTED = {
    name: spec.support_detail for name, spec in features.REGISTRY.items()
    if spec.support_status == "unsupported"
}


def _tag(fn, *feature_names):
    """Record which feature(s) contributed a hook, for ordering verification.

    build_callbacks wires hooks in a fixed source order chosen to satisfy the
    must_run_after graph in features.REGISTRY -- it does not re-derive that
    order from the graph, and `active` is only membership-tested below. So the
    two can drift silently, and the comments are the only thing holding them
    together. This tag is what lets a test prove they still agree; see
    tests/test_features.py::TestDeclaredOrderMatchesWiring.
    """
    fn.adkl_features = frozenset(feature_names)
    return fn


def build_callbacks(active: list, config: dict,
                    on_output_finding=None, widget_specs=None, root=None) -> tuple[dict, list]:
    """Map an active pipeline to ADK callback kwargs, plus unhonoured features.

    Returns `(kwargs, unhonoured)`. Console observation is development evidence,
    not a durable host analytics pipeline. Retired declarations are rejected by
    feature resolution before construction.
    """
    kwargs: dict[str, Any] = {}
    before_tool, after_tool = [], []
    before_model, after_model = [], []

    if "history_slicing" in active:
        # Registry defaults, never a literal: `or 5` here ate a deliberate
        # `recent_turns: 0` and the console still showed 0. Defaults supply the
        # FALLBACK per option rather than a literal, so a caller that hands us a
        # bare {} gets the same values features.resolve() would have produced.
        #
        # Read through `cfg.get(...)`, NOT `defaults | cfg`. `dict.__or__` copies
        # the right operand at the C level and never calls its `__getitem__` or
        # `get`, so a Mapping SUBCLASS that instruments those two methods sees
        # ZERO reads. Preserve instrumented mapping access so host audits can
        # distinguish a consulted configuration option from a decorative one.
        _hs = features.defaults("history_slicing")
        cfg = config.get("history_slicing") or {}
        before_model.append(_tag(make_history_slicer(
            int(cfg.get("recent_turns", _hs["recent_turns"])),
            bool(cfg.get("preserve_tool_pairs", _hs["preserve_tool_pairs"]))),
            "history_slicing"))

    if "output_cleanup" in active or "pii_redaction" in active:
        from adkl import output_filters
        # ONE callback for both features, not two. They edit the same text, and
        # two independent filters would each read the original and the second
        # would overwrite the first's edit -- so a redaction could be undone by a
        # strip that ran after it.
        cleanup_cfg = config.get("output_cleanup", {})
        pii_cfg = config.get("pii_redaction", {})
        # `kinds` selects patterns; an EMPTY list means "redact nothing". Passing
        # `pii_cfg.get("kinds") or None` collapsed [] to None, and None means ALL
        # patterns downstream -- so a deliberate kill switch redacted everything
        # instead of nothing, the exact inversion of what was written. Per-option
        # registry fallback for the reason given at the history_slicing site, so a
        # bare {} matches features.resolve(); only a declared [] means none, and
        # False still means the feature is not in the pipeline at all.
        _pii = features.defaults("pii_redaction")
        kinds = pii_cfg.get("kinds", _pii["kinds"])
        after_model.append(_tag(output_filters.make_output_filter(
            strip="output_cleanup" in active,
            redact_kinds=kinds if "pii_redaction" in active else False,
            on_finding=(on_output_finding
                        if cleanup_cfg.get("report_removals", True) else None)),
            *(n for n in ("output_cleanup", "pii_redaction") if n in active)))

    if "progressive_auth" in active:
        cfg = config.get("progressive_auth", {})
        after_tool.append(_tag(make_public_help_marker(
            cfg.get("tools", []), cfg.get("state_key", "temp:public_help_attempt")),
            "progressive_auth"))
    if "short_circuit" in active:
        kwargs["before_agent_callback"] = [_tag(make_short_circuit(
            config.get("short_circuit", {}).get("phrases", {})), "short_circuit")]
    if "prompt_state_injection" in active:
        before_model.append(_tag(make_state_injector(
            config.get("prompt_state_injection", {}).get("slots", {}), root,
            lambda names: on_output_finding(["Missing context: " + ", ".join(names)]) if on_output_finding else None),
            "prompt_state_injection"))

    if "tool_result_projection" in active:
        from adkl.projection import make_result_projector
        # Observers run on the raw result, then projection precedes trimming.
        after_tool.append(_tag(make_result_projector(
            config.get("tool_result_projection", {}).get("tools", {}),
            on_output_finding), "tool_result_projection"))

    if "tool_result_trimming" in active:
        # `or 12000` contradicted the declared default of 4000 outright: three
        # copies of this number existed (registry 4000, wiring 12000, factory
        # 12000) and only one of them was the one the console showed.
        # Per-option fallback, read through `cfg.get`, for the reason spelled out
        # at the history_slicing site above: `defaults | cfg` would make every
        # read here invisible to a host that instruments its config mapping.
        _tr = features.defaults("tool_result_trimming")
        cfg = config.get("tool_result_trimming") or {}
        # After observation/projection, before current widget guidance. The
        # composed hook passes each replacement to the next transform.
        after_tool.append(_tag(make_result_trimmer(
            cfg.get("max_chars", _tr["max_chars"]),
            cfg.get("keep", _tr["keep"])), "tool_result_trimming"))

    if "tool_history_repair" in active:
        from adkl.history import make_tool_history_repair
        before_model.append(_tag(make_tool_history_repair(), "tool_history_repair"))

    # Trim the bulk first, then append the current contract so guidance survives.
    if "staged_widgets" in active and widget_specs and config.get("staged_widgets", {}).get("inject_contract", True):
        after_tool.append(_tag(make_contract_channel(widget_specs), "staged_widgets"))

    if before_model:
        kwargs["before_model_callback"] = before_model
    if after_model:
        kwargs["after_model_callback"] = after_model
    if before_tool:
        kwargs["before_tool_callback"] = before_tool
    if after_tool:
        kwargs["after_tool_callback"] = [compose_after_tool(after_tool)]

    unhonoured = [f"{n}: {NOT_IMPLEMENTED[n]}"
                  for n in active if n in NOT_IMPLEMENTED]
    unknown = [f"{n}: no runtime implementation" for n in active
               if n not in IMPLEMENTED and n not in NOT_IMPLEMENTED]
    return kwargs, unhonoured + unknown
