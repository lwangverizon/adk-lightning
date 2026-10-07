#!/usr/bin/env python3
"""Cross-layer conflict rules: prompt vs skill vs tool docstring.

## Why this is the category that matters

A retrieved-skill agent assembles its instructions from three independently
authored layers at runtime -- the system prompt, the matched skill's body, and the
declared tools' docstrings. Nobody reads all three together, and nothing checked
that they agree. Every existing rule looks at one layer, or at two in the single
case of TOOL_USED_NOT_DECLARED.

The failure mode is expensive and silent. The concrete instance from this project:
the system prompt's Simplicity block says **"Answer from sources, not memory"**,
and on a turn with no working tool the model correctly stayed silent -- returning
`finish_reason=STOP` with empty parts, indistinguishable from a broken agent. That
took a bisect against the live model to find. A rule comparing the layers would
have named it in a second.

## Deterministic only, and why

Every rule here has a provable basis and cites BOTH sides of the conflict. No
model is asked whether two paragraphs disagree.

That is a deliberate limit, not a shortcut. An LLM pass would catch subtler prose
contradictions, but its findings would be unreproducible -- and a linter whose
findings cannot be trusted gets ignored wholesale, which costs more than the rules
it would have added. The rules below are narrow and certain; the prose-level
contradictions they miss stay missed, visibly, rather than being covered by
something unreliable.

## What each rule keys on

    WIDGET_PROMISED_NOT_EMITTABLE   catalog `emits_widget` vs skill's claim
    WIDGET_OUTPUT_MISSING           configured display tools vs a skill binding none
    WIDGET_OUTPUT_ANNOTATION_STALE  `text_only` annotation vs the skill's bindings
    TOOL_ORDER_CONTRADICTED         docstring "call X first" vs skill step order
    TOOL_INSTRUCTED_DIFFERENTLY     two skills, same tool, opposite instruction
    PROMPT_PROHIBITION_CONTRADICTED  prompt "NEVER x" vs a skill instructing x
    TOOL_NOT_IN_ANY_SKILL           bound and catalogued, never referenced

The first two are fully checkable today because the catalog carries `emits_widget`
per tool, and records whether that value was declared by a presentation pack or
merely inferred from module source -- neither is guessed from the name.
"""

from __future__ import annotations

import re

# A skill claiming the turn will show something. Deliberately narrow: these are
# phrases that assert UI, not ones that merely mention it.
_WIDGET_CLAIM = re.compile(
    r"\b(render|display|show|present)s?\b[^.\n]{0,40}\b"
    r"(card|carousel|widget|cta|button|tile|chart|form)\b", re.I)

# "call X first", "before calling X", "X must be called before".
_ORDER_FIRST = re.compile(
    r"\b(?:call|invoke)\s+`?(\w+)`?\s+(?:first|before)\b"
    r"|\bbefore\s+(?:calling|invoking)\s+`?(\w+)`?", re.I)

# A numbered or checkbox step naming a tool, so step ORDER can be compared.
_STEP_TOOL = re.compile(
    r"^\s*(?:(\d+)[.)]|-\s*\[[ xX]\])\s*.*?`(\w+)`", re.M)

# Prompt prohibitions worth checking a skill against. Extracted, not hardcoded:
# the pattern finds them, so a new prohibition in the prompt is covered without
# editing this file.
_PROHIBITION = re.compile(
    r"\b(?:NEVER|never|do not|don't|must not)\s+([^.\n]{8,70})", re.I)

_STOP = frozenset("""a an the to for of and or with when if that this it is are be
been on in at by from as into your you their our its not no than then so such use
using used call calls calling""".split())


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower())
            if w not in _STOP}


