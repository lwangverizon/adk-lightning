#!/usr/bin/env python3
"""Cost and verbosity rules: what a skill charges on every turn it wins.

## Why these exist

The catalog has computed `estimated_input_tokens_per_turn` per tool since the
beginning, and the console shows it as a badge. Nothing ever *linted* it. So the
data to answer "is this skill too expensive?" was sitting there unused while the
question went unasked -- a 3,000-token skill body and a 5,400-token toolset both
looked fine because nothing was watching.

Context cost is not just a style preference. Loaded instructions and exposed tool
schemas can recur across model calls, but activation, request assembly, history
and provider caching determine actual token usage and billing. These checks use
source estimates, not observations of the final request or a measured bill.

## Thresholds come from the corpus, not from taste

Measured across the 24 real prospect skills:

    body tokens     median   545     mean   800     max  3,094
    tool tokens     median 1,900                    max  5,405
    combined                                        max  7,371

    tool declarations   median 506 tokens, 45 of 77 tools over 400

So the thresholds below sit at roughly 2-3x the median rather than at a round
number: flagging the median would flag everything and teach a reader to ignore the
category. The point is to name the genuine outliers -- `general-support` at 3,094
body tokens, `prospect-plan-and-perk-exploration` carrying 26KB of reference docs
against a 6KB skill.

## What is NOT flagged

A high absolute cost that is justified. `prospect-offer-exploration` is expensive
because offers are genuinely complicated, and a rule that told an author to cut it
would be wrong. Every finding here is INFO or WARN, never ERROR: cost is a
tradeoff to see, not a defect to block a save on.
"""

from __future__ import annotations

# Roughly 4 chars per token, the usual heuristic. Stated once so the estimate is
# consistent with the catalog's own arithmetic rather than drifting from it.
CHARS_PER_TOKEN = 4

# ~2x the observed median body (545). Below this a skill is unremarkable.
BODY_TOKENS_WARN = 1200
# ~3x. `general-support` (3,094) and `prospect-travel-support` (2,970) land here.
BODY_TOKENS_HIGH = 2400

# Combined body + bound tools, charged per model call. The corpus max is 7,371.
TURN_TOKENS_WARN = 4000
TURN_TOKENS_HIGH = 6500

# A single tool declaration. Corpus median is 506; 45 of 77 exceed 400, so the
# threshold has to be well above that to mean anything.
TOOL_TOKENS_WARN = 1500

# A zero-parameter tool needs no schema, so its cost is pure docstring. This is
# the clearest disproportion signal in the corpus.
NO_PARAM_TOOL_TOKENS = 300

# Reference docs are loaded on demand rather than per turn, so the concern is
# different: a skill whose references dwarf it has put its content in the wrong
# place, where retrieval cannot see it.
REFS_RATIO_WARN = 3.0
REFS_BYTES_FLOOR = 8000


def _tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


def check_skill_cost(name: str, body: str, declared: list, tool_cost: dict,
                     ref_bytes: int = 0) -> list:
    """Cost findings for one skill. Returns [(sev, rule, message)].

    Missing tool costs contribute zero to a LOWER BOUND, not to a complete
    runtime estimate. Unknown costs are named when a turn-cost finding fires.
    """
    out: list[tuple[str, str, str]] = []
    body_tok = _tokens(body)
    tools_tok = sum(tool_cost.get(t, 0) for t in declared)
    turn_tok = body_tok + tools_tok
    unknown = sum(t not in tool_cost for t in declared)
    qualification = (f"; lower bound: cost unknown for {unknown} tools"
                     if unknown else "; static estimate, not measured usage or billing")

    if body_tok >= BODY_TOKENS_HIGH:
        out.append(("WARN", "SKILL_BODY_LARGE",
                    f"~{body_tok} tokens of instruction, {body_tok / 545:.1f}x the "
                    f"~545-token reference median. Static body estimate; "
                    f"review optional explanation separately from required workflow "
                    f"state, consent and recovery before reducing it"))
    elif body_tok >= BODY_TOKENS_WARN:
        out.append(("INFO", "SKILL_BODY_LARGE",
                    f"~{body_tok} tokens of instruction, above the ~545 median"))

    if turn_tok >= TURN_TOKENS_HIGH:
        out.append(("WARN", "TURN_COST_HIGH",
                    f"~{turn_tok} tokens per model call ({body_tok} instruction + "
                    f"{tools_tok} known tool tokens across {len(declared)} tools)"
                    + qualification))
    elif turn_tok >= TURN_TOKENS_WARN:
        out.append(("INFO", "TURN_COST_HIGH",
                    f"~{turn_tok} tokens per model call "
                    f"({body_tok} instruction + {tools_tok} known tool tokens)"
                    + qualification))

    if ref_bytes and body:
        ratio = ref_bytes / len(body)
        if ratio >= REFS_RATIO_WARN and ref_bytes >= REFS_BYTES_FLOOR:
            out.append(("INFO", "REFERENCES_DOMINATE",
                        f"{ref_bytes // 1000}KB of references against a "
                        f"{len(body) // 1000}KB skill ({ratio:.1f}x). Fine if that "
                        f"detail is genuinely on-demand. Keep routing cues in the "
                        f"description and required execution/recovery instructions "
                        f"available when the skill runs"))
    return out


def check_tool_cost(tools: list) -> list:
    """Cost findings for the tool catalog. Returns [(sev, rule, tool, message)].

    Catalog-wide rather than per-skill, because a bloated docstring is charged to
    every skill that declares the tool -- fixing it once helps all of them, and
    reporting it per skill would say the same thing five times.
    """
    out: list[tuple[str, str, str, str]] = []
    for t in tools:
        name = t.get("name") or "?"
        tok = t.get("estimated_input_tokens_per_turn") or 0
        params = t.get("params") or []

        if tok >= TOOL_TOKENS_WARN:
            out.append(("INFO", "TOOL_DECLARATION_LARGE", name,
                        f"~{tok} tokens of declaration, charged to every skill that "
                        f"declares it (corpus median is ~506)"))
        elif not params and tok >= NO_PARAM_TOOL_TOKENS:
            # The clean disproportion: no parameters means no schema, so the cost
            # is entirely docstring.
            out.append(("INFO", "TOOL_DOCSTRING_HEAVY", name,
                        f"~{tok} tokens with no parameters, so the cost is all "
                        f"docstring. The model needs enough to call it correctly; "
                        f"the rest belongs in a code comment"))

        undocumented = (t.get("quality") or {}).get("undocumented_params") or []
        if undocumented:
            # The inverse failure, and worth pairing with the above: paying for a
            # declaration that does not tell the model what an argument means.
            out.append(("INFO", "TOOL_PARAMS_UNDOCUMENTED", name,
                        f"{len(undocumented)} parameter(s) with no description "
                        f"({', '.join(undocumented[:3])}) -- the model sees the name "
                        f"and type only"))
    return out
