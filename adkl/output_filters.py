#!/usr/bin/env python3
"""After-model filters: what reaches the customer.

## Why these are a separate module

`runtime.py` owns wiring; this owns the text rules. They are separated because the
rules are the part worth reading on their own -- each one exists because a specific
kind of garbage reached a reply -- while the wiring is mechanical.

## The streaming constraint that shapes every filter here

ADK yields one `LlmResponse` per delta with `partial=True`, then a single
aggregated response with `partial=False`. On a partial, the chunk's own
leading and trailing whitespace **is the join** between it and its neighbours, so
trimming a partial concatenates words: "the plan" becomes "theplan". Every filter
below therefore runs only on the aggregated response.

A filter that looks correct on a complete reply can still corrupt streamed
output; partial-delta preservation therefore needs its own regression coverage.

## Why redaction is here and not in the logger

Redaction at the **log handler** protects logs, not replies: a model that echoes an account
number back to the customer has already leaked it, and a clean log makes the leak
harder to notice rather than less likely. This module filters what the customer
sees; a log filter would be a separate, additive thing.
"""

from __future__ import annotations

import re
from typing import Any

# ------------------------------------------------------------ structured leaks

# A fenced code block, and the bare-JSON case a model produces when it decides to
# "show its work". Both reach the customer as gibberish.
_FENCE = re.compile(r"```[a-zA-Z]*\s*\n?(.*?)```", re.S)
_JSON_OBJECT = re.compile(r"^\s*\{[\s\S]{20,}\}\s*$")
# Tool-ish XML the model sometimes emits verbatim, e.g. a half-copied
# <function_call> or an <accordion> wrapper from a widget payload.
_XML_TAG = re.compile(r"</?(?:function_call|tool_code|accordion|thinking)[^>]*>",
                      re.I)
# A thinking marker that escaped the planner.
_THOUGHT = re.compile(r"^\s*(?:Thought|Reasoning|Plan)\s*:\s*.*$", re.M)


def strip_structured(text: str) -> tuple[str, list]:
    """Remove structured-data leaks. Returns (cleaned, [what was removed]).

    The removals are REPORTED rather than silent. A reply that quietly loses a
    paragraph is indistinguishable from a model that wrote less, and the console's
    whole job is making the difference visible -- if the model is leaking JSON,
    that is a prompt problem the author needs to see, not something to tidy away.
    """
    removed: list[str] = []
    out = text

    if _FENCE.search(out):
        out = _FENCE.sub("", out)
        removed.append("fenced code block")
    if _XML_TAG.search(out):
        out = _XML_TAG.sub("", out)
        removed.append("tool/XML tag")
    if _THOUGHT.search(out):
        out = _THOUGHT.sub("", out)
        removed.append("thinking marker")
    # Whole-reply JSON only: a JSON fragment inside prose is usually a legitimate
    # example, and cutting it would damage a correct answer.
    if _JSON_OBJECT.match(out):
        removed.append("reply was a bare JSON object")
        out = ""

    # Collapse the blank runs the removals leave behind, but never touch leading
    # or trailing whitespace -- see the module docstring on streaming.
    if removed:
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out, removed


# ------------------------------------------------------------------ redaction

# Ordered: the specific patterns first, so a 10-digit run inside a longer
# account number is not partially masked.
#
# Every pattern is anchored on a word boundary and a length, because the failure
# direction matters: masking a price ("$1,199.99") or a plan size ("5G") would
# corrupt a correct answer, which is worse than missing one identifier in a dev
# console. Production redacts at the log handler where over-masking is cheap; here
# it is not.
_REDACTIONS: tuple[tuple[str, Any, str], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    ("account", re.compile(r"\b\d{9,15}\b"), "[account]"),
    ("phone", re.compile(r"\b(?:\d{3}[-.\s]\d{3}[-.\s]\d{4})\b"), "[phone]"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "[email]"),
)