def check_widget_promises(name: str, body: str, declared: list,
                          emits: dict) -> list:
    """A skill promising UI that none of its declared tools can emit.

    `emits` maps tool name -> True / False / None, from the catalog's
    `emits_widget` (read from module source, so this is evidence rather than a
    name guess). **None means no evidence was recorded, which is not the same as
    evidence of absence** -- see the unknown-emission guard below.

    Scoped to skills that make an explicit claim: a skill merely mentioning a
    "card" is not promising one. And a skill declaring NO tools is skipped
    entirely -- a pure-prose skill describing what the customer will see
    elsewhere is legitimate.
    """
    out = []
    if not declared:
        return out
    claims = _WIDGET_CLAIM.findall(body)
    if not claims:
        return out
    if any(emits.get(t) for t in declared):
        return out
    # Unknown emission is not "does not emit". A native/host-factory binding
    # reports tools as name+description+scope only -- `emits_widget` is populated
    # by the static catalog scan, so on that path EVERY tool reads as False and
    # this rule can never take the early return above. That made it fire on
    # every skill with a widget claim, including ones whose stagers demonstrably
    # emit at the turn boundary. This rule accuses an author of promising
    # unrenderable UI, so it must speak only from recorded evidence.
    if any(emits.get(t) is None for t in declared):
        return out

    phrase = " ".join(claims[0]) if isinstance(claims[0], tuple) else str(claims[0])
    out.append(("WARN", "WIDGET_PROMISED_NOT_EMITTABLE",
                f"instructs the model to {phrase.strip()}, but none of its "
                f"{len(declared)} declared tool(s) emits a widget "
                f"({', '.join(sorted(declared)[:4])}). The reply will describe UI "
                f"the customer never sees"))
    return out


ANNOTATION = "adkl_presentation"
TEXT_ONLY = "text_only"


def check_widget_coverage(name: str, fm: dict, declared: list,
                          emits: dict, configured: dict) -> list:
    """A skill that produces no widget where the project has display tools.

    The inverse of `check_widget_promises`: that rule catches a promise nothing
    can keep, this one catches the absence of a promise where one was probably
    intended. A skill binding no display tool renders nothing and reports
    nothing -- the reply is text, the turn is clean, and the card the author
    assumed the customer would see never existed.

    Scoped by evidence, not by ambition. The two evidence bars are deliberately
    asymmetric -- strict to arm the rule, lenient to clear a skill:

    * `configured` arms it: tools whose emission was *declared*, not guessed --
      a presentation pack or a result binding (`inferred: False`). The looser
      `emits` would not do, because `tool_catalog.widget_emission` searches
      module text and its own docstring calls that an over-report: one domain
      module mentioning `render_ui_widget` anywhere would put a warning on every
      text-only skill in the project, which is the noise this gate exists to
      prevent.
    * `emits` clears it: any recorded or *possible* emission by a declared tool
      is enough. A skill binding an inferred emitter is not accused.
    * Skips a skill whose declared tools carry no recorded emission evidence,
      for the same reason `check_widget_promises` does: a native/host-factory
      inventory reports name+description+scope only, and unknown is not absence.

    Unlike `check_widget_promises`, a skill declaring NO tools is flagged rather
    than skipped. The questions differ. That rule accuses an author of promising
    unrenderable UI, which a prose-only skill never does. This one asks whether
    a text-only turn was *chosen*, and an unannotated prose skill in a
    pack-configured project is exactly the case that cannot answer it.

    `metadata.adkl_presentation: text_only` is that answer. It is a declaration
    of intent, so a stale one -- set on a skill that does bind a display tool --
    is worth reporting rather than silently honouring.
    """
    out = []
    annotated = (fm.get("metadata") or {}).get(ANNOTATION) == TEXT_ONLY
    # The gate is the whole catalog, not this skill: a declared emitter anywhere
    # means display is configured and available for any skill to bind.
    if not any(configured.values()):
        return out
    emitting = sorted(t for t in declared if emits.get(t))
    if emitting:
        if annotated:
            out.append(("INFO", "WIDGET_OUTPUT_ANNOTATION_STALE",
                        f"declares {ANNOTATION}: {TEXT_ONLY} but binds "
                        f"{', '.join(emitting[:3])}, which emits a widget. The "
                        f"annotation no longer describes the skill, so it "
                        f"suppresses nothing and misleads the next reader"))
        return out
    if annotated:
        return out
    if any(emits.get(t) is None for t in declared):
        return out

    available = sorted(t for t, value in configured.items() if value)
    out.append(("WARN", "WIDGET_OUTPUT_MISSING",
                f"declares no widget-emitting tool; runtime output is not verified. "
                f"The project provides {', '.join(available[:4])}. Bind a "
                f"display tool, or record the choice with "
                f"`metadata.{ANNOTATION}: {TEXT_ONLY}`"))
    return out


