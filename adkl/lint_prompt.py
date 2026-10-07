"""Conservative prompt ownership checks over supplied declarations, not runtime.

No product-name heuristics: domain policy cannot safely be classified from prose.
Findings are advisory and never justify removing authorization or safety policy.
"""
from __future__ import annotations

from collections import Counter
import re


def _sentences(text: str) -> list[str]:
    # Ignore examples in fenced blocks and separate XML policy wrappers.
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"</?[A-Za-z_][^>]*>", "\n", text)
    return [re.sub(r"\s+", " ", part.replace('**', '')).strip(" -*\t") for part in
            re.split(r"(?<=[.!?;])\s+|\n\s*\n|\n(?=\s*[-*] )", text)
            if part.strip()]


def check_prompt(prompt: str, skills: list, catalog: dict | None,
                 core_tools=()) -> list:
    """Return (severity, rule, subject, message) with no full prompt excerpts.

    A missing catalog means unknown tool declarations: do not infer absence.
    Even a supplied catalog cannot establish state-dependent runtime availability.
    Skill loaders may be implicit in a skill-enabled agent and are exempt there.
    """
    sentences = _sentences(prompt)
    normalized = [s.casefold() for s in sentences]
    counts = Counter(normalized)
    out = []
    repeated = sum(1 for s, n in counts.items() if n > 1 and len(s.split()) >= 12)
    if repeated:
        out.append(("INFO", "PROMPT_POLICY_REPEATED", "",
                    f"{repeated} long sentence(s) repeat in the supplied prompt. "
                    "Review single ownership; this does not establish semantic "
                    "redundancy or measured token savings."))
    prompt_rules = {s for s in normalized if len(s.split()) >= 12}
    for name, body, _declared in skills:
        overlap = prompt_rules & {s.casefold() for s in _sentences(body)}
        if overlap:
            out.append(("INFO", "PROMPT_SKILL_POLICY_DUPLICATED", name,
                        f"{len(overlap)} long sentence(s) occur in both the prompt "
                        "and this skill. Review policy ownership; keep universal "
                        "privacy and safety rules independent of skill selection."))

    # Narrow literal conflict, not word overlap. Ignore conditional/quoted prose.
    for feature in ("markdown", "tables", "bullet points"):
        suffix = r"(?: formatting)?(?: only)?(?:[.!]|\s*[—:(].*)?"
        positive = re.compile(rf"(?:always )?use {feature}{suffix}", re.I)
        negative = re.compile(rf"(?:do not|never|don't) use {feature}{suffix}", re.I)
        if any(positive.fullmatch(s) for s in sentences) and any(
                negative.fullmatch(s) for s in sentences):
            out.append(("WARN", "PROMPT_SURFACE_CONFLICT", "",
                        f"Supplied prompt contains both 'use {feature}' and "
                        f"'do not/never use {feature}' directives. Compose "
                        "surface-specific policy instead of appending overrides."))

    if catalog is not None:
        known = {t['name'] for t in catalog.get('tools', [])} | set(core_tools)
        if skills:
            known |= {'load_skill', 'load_skill_resource'}
        if not skills and 'load_skill' not in known:
            delegated = set()
            for sentence in sentences:
                match = re.match(
                    r"^the `?([a-z][a-z0-9_-]*-+[a-z0-9_-]+)`? skill "
                    r"(?:carries|contains|provides)\b", sentence, re.I)
                if match:
                    delegated.add(match.group(1))
            for skill in sorted(delegated):
                out.append(("WARN", "PROMPT_SKILL_LOADER_NOT_IN_INVENTORY", "",
                            f"Prompt delegates guidance to `{skill}`, but the "
                            "supplied inventory has neither skills nor load_skill. "
                            "Check the consumer's capabilities or compose the "
                            "required policy directly for that consumer."))
        requested = set()
        for sentence in sentences:
            # Only imperative, explicit tool-shaped names; negations, mentions,
            # conditionals and quoted examples are not executable requirements.
            match = re.match(
                r"^(?:Always |You must )?(?:call|invoke|use) "
                r"`([a-z][a-z0-9]*_[a-z0-9_]+)`(?:\s|[.(]|$)", sentence, re.I)
            if match:
                requested.add(match.group(1))
        for tool in sorted(requested - known):
            out.append(("WARN", "PROMPT_TOOL_NOT_IN_INVENTORY", "",
                        f"Prompt instructs `{tool}`, absent from the supplied "
                        "tool inventory. Check the consumer's actual bindings "
                        "and implicit/dynamic tools; declarations alone do not "
                        "prove runtime unavailability. Do not add global tools "
                        "just to silence this finding."))
    return out