# Never redacted. Correlation and session identifiers are infrastructure, not
# customer data, and masking them makes a reply impossible to tie back to a trace
# Keep currency amounts readable while applying the configured PII rules.
_EXEMPT = re.compile(r"\b(?:POE-[MDB]-\S+|req[-_]\w+|session[-_]\w+)\b", re.I)


def redact(text: str, kinds: list | None = None) -> tuple[str, list]:
    """Mask customer identifiers. Returns (redacted, [kinds found]).

    `kinds` selects which patterns run; None means all of them, and an EMPTY list
    means none -- distinguishing "unspecified" from "explicitly nothing", because
    `if kinds` conflated them and made an empty selection run every pattern.
    Exempt tokens are stashed before matching and restored after, so a session id
    containing digits survives intact.
    """
    wanted = ({k for k, _, _ in _REDACTIONS} if kinds is None else set(kinds))
    found: list[str] = []

    # Stash exempt tokens behind placeholders no pattern can match.
    stash: dict = {}

    def _hide(m):
        key = f"\x00{len(stash)}\x00"
        stash[key] = m.group(0)
        return key

    out = _EXEMPT.sub(_hide, text)

    for name, pattern, replacement in _REDACTIONS:
        if name not in wanted:
            continue
        out, n = pattern.subn(replacement, out)
        if n:
            found.append(f"{name} x{n}")

    for key, original in stash.items():
        out = out.replace(key, original)
    return out, found


def filter_text(text: str, *, strip=False, redact_kinds=False):
    notes = []
    if strip:
        text, removed = strip_structured(text)
        notes.extend(removed)
    if redact_kinds is not False:
        text, found = redact(text, redact_kinds)
        notes.extend(found)
    return text, notes


class StreamRedactor:
    """Compatibility accumulator: release only complete text on flush.

    Regex candidates (notably email) have unbounded length. No fixed carry
    window can prove a prefix safe. A failed or oversized stream releases none.
    """
    def __init__(self, kinds=None, max_chars=2 * 1024 * 1024):
        self.kinds, self.max_chars = kinds, max_chars
        self._buffer = ""
        self.found = []

    def feed(self, chunk):
        self._buffer += chunk
        if len(self._buffer) > self.max_chars:
            self._buffer = ""
            raise ValueError("Reply exceeds streaming text limit")
        return ""

    def flush(self):
        text, self._buffer = self._buffer, ""
        text, self.found = filter_text(text, redact_kinds=self.kinds)
        return text


# --------------------------------------------------------------------- wiring


def make_output_filter(strip: bool = True, redact_kinds: list | None = None,
                       on_finding=None):
    """Build an after-model callback.

    `on_finding(list[str])` receives what was changed, so the console can show it
    in the turn trace. Filtering silently would hide exactly the signal an author
    needs: a model leaking JSON is a prompt problem, not a formatting nuisance.
    """

    def after_model(callback_context=None, llm_response=None, **kwargs):
        response = llm_response if llm_response is not None else \
            kwargs.get("response")
        if response is None:
            return None
        # Partials carry the join whitespace between chunks -- see the module
        # docstring. Only the aggregated response is safe to rewrite.
        if getattr(response, "partial", False):
            return None

        content = getattr(response, "content", None)
        parts = getattr(content, "parts", None) or []
        # Join visible parts before matching: identifiers can span text parts.
        visible = [p for p in parts if getattr(p, "text", None) is not None
                   and not getattr(p, "thought", False)]
        if not visible:
            return None
        original = "".join(p.text for p in visible)
        cleaned, notes = filter_text(original, strip=strip, redact_kinds=redact_kinds)
        if cleaned != original:
            # Native Part is mutable. Failure must abort rather than leak raw text.
            visible[0].text = cleaned
            for part in visible[1:]:
                part.text = ""
        if notes and on_finding:
            on_finding(notes)

        return None

    return after_model