def check_tool_order(name: str, body: str, declared: list,
                     summaries: dict) -> list:
    """A tool docstring's stated ordering contradicted by the skill's steps.

    Only fires when BOTH sides are explicit: the docstring says a specific tool
    comes first, and the skill's numbered steps place it later. Anything vaguer is
    left alone -- a rule that guessed at intent here would produce noise on every
    skill with a workflow.
    """
    out = []
    steps = _STEP_TOOL.findall(body)
    if len(steps) < 2:
        return out
    # tool -> earliest step index it appears in
    position = {}
    for idx, (_num, tool) in enumerate(steps):
        position.setdefault(tool, idx)

    for tool in declared:
        doc = summaries.get(tool) or ""
        for m in _ORDER_FIRST.finditer(doc):
            required = m.group(1) or m.group(2)
            if not required or required not in position or tool not in position:
                continue
            if position[required] > position[tool]:
                out.append(("WARN", "TOOL_ORDER_CONTRADICTED",
                            f"`{tool}`'s docstring says to call `{required}` first, "
                            f"but this skill's steps call `{tool}` at step "
                            f"{position[tool] + 1} and `{required}` at step "
                            f"{position[required] + 1}"))
    return out


def check_prompt_prohibitions(name: str, body: str, prompt_text: str) -> list:
    """A skill instructing what the system prompt forbids.

    Extracts prohibitions from the prompt rather than hardcoding them, so a new
    "NEVER x" line is covered without editing this file.

    Require the prohibited action phrase in order, ignoring only articles and
    Markdown emphasis. Bag-of-words overlap confused "end the turn" plus an
    unrelated empty field with "end a turn empty". Negations inside an action
    ("tell a customer it does not exist") must retain their meaning. This narrow
    heuristic intentionally does not detect paraphrased contradictions.
    """
    out = []
    if not prompt_text:
        return out

    def normalized(text):
        return " ".join(word for word in re.findall(r"[\w'-]+", text.lower())
                        if word not in {"a", "an", "the"})

    # Blank lines, list items and headings are boundaries, not a single sentence.
    # Ordinary hard-wrapped prose remains together.
    sentences = re.split(r"(?<=[.!?;])\s+|\n\s*\n|\n(?=\s*(?:[-*] |\d+[.)] |#))", body)
    for m in _PROHIBITION.finditer(prompt_text):
        banned = m.group(1).strip()
        bw = _words(banned)
        if len(bw) < 3:
            continue
        action = normalized(banned)
        for s in sentences:
            plain = normalized(re.sub(r"^\s*(?:[-*] |\d+[.)] )", "", s))
            match = re.search(rf"(?<!\w){re.escape(action)}(?!\w)", plain)
            if not match:
                continue
            # Test polarity before THIS occurrence, not later in the sentence.
            # "Call X, never Y" still instructs X. Quoted/descriptive mentions
            # without a directive prefix are not proof of an instruction.
            lead = plain[:match.start()]
            restating = re.search(r"\b(never|do not|don't|must not|avoid)\b",
                                  lead, re.I)
            instructing = re.fullmatch(
                r"\s*(?:\d+\s+)?(?:(?:please|always|then|you must|you should)\s+)?",
                lead,
            ) or re.search(r"\b(?:then|must|should)\s+$", lead)
            if instructing and not restating:
                out.append(("WARN", "PROMPT_PROHIBITION_CONTRADICTED",
                            f'system prompt says "{banned[:60]}" but this skill '
                            f'says "{s.strip()[:70]}"'))
                break
    return out


