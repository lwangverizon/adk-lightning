#!/usr/bin/env python3
"""Structured view of a SKILL.md: parse into sections, edit, write back.

## Why

The console previously exposed the instruction body as one raw markdown
textarea. That asks a business user to know markdown *and* the house
conventions, and it makes the whole body one diff hunk.

Real skills already have a consistent shape (verified across the prospect
set):

    ## Workflows          (or "## Workflow")
    ### <Intent name>
    <when-to-use prose>
    - [ ] Step 1: ...
    - [ ] Step 2: ...
    ## Gotchas
    - **Label:** text
    ## Simplicity Context   (free prose, skill-specific)

So the body is not freeform -- it is workflows containing ordered steps, plus
a gotchas list, plus optional prose sections. This module makes that structure
addressable so the UI can offer one field per thing.

## The hard constraint

Round-tripping structured edits through markdown is where these tools usually
rot: parse, re-emit, and every unrelated line moves. The rule here, enforced
by tests:

  **A section the user did not touch is written back byte-identical.**

That is achieved by keeping each section's raw source and only re-rendering
sections that actually changed. It is the same rule the server already applies
to descriptions and tool order, for the same reason -- if a console write is
distinguishable from a hand edit, review dies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# `- [ ] Step 3: text` / `- [x] Step 3: text` / `- [ ] text`
_STEP = re.compile(r"^-\s*\[([ xX])\]\s*(?:(Step\s*[\w.]+)\s*[:.]\s*)?(.*)$")
# `- **Label:** text`  (gotchas convention)
_LABELLED = re.compile(r"^-\s*\*\*(.+?):?\*\*:?\s*(.*)$")
_H2 = re.compile(r"^##\s+(.+?)\s*$")
_H3 = re.compile(r"^###\s+(.+?)\s*$")

# H2 headings recognized as structured rather than prose. Matched
# case-insensitively and singular/plural, because real skills use both
# "## Workflow" and "## Workflows".
_WORKFLOWS = {"workflow", "workflows"}
_GOTCHAS = {"gotcha", "gotchas"}


@dataclass
class Step:
    """One `- [ ] Step N: ...` item, possibly spanning continuation lines.

    `raw` holds the verbatim source lines. Steps in real skills wrap across
    2-4 lines with hanging indents and nested bullets; re-emitting them as one
    long line is a diff on every step in the section even when only one was
    edited. So an untouched step is written back exactly as found, and only its
    `Step N:` prefix is rewritten if the number changed.
    """

    text: str
    done: bool = False
    raw: str | None = None

    gap_before: int = 0            # blank lines preceding this step
    label: str | None = None       # the authored "Step N" text, verbatim

    def render(self, index: int) -> str:
        """Re-emit. `index` is only a fallback for steps with no authored label.

        Numbers are author-controlled, not positional: real skills use "Step 0:"
        for a conditional preamble and restart numbering inside one workflow.
        Deriving the number from position rewrote both -- silently, and it looked
        like a successful save. So an existing step keeps its label and only its
        text is replaced.
        """
        box = "x" if self.done else " "
        prefix = f"- [{box}] {self.label}: " if self.label else f"- [{box}] Step {index}: "
        if self.raw is not None:
            return re.sub(r"^-\s*\[[ xX]\]\s*(?:Step\s*[\w.]+\s*[:.]\s*)?",
                          prefix, self.raw, count=1)
        return prefix + self.text


@dataclass
class Workflow:
    """An `### Intent` block: prose describing when to use it, then steps."""

    name: str
    when: str = ""                 # prose between the heading and the first step
    steps: list[Step] = field(default_factory=list)
    raw: str | None = None         # verbatim source; dropped once edited

    def render(self) -> str:
        if self.raw is not None:
            return self.raw
        out = [f"### {self.name}", ""]
        if self.when.strip():
            out += [self.when.strip(), ""]

        # Authored labels are kept only if they are still unique. Inserting or
        # deleting a step would otherwise produce two "Step 2"s, so a structural
        # change falls back to positional numbering for the whole workflow.
        labels = [st.label for st in self.steps]
        renumber = (any(l is None for l in labels)
                    or len(set(labels)) != len(labels))

        for i, st in enumerate(self.steps, 1):
            out += [""] * st.gap_before
            if renumber:
                out.append(re.sub(
                    r"^-\s*\[([ xX])\]\s*(?:Step\s*[\w.]+\s*[:.]\s*)?",
                    lambda m, i=i: f"- [{m.group(1)}] Step {i}: ",
                    st.raw if st.raw is not None else
                    f"- [{'x' if st.done else ' '}] {st.text}", count=1))
            else:
                out.append(st.render(i))
        return "\n".join(out)


@dataclass
class Section:
    """A `##` section. `kind` drives which editor the console shows."""

    title: str
    kind: str                      # workflows | gotchas | prose
    prose: str = ""
    lead: str = ""                 # prose between the ## heading and the first ###
    workflows: list[Workflow] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)   # gotchas: {label, text}
    raw: str | None = None
    gap: int = 1                   # blank lines between heading and content
    sep_before: int = 1            # blank lines before this section's heading

    def render(self) -> str:
        if self.raw is not None:
            return self.raw
        out = [f"## {self.title}"] + [""] * self.gap
        if self.kind == "workflows":
            if self.lead.strip():
                out += [self.lead.strip(), ""]
            for w in self.workflows:
                out += [w.render(), ""]
        elif self.kind == "gotchas":
            for it in self.items:
                # Same rule as steps: an untouched item keeps its original
                # wrapping, so editing one gotcha does not reflow the others.
                if it.get("raw"):
                    out.append(it["raw"])
                    continue
                label = (it.get("label") or "").strip()
                text = (it.get("text") or "").strip()
                out.append(f"- **{label}:** {text}" if label else f"- {text}")
            out.append("")
        else:
            out += [self.prose.strip(), ""]
        return "\n".join(out).rstrip() + "\n"


@dataclass
class SkillDoc:
    """A parsed skill body. `preamble` is anything before the first `##`."""

    preamble: str = ""
    sections: list[Section] = field(default_factory=list)
    trailing_newline: bool = True   # some real files end without one
    leading_blanks: int = 0         # blank lines between frontmatter and content

    # ------------------------------------------------------------- parsing

    @classmethod
    def parse(cls, body: str) -> "SkillDoc":
        lines = body.splitlines()
        leading = 0
        for ln in lines:
            if ln.strip():
                break
            leading += 1
        doc = cls(trailing_newline=body.endswith("\n"), leading_blanks=leading)

        # split into (h2 title, [lines]) chunks
        chunks: list[tuple[str | None, list[str]]] = [(None, [])]
        for ln in lines:
            m = _H2.match(ln)
            if m:
                chunks.append((m.group(1), []))
            else:
                chunks[-1][1].append(ln)

        for idx, (title, content) in enumerate(chunks):
            text = "\n".join(content)
            if title is None:
                doc.preamble = text.strip()
                continue

            # Blank lines the PREVIOUS chunk ended with belong to this boundary.
            prev = chunks[idx - 1][1]
            sep = 0
            for ln in reversed(prev):
                if ln.strip():
                    break
                sep += 1

            key = title.strip().lower()
            raw = f"## {title}\n" + text
            gap = 0
            for ln in content:
                if ln.strip():
                    break
                gap += 1
            # Classify by CONTENT, not just the heading. competitive-switch and
            # prospect-plan-and-perk-exploration carry "### Intent" blocks with
            # steps under headings like "## Simplicity Context"; keying only off
            # the title left 24 workflows and 76 steps unreachable in the UI.
            has_steps = any(_STEP.match(x.strip()) for x in content)
            if key in _WORKFLOWS or (has_steps and key not in _GOTCHAS):
                workflows, lead = _parse_workflows(content)
                doc.sections.append(
                    Section(title=title, kind="workflows", gap=gap, sep_before=sep,
                            lead=lead, workflows=workflows, raw=raw))
            elif key in _GOTCHAS:
                doc.sections.append(
                    Section(title=title, kind="gotchas", gap=gap, sep_before=sep,
                            items=_parse_labelled(content), raw=raw))
            else:
                doc.sections.append(
                    Section(title=title, kind="prose", gap=gap, sep_before=sep,
                            prose=text.strip(), raw=raw))
        return doc

    # ------------------------------------------------------------ emitting

    def render(self) -> str:
        """Re-emit the body. Untouched sections are byte-identical."""
        # Build with explicit separators rather than "\n".join, which would force
        # exactly one blank line between every section.
        #
        # Boundary arithmetic differs on ONE side, which is why this is not a
        # uniform separator. A section is emitted with a trailing newline (below),
        # so `sep_before` blank lines land on a line that is already terminated.
        # The preamble is `.strip()`ed and carries no terminating newline, so its
        # boundary needs one extra to end its last line first.
        #
        # Found by `general-support` failing the byte-identical round-trip when it
        # joined the example: it is the first skill with lead prose directly before
        # a section heading. Every skill already there happened to avoid that
        # shape, so 23 round-trip tests passed over a renderer that dropped a
        # blank line in it -- editing that skill in the console would have
        # silently reflowed the file. A round-trip suite only covers the shapes it
        # is given, and a corpus that grows finds bugs a fixed one cannot.
        chunks: list[str] = []
        after_preamble = False
        if self.preamble.strip():
            chunks.append(self.preamble.strip())
            after_preamble = True
        for i, sec in enumerate(self.sections):
            if chunks or i:
                extra = 1 if (after_preamble and i == 0) else 0
                chunks.append("\n" * (max(0, sec.sep_before) + extra))
            chunks.append(sec.render().rstrip() + "\n")
        out = "\n" * self.leading_blanks + "".join(chunks).rstrip()
        return out + "\n" if self.trailing_newline else out

    # --------------------------------------------------------------- json

    def to_json(self) -> dict[str, Any]:
        """Serialize for the console.

        `items` is deep-copied. Returning the live dicts let a caller mutate the
        payload and the parsed doc simultaneously, so the change-detection in
        `from_json` compared a section to itself and silently discarded every
        gotcha edit. Caught by test_editing_a_gotcha_leaves_workflows_untouched.
        """
        return {
            "preamble": self.preamble,
            "sections": [
                {
                    "title": s.title,
                    "kind": s.kind,
                    "prose": s.prose,
                    "lead": s.lead,
                    "items": [{"label": i.get("label", ""), "text": i.get("text", "")}
                              for i in s.items],
                    "workflows": [
                        {"name": w.name, "when": w.when,
                         "steps": [{"text": st.text, "done": st.done,
                                   "label": st.label} for st in w.steps]}
                        for w in s.workflows
                    ],
                }
                for s in self.sections
            ],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any], original: "SkillDoc") -> "SkillDoc":
        """Rebuild from console JSON, preserving raw source where unchanged.

        `original` is the parsed on-disk doc. Any section whose incoming content
        matches it keeps its `raw`, so re-emitting is byte-identical. Only edited
        sections are re-rendered.
        """
        doc = cls(preamble=payload.get("preamble", original.preamble),
                  trailing_newline=original.trailing_newline,
                  leading_blanks=original.leading_blanks)
        by_title = {s.title: s for s in original.sections}

        for spec in payload.get("sections") or []:
            title = spec.get("title", "")
            kind = spec.get("kind", "prose")
            prior = by_title.get(title)

            sec = Section(title=title, kind=kind,
                          gap=prior.gap if prior else 1,
                          sep_before=prior.sep_before if prior else 1,
                          lead=spec.get("lead", prior.lead if prior else ""))
            if kind == "workflows":
                prior_wf = {w.name: w for w in (prior.workflows if prior else [])}
                for w in spec.get("workflows") or []:
                    wf = Workflow(name=w.get("name", ""), when=w.get("when", ""))
                    old = prior_wf.get(wf.name)
                    # Match old steps by text so an unedited step keeps its raw
                    # source even if steps around it were inserted or reordered.
                    old_by_text = {}
                    if old:
                        for st in old.steps:
                            old_by_text.setdefault(st.text.strip(), st)
                    for st in w.get("steps") or []:
                        text = st.get("text", "")
                        step = Step(text=text, done=bool(st.get("done")),
                                    label=st.get("label"))
                        match = old_by_text.get(text.strip())
                        if match is not None and match.done == step.done:
                            step.raw = match.raw
                        wf.steps.append(step)
                    sec.workflows.append(wf)
            elif kind == "gotchas":
                prior_raw = {}
                if prior:
                    for it in prior.items:
                        key = ((it.get("label") or "").strip(),
                               (it.get("text") or "").strip())
                        prior_raw.setdefault(key, it.get("raw"))
                for it in spec.get("items") or []:
                    entry = {"label": it.get("label", ""), "text": it.get("text", "")}
                    key = (entry["label"].strip(), entry["text"].strip())
                    if prior_raw.get(key):
                        entry["raw"] = prior_raw[key]
                    sec.items.append(entry)
            else:
                sec.prose = spec.get("prose", "")

            # Byte-identical passthrough when nothing in this section changed.
            if prior is not None and prior.kind == kind and _same(prior, sec):
                sec.raw = prior.raw
            doc.sections.append(sec)
        return doc


def _same(a: Section, b: Section) -> bool:
    """Semantic equality, ignoring whitespace the renderer would normalize."""
    if a.kind != b.kind:
        return False
    if a.kind == "prose":
        return a.prose.strip() == b.prose.strip()
    if a.kind == "gotchas":
        # Was a lambda whose comprehension shadowed its own parameter, so it
        # compared a.items to itself and every gotcha edit was silently
        # discarded. Caught by test_editing_a_gotcha_leaves_workflows_untouched.
        def _norm(items: list[dict]) -> list[tuple[str, str]]:
            return [((i.get("label") or "").strip(), (i.get("text") or "").strip())
                    for i in items]

        return _norm(a.items) == _norm(b.items)
    if a.lead.strip() != b.lead.strip():
        return False
    if len(a.workflows) != len(b.workflows):
        return False
    for x, y in zip(a.workflows, b.workflows):
        if x.name.strip() != y.name.strip() or x.when.strip() != y.when.strip():
            return False
        if len(x.steps) != len(y.steps):
            return False
        for s, t in zip(x.steps, y.steps):
            if s.text.strip() != t.text.strip() or s.done != t.done:
                return False
    return True


# ----------------------------------------------------------------- helpers

def _parse_workflows(lines: list[str]) -> tuple[list[Workflow], str]:
    blocks: list[tuple[str, list[str]]] = []
    preamble: list[str] = []
    for ln in lines:
        m = _H3.match(ln)
        if m:
            blocks.append((m.group(1), []))
        elif blocks:
            blocks[-1][1].append(ln)
        else:
            preamble.append(ln)

    # Some real skills put steps directly under "## Workflow" with no "###"
    # heading at all (bill-upload). Synthesize one unnamed workflow so those
    # steps are still editable rather than invisible.
    lead = ""
    if not blocks and any(_STEP.match(x.strip()) for x in preamble):
        blocks = [("", preamble)]
    else:
        lead = "\n".join(preamble).strip()

    out: list[Workflow] = []
    for name, content in blocks:
        wf = Workflow(name=name)
        pre: list[str] = []
        current: Step | None = None
        raw_lines: list[str] = []

        def _flush() -> None:
            if current is not None:
                current.raw = "\n".join(raw_lines).rstrip()

        pending_blanks = 0
        for ln in content:
            # Indented checkboxes are nested content, not steps.
            m = _STEP.match(ln) if not ln[:1].isspace() else None
            if m:
                _flush()
                current = Step(text=m.group(3).strip(), done=m.group(1).lower() == "x",
                               gap_before=pending_blanks,
                               label=(m.group(2) or "").strip() or None)
                pending_blanks = 0
                wf.steps.append(current)
                raw_lines = [ln]
            elif current is not None and not ln.strip():
                pending_blanks += 1
            elif current is not None and ln.strip():
                # Continuation: real steps wrap across several lines with hanging
                # indents and nested bullets. Keep both the joined text (for
                # editing) and the verbatim lines (for byte-identical re-emit).
                current.text += " " + ln.strip()
                raw_lines += [""] * pending_blanks + [ln]
                pending_blanks = 0
            elif current is None:
                pre.append(ln)
            else:
                raw_lines.append(ln)
        _flush()
        wf.when = "\n".join(pre).strip()
        out.append(wf)
    return out, lead


def _parse_labelled(lines: list[str]) -> list[dict]:
    """Parse `- **Label:** text` items, keeping verbatim source per item."""
    items: list[dict] = []
    raw: list[list[str]] = []
    for ln in lines:
        stripped = ln.strip()
        m = _LABELLED.match(stripped)
        if m:
            items.append({"label": m.group(1).strip(), "text": m.group(2).strip()})
            raw.append([ln])
        elif stripped.startswith("- "):
            items.append({"label": "", "text": stripped[2:].strip()})
            raw.append([ln])
        elif items and stripped:
            items[-1]["text"] = (items[-1]["text"] + " " + stripped).strip()
            raw[-1].append(ln)
    for it, lines_ in zip(items, raw):
        it["raw"] = "\n".join(lines_).rstrip()
    return items


if __name__ == "__main__":
    import pathlib
    import sys

    if len(sys.argv) < 2:
        sys.exit("usage: python3 -m adkl.skill_doc <SKILL.md>")
    src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    body = src.split("\n---", 2)[-1] if src.startswith("---") else src
    doc = SkillDoc.parse(body)
    print(f"preamble: {len(doc.preamble)} chars")
    for s in doc.sections:
        detail = ""
        if s.kind == "workflows":
            detail = f"{len(s.workflows)} workflows, " \
                     f"{sum(len(w.steps) for w in s.workflows)} steps"
        elif s.kind == "gotchas":
            detail = f"{len(s.items)} items"
        else:
            detail = f"{len(s.prose)} chars"
        print(f"  [{s.kind:9}] {s.title:24} {detail}")
    # round-trip check
    same = doc.render().strip() == body.strip()
    print(f"\nbyte-identical round trip: {same}")
