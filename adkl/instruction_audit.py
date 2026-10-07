"""Audit final request layering without rewriting prompts or executing a model.

Hosts annotate exact spans in the final resolved request. Layer/scope/ownership
are explicit host declarations, not inferred from product names or prose. Output
contains bounded identifiers and counts, never prompt excerpts or tool results.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from adkl.lint_prompt import _sentences, check_prompt

LAYERS = frozenset({"system", "skill", "workflow", "reference", "tool", "state", "presentation"})
SCOPES = frozenset({"universal", "procedure", "interface", "data", "presentation"})
PHASES = frozenset({"cold", "followup", "topic_switch", "recovery"})
BOUNDARIES = frozenset({"adk_request", "provider_request"})
MAX_CHARS = 2 * 1024 * 1024


@dataclass(frozen=True)
class InstructionSpan:
    """A source-attributed slice of system text or one tool description.

    Offsets are Python character indices [start, end), not tokens or UTF-8 bytes.
    surface is ``system`` or ``tool:<declared_name>``. layer is the prose owner,
    not the transport role: a loaded skill may legitimately occupy system text.
    owner identifies the skill/workflow for conditional layers; it is not PII.
    """

    surface: str
    start: int
    end: int
    layer: str
    scope: str
    owner: str
    dynamic: bool = False


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Expected bounded non-sensitive identifier")


def _names(values):
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > 1000:
        raise ValueError("Expected bounded owner list")
    for value in values:
        _label(value)
    if len(set(values)) != len(values):
        raise ValueError("Duplicate owner identifier")
    return set(values)


def audit_request(*, system: str, tools: Mapping[str, str],
                  spans: Sequence[InstructionSpan], active_owners: Sequence[str],
                  required_owners: Mapping[str, Sequence[str]],
                  phase: str, boundary: str = "adk_request",
                  max_system_bytes: int | None = None,
                  max_tool_description_bytes: int | None = None) -> dict:
    """Return sanitized structural findings for ONE complete request boundary.

    required_owners maps gated tool names to acceptable active owners (OR). An
    empty map is an explicit host declaration of no gated tools, not something
    inferred from descriptions. Active means loaded/prepared, not shortlisted.
    No semantic consistency, authorization, optimality or model outcome is proven.
    """
    if phase not in PHASES or boundary not in BOUNDARIES:
        raise ValueError("Unknown capture phase or boundary")
    if (not isinstance(system, str) or not system.strip() or not isinstance(tools, Mapping)
            or len(tools) > 1000 or not isinstance(spans, (list, tuple)) or len(spans) > 10000):
        raise ValueError("Invalid request capture")
    surfaces = {"system": system}
    for name, text in tools.items():
        _label(name)
        if not isinstance(text, str):
            raise ValueError("Tool description must be text")
        surfaces["tool:" + name] = text
    if sum(len(text) for text in surfaces.values()) > MAX_CHARS:
        raise ValueError("Request text exceeds audit limit")
    active = _names(active_owners)
    if not isinstance(required_owners, Mapping) or len(required_owners) > 1000:
        raise ValueError("Invalid tool ownership map")
    owners = {}
    for tool, names in required_owners.items():
        _label(tool)
        owners[tool] = _names(names)
        if not owners[tool]:
            raise ValueError("Gated tools require at least one owner")
    for budget in (max_system_bytes, max_tool_description_bytes):
        if budget is not None and (type(budget) is not int or not 1 <= budget <= 10**9):
            raise ValueError("Budgets must be positive byte counts")

    findings = []

    def emit(rule, subject, message, severity="WARN"):
        row = {"rule": rule, "severity": severity, "subject": subject, "message": message}
        if row not in findings:
            findings.append(row)

    grouped = {surface: [] for surface in surfaces}
    for span in spans:
        if (not isinstance(span, InstructionSpan) or span.surface not in surfaces
                or span.layer not in LAYERS or span.scope not in SCOPES
                or type(span.start) is not int or type(span.end) is not int
                or not 0 <= span.start < span.end <= len(surfaces[span.surface])
                or type(span.dynamic) is not bool):
            raise ValueError("Invalid instruction span")
        _label(span.owner)
        grouped[span.surface].append(span)
        if span.scope == "procedure" and span.layer in {"system", "tool"}:
            emit("INSTRUCTION_PROCEDURE_WRONG_LAYER", span.owner,
                 "Declared domain procedure belongs in its activated skill/workflow, not universal policy or a tool interface.")
        if span.layer in {"skill", "workflow", "reference"} and span.owner not in active:
            emit("INSTRUCTION_OWNER_INACTIVE", span.owner,
                 "Conditional instructions are present without their declared owner loaded/prepared in this request.")
        if span.layer == "state" and span.scope != "data":
            emit("INSTRUCTION_STATE_POLICY", span.owner,
                 "State is declared to carry policy. Separate trusted application controls from untrusted session/tool data.")
        if span.surface.startswith("tool:") and span.layer != "tool":
            emit("INSTRUCTION_TOOL_LAYER_MISMATCH", span.owner,
                 "Tool description contains guidance attributed to another instruction layer; review its consumer and intrinsic contract.")

    sentence_owners = {}
    unmapped = 0
    for surface, text in surfaces.items():
        ordered = sorted(grouped[surface], key=lambda span: span.start)
        cursor = 0
        dynamic_seen = False
        for span in ordered:
            if span.start < cursor:
                raise ValueError("Instruction spans must not overlap")
            unmapped += len(text[cursor:span.start].strip())
            cursor = span.end
            if surface == "system":
                if dynamic_seen and not span.dynamic and span.layer != "presentation":
                    emit("INSTRUCTION_STATIC_AFTER_DYNAMIC", span.owner,
                         "Static instruction text follows dynamic context; review final assembly order and measure prefix-cache behavior.")
                dynamic_seen |= span.dynamic
            # Exact repeated sentences only; no semantic contradiction inference.
            if span.scope != "data":
                for sentence in _sentences(text[span.start:span.end]):
                    normalized = sentence.casefold()
                    if len(normalized.split()) >= 12:
                        sentence_owners.setdefault(normalized, []).append((surface, span.layer, span.owner))
        unmapped += len(text[cursor:].strip())
    if unmapped:
        emit("INSTRUCTION_PROVENANCE_INCOMPLETE", "request",
             f"{unmapped} nonempty characters lack layer/source attribution; late injections may be unreviewed.")
    repeated = sum(len(rows) > 1 for rows in sentence_owners.values())
    if repeated:
        emit("INSTRUCTION_TEXT_DUPLICATED", "request",
             f"{repeated} long instruction sentence(s) repeat across attributed blocks. Review single ownership; do not remove safety mechanically.", "INFO")
    for tool, accepted in owners.items():
        if tool in tools and not accepted & active:
            emit("INSTRUCTION_TOOL_OWNER_BYPASS", tool,
                 "A gated tool is exposed without an acceptable instruction owner active. Check cold-start, retained and standalone bindings.")

    # Reuse the deliberately narrow unconditional formatting-conflict check.
    # Do not concatenate separately scoped skills/tools and infer that opposing
    # conditional rules apply together. General semantic conflicts need review.
    universal = "\n\n".join(surfaces[span.surface][span.start:span.end]
        for span in spans if span.layer == "system" and span.scope == "universal")
    for severity, rule, _subject, message in check_prompt(universal, [], None):
        if rule == "PROMPT_SURFACE_CONFLICT":
            emit(rule, "request", message, severity)

    sizes = {surface: len(text.encode("utf-8")) for surface, text in surfaces.items()}
    if max_system_bytes is not None and sizes["system"] > max_system_bytes:
        emit("INSTRUCTION_SYSTEM_BUDGET", "request", "Assembled system bytes exceed the host's explicit budget.")
    if max_tool_description_bytes is not None:
        for name in tools:
            if sizes["tool:" + name] > max_tool_description_bytes:
                emit("INSTRUCTION_TOOL_BUDGET", name, "Tool description bytes exceed the host's explicit budget.")
    return {"version": 1, "evidence": "host_attributed_request", "phase": phase,
            "boundary": boundary, "behavioralEvaluation": "not_run",
            "passed": not any(f["severity"] == "WARN" for f in findings),
            "findings": findings, "metrics": {"systemBytes": sizes["system"],
                "toolDescriptionBytes": sum(v for k, v in sizes.items() if k != "system"),
                "tools": len(tools), "spans": len(spans), "unattributedCharacters": unmapped},
            "limitations": ["Host attribution is not attestation or semantic policy inference.",
                "Counts exclude schemas/history; bytes are not tokens, provider cost or cache savings.",
                "A clean capture does not establish other variants, authorization or model compliance."]}


def inspect_request(request, **kwargs) -> dict:
    """Read a final ADK LlmRequest via public attributes; never modify it.

    Call AFTER all toolset preparation and before-model transformations. A later
    mutation invalidates the capture. This does not install a runtime callback or
    import ADK; unsupported multimodal instructions/declarations fail explicitly.
    Only outgoing config.tools declarations count, not dispatch-only tools_dict.
    """
    config = getattr(request, "config", None)
    system = getattr(config, "system_instruction", None)
    if not isinstance(system, str):
        raise ValueError("Capture requires a fully resolved text system instruction")
    tools = {}
    for group in getattr(config, "tools", None) or []:
        declarations = getattr(group, "function_declarations", None)
        if not declarations:
            raise ValueError("Capture requires explicit function declarations")
        for declaration in declarations:
            name = declaration.name
            if name in tools:
                raise ValueError("Duplicate outgoing tool declaration")
            tools[name] = declaration.description or ""
    return audit_request(system=system, tools=tools, **kwargs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Trusted local synthetic JSON capture, not a customer transcript")
    parser.add_argument("--fail-on", choices=("warning", "info"), default="warning")
    args = parser.parse_args(argv)
    try:
        from adkl.evaluation import _unique_pairs
        if args.capture.stat().st_size > MAX_CHARS * 2:
            raise ValueError("Capture file too large")
        payload = json.loads(args.capture.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        payload["spans"] = [InstructionSpan(**row) for row in payload["spans"]]
        report = audit_request(**payload)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Never echo parser/host content, paths or exception text into CI output.
        print(json.dumps({"passed": False, "error": "Invalid or incomplete instruction capture; inspect locally."}))
        return 2
    print(json.dumps(report, indent=2))
    return int(not report["passed"] or (args.fail_on == "info" and bool(report["findings"])))


if __name__ == "__main__":
    raise SystemExit(main())