def check_cross_skill(skills: list, summaries: dict) -> list:
    """Two skills instructing the same tool in opposite directions.

    `skills` is [(name, body, declared)]. Returns [(sev, rule, skill, msg)].

    Fires only on an explicit polarity disagreement: one skill says to call a
    tool, another says not to, and both name it. Same-tool-different-arguments is
    normal and not reported -- that is what parameters are for.
    """
    out = []
    positive: dict = {}
    negative: dict = {}
    for name, body, declared in skills:
        for tool in declared:
            for m in re.finditer(rf"([^.\n]{{0,60}})`{re.escape(tool)}`", body):
                lead = m.group(1)
                if re.search(r"\b(never|do not|don't|must not|avoid|without)\b",
                             lead, re.I):
                    negative.setdefault(tool, []).append((name, lead.strip()))
                elif re.search(r"\b(call|invoke|use)\b", lead, re.I):
                    positive.setdefault(tool, []).append((name, lead.strip()))

    for tool in sorted(set(positive) & set(negative)):
        pos = [n for n, _ in positive[tool]]
        neg = [(n, q) for n, q in negative[tool]]
        # Same skill on both sides is normal: "call X, but never with Y".
        cross = [(p, n, q) for p in pos for n, q in neg if p != n]
        if not cross:
            continue
        p, n, quote = cross[0]
        out.append(("INFO", "TOOL_INSTRUCTED_DIFFERENTLY", p,
                    f"instructs calling `{tool}` while `{n}` says not to "
                    f'("{quote[:50]}"). Review workflow and argument conditions; '
                    f"different scopes can be intentional. Coactivation and a "
                    f"runtime conflict are not verified"))
    return out


def check_unused_tools(catalog_tools: list, skills: list) -> list:
    """Catalogued and bindable, but no skill references it.

    Not a defect on its own -- an agent may carry a tool for a flow the skills do
    not spell out. But an unreferenced tool is dead weight the model still pays
    for whenever something binds it, and it is usually a rename that only got
    half-applied.
    """
    referenced = set()
    for _name, body, declared in skills:
        referenced.update(declared)
        referenced.update(re.findall(r"`(\w+)`", body))

    out = []
    for t in catalog_tools:
        name = t.get("name")
        if name and name not in referenced:
            out.append(("INFO", "TOOL_NOT_IN_ANY_SKILL", name,
                        "catalogued and bindable but referenced by no skill in "
                        "this agent. The host may bind or call it directly; "
                        "check registration and callers before removing it"))
    return out


def check_prompt_tool_scope(prompt_text: str, skills: list, catalog: dict,
                            core_tools=()) -> list:
    """Flag prompt references whose tool availability depends on skill loading.

    This is an advisory scope review, not proof that a conditional prompt is
    wrong. Universal requirements need core tools; conditional ones need loading.
    """
    global_tools = set(core_tools) | {t["name"] for t in catalog.get("tools", [])
                                     if t.get("provider") == "adkl/presentation"}
    mentioned = set(re.findall(r"`([a-z][a-z0-9_]+)(?:`|\()", prompt_text))
    known = {t["name"] for t in catalog.get("tools", [])}
    out = []
    for tool in sorted((mentioned & known) - global_tools):
        missing = [name for name, _body, declared in skills if tool not in declared]
        if missing:
            out.append(("INFO", "PROMPT_TOOL_SKILL_SCOPED", missing[0],
                f"System prompt references `{tool}`, but {len(missing)} skill(s) do not declare it. "
                "Require loading a suitable skill first, or declare tools.core if this is a universal policy."))
    return out
