#!/usr/bin/env python3
"""Instruction compiler: agent.yaml -> a callable InstructionProvider.

## Why this exists

ADK's `LlmAgent.instruction` accepts a string or a callable. A string gets
`{state_key}` interpolation for free. A callable does not:

    # llm_agent.py:769-772
    if isinstance(self.instruction, str):
      return self.instruction, False        # bypass_state_injection = False
    else:
      instruction = self.instruction(ctx)   # ... = True

`bypass_state_injection=True` makes the flow skip `inject_session_state`
entirely, so **a callable that does not interpolate silently ships literal
`{pageName}` text to the model.** Any wrapper that reaches for a callable
inherits that obligation. This module discharges it.

We need a callable because real instructions are assembled, not static:
named slots filled from block files, a per-surface variant chosen at build
time, and layers composed in order. ADK can do conditionals via Jinja2, but
`use_jinja2` is a parameter of `inject_session_state`, not a field on
`LlmAgent` -- the flow always calls it with the default `False`. So it is
unreachable declaratively, and a compiled provider is the only route.

## Compatibility contract

`render()` reimplements `_render_with_regex` from
`google/adk/utils/instructions_utils.py`. Behaviours deliberately matched,
each verified against that source:

* `{key}`   -> `str(state[key])`; missing raises `KeyError`
* `{key?}`  -> same, but missing or `None` renders as `''`
* `{key}` where the value is `None` -> `''` even when not optional
* invalid state names are returned **verbatim, no error** (so `{my-key}`
  passes through as literal text -- which is why the validator rejects
  those at config time instead)
* `{artifact.name}` / `{artifact.name?}` -> artifact service lookup
* a template with no `{` is returned unchanged without scanning

Divergence, deliberate: this module raises `SlotError` for an unfilled
*named slot* (`{RESPONSE_STANDARDS}`) rather than treating it as state.
Slots are build-time; state is request-time. Conflating them is how you get
a prompt that silently ships a placeholder to production.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# ADK's own rule (_is_valid_state_name): a bare identifier, or one of the three
# scope prefixes plus an identifier.
_STATE_NAME = re.compile(r"^(app:|user:|temp:)?[A-Za-z_][A-Za-z0-9_]*$")

# ADK matches `{...}` with no nesting and no whitespace inside.
_PLACEHOLDER = re.compile(r"{+[^\{\}]*}+")

# A named slot is SCREAMING_SNAKE. Chosen because it cannot collide with a valid
# ADK state name in practice -- state keys in this codebase are lower_snake --
# so a template author can tell slots and state apart by eye.
_SLOT = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SlotError(ValueError):
    """A named slot in the template had no value supplied at build time."""


class TemplateError(ValueError):
    """The template or its block files are structurally wrong."""


@dataclass(frozen=True)
class Slot:
    """A build-time substitution: `{NAME}` <- the contents of a block file.

    `variants` supports per-surface forms of a block, such as web/tablet/mobile.
    `select` names which variant this build uses.
    """

    name: str
    path: pathlib.Path | None = None
    variants: Mapping[str, pathlib.Path] = field(default_factory=dict)
    select: str | None = None

    def resolve(self, root: pathlib.Path) -> str:
        if self.variants:
            if self.select is None:
                raise SlotError(
                    f"slot {self.name!r} declares variants "
                    f"({', '.join(sorted(self.variants))}) but no `select`")
            if self.select not in self.variants:
                raise SlotError(
                    f"slot {self.name!r} selects variant {self.select!r}, "
                    f"which is not one of: {', '.join(sorted(self.variants))}")
            target = root / self.variants[self.select]
        elif self.path is not None:
            target = root / self.path
        else:
            raise SlotError(f"slot {self.name!r} has neither `path` nor `variants`")

        if not target.is_file():
            raise SlotError(f"slot {self.name!r} -> {target} not found")
        return target.read_text(encoding="utf-8").strip()


@dataclass
class CompiledInstruction:
    """A template with build-time slots already substituted.

    Split from the provider so the console and `adkl explain` can inspect the
    resolved text -- and its remaining runtime placeholders -- without needing a
    live ADK session.
    """

    text: str
    slots_filled: dict[str, int]          # slot name -> chars substituted
    state_keys: dict[str, bool]           # state key -> optional?
    artifact_keys: dict[str, bool]
    passthrough: list[str]                # placeholders ADK will emit verbatim

    @property
    def required_state(self) -> list[str]:
        return sorted(k for k, opt in self.state_keys.items() if not opt)

    @property
    def optional_state(self) -> list[str]:
        return sorted(k for k, opt in self.state_keys.items() if opt)


def _split(raw: str) -> tuple[str, bool]:
    """`{key?}` -> ('key', True). Mirrors instructions_utils.py:135-138."""
    name = raw.lstrip("{").rstrip("}").strip()
    if name.endswith("?"):
        return name[:-1], True   # not removesuffix(): 3.8 compatibility
    return name, False


def compile_instruction(
    template_path: pathlib.Path,
    root: pathlib.Path,
    slots: list[Slot],
) -> CompiledInstruction:
    """Substitute named slots and classify what is left for runtime.

    Slot substitution is done in one pass over the template rather than
    repeated `str.replace`, so a block whose *contents* happen to contain
    `{SOMETHING}` cannot be re-substituted.
    """
    if not template_path.is_file():
        raise TemplateError(f"instruction template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")

    by_name = {s.name: s for s in slots}
    if len(by_name) != len(slots):
        dupes = sorted({s.name for s in slots if sum(1 for x in slots if x.name == s.name) > 1})
        raise TemplateError(f"duplicate slot declarations: {', '.join(dupes)}")

    resolved: dict[str, str] = {}
    filled: dict[str, int] = {}
    state_keys: dict[str, bool] = {}
    artifact_keys: dict[str, bool] = {}
    passthrough: list[str] = []
    unfilled: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        token = m.group()

        # `{{...}}` is an escaped literal brace pair. Compiling it down to
        # `{...}` puts a single pair in the finished text, and ADK -- which has
        # no escape of its own, and returns anything that is not a valid state
        # name verbatim (instructions_utils.py:159-160) -- passes it to the
        # model unchanged. So the model sees exactly `{...}`.
        #
        # Without this, a prompt that documents a JSON-ish shape ("options,
        # each {label: "A"|"B", text}") is indistinguishable from a mistyped
        # state key, and the validator has to either reject real prompts or
        # stop reporting real typos. clawtutor's tutor prompt has exactly one
        # of these.
        if len(token) >= 4 and token.startswith("{{") and token.endswith("}}"):
            return token[1:-1]

        name, optional = _split(token)

        if _SLOT.match(name):
            slot = by_name.get(name)
            if slot is None:
                unfilled.append(name)
                return token
            if name not in resolved:
                resolved[name] = slot.resolve(root)
                filled[name] = len(resolved[name])
            return resolved[name]

        if name.startswith("artifact."):
            artifact_keys[name[len("artifact."):]] = optional
            return token

        if _STATE_NAME.match(name):
            # An optional use anywhere makes the key optional overall: ADK
            # renders '' rather than raising, so the prompt survives without it.
            state_keys[name] = state_keys.get(name, True) and optional
            return token

        # ADK returns invalid state names verbatim with no error. Surface it so
        # `explain` can show what will reach the model as literal text.
        passthrough.append(name)
        return token

    text = _PLACEHOLDER.sub(_sub, template) if "{" in template else template

    if unfilled:
        raise SlotError(
            f"template references slot(s) with no declaration: "
            f"{', '.join(sorted(set(unfilled)))}. Declare them under "
            f"`agent.instruction.slots`, or rename if they were meant to be "
            f"state keys (state keys are lower_snake, slots are UPPER_SNAKE).")

    declared_unused = sorted(set(by_name) - set(resolved))
    if declared_unused:
        raise TemplateError(
            f"slot(s) declared but never referenced in the template: "
            f"{', '.join(declared_unused)}. A slot that is never substituted is "
            f"silently dead -- remove it or add `{{{declared_unused[0]}}}` to "
            f"the template.")

    return CompiledInstruction(
        text=text,
        slots_filled=filled,
        state_keys=state_keys,
        artifact_keys=artifact_keys,
        passthrough=sorted(set(passthrough)),
    )


def compile_config(root: pathlib.Path, doc: dict) -> CompiledInstruction:
    """Compile exactly the configured template and selected slots for all surfaces."""
    inst = (doc.get("agent") or {}).get("instruction") or {}
    slots = []
    for name, spec in (inst.get("slots") or {}).items():
        if isinstance(spec, str):
            slots.append(Slot(name, pathlib.Path(spec)))
        else:
            slots.append(Slot(name, variants={k: pathlib.Path(v) for k, v
                              in (spec.get("variants") or {}).items()}, select=spec.get("select")))
    return compile_instruction(root / inst["template"], root, slots)


# --------------------------------------------------------------- runtime render

async def render(
    text: str,
    state: Mapping[str, Any],
    *,
    agent_name: str = "agent",
    load_artifact: Callable[[str], Any] | None = None,
) -> str:
    """Interpolate runtime placeholders. Reimplements ADK's regex engine.

    Kept as a standalone async function -- not a method -- so it can be unit
    tested against a plain dict without constructing a ReadonlyContext or an
    InvocationContext.
    """
    if "{" not in text:
        return text

    out: list[str] = []
    last = 0
    for m in _PLACEHOLDER.finditer(text):
        out.append(text[last:m.start()])
        out.append(await _render_one(m.group(), state, agent_name, load_artifact))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


async def _render_one(
    token: str,
    state: Mapping[str, Any],
    agent_name: str,
    load_artifact: Callable[[str], Any] | None,
) -> str:
    name, optional = _split(token)

    if name.startswith("artifact."):
        key = name[len("artifact."):]
        if load_artifact is None:
            raise ValueError("Artifact service is not initialized.")
        artifact = load_artifact(key)
        if hasattr(artifact, "__await__"):
            artifact = await artifact
        if artifact is None:
            if optional:
                return ""
            raise KeyError(f"Artifact '{key}' not found in agent '{agent_name}'.")
        return str(artifact)

    if not _STATE_NAME.match(name):
        return token  # ADK's behaviour: verbatim, no error

    if name in state:
        value = state[name]
        return "" if value is None else str(value)
    if optional:
        return ""
    raise KeyError(f"Context variable not found: `{name}` in agent '{agent_name}'.")


def build_provider(
    compiled: CompiledInstruction,
    *,
    agent_name: str = "agent",
) -> Callable[[Any], Any]:
    """Wrap a CompiledInstruction as an ADK InstructionProvider.

    The returned callable takes a `ReadonlyContext` and returns an awaitable --
    ADK accepts either sync or async providers. State is read through the
    context so `app:`/`user:`/`temp:` prefixes resolve the way ADK expects.
    """

    async def provider(ctx: Any) -> str:
        state = getattr(ctx, "state", None)
        if state is None:
            inv = getattr(ctx, "_invocation_context", None)
            state = getattr(getattr(inv, "session", None), "state", {}) or {}

        loader = None
        inv = getattr(ctx, "_invocation_context", None)
        svc = getattr(inv, "artifact_service", None)
        if svc is not None:
            session = inv.session

            def loader(filename: str):  # noqa: F811 -- intentional local shadow
                return svc.load_artifact(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                    filename=filename,
                )

        return await render(
            compiled.text, state,
            agent_name=getattr(ctx, "agent_name", agent_name),
            load_artifact=loader,
        )

    provider.__adkl_compiled__ = compiled  # type: ignore[attr-defined]
    return provider


# --------------------------------------------------------------------- cli

def _main() -> int:
    """`python3 -m adkl.instruction <agent.yaml>` -- show the compiled prompt.

    A preview of `adkl explain`: the value of a compiler you cannot inspect is
    limited, and the first question anyone asks is "what prompt did that produce?"
    """
    import argparse
    import yaml

    ap = argparse.ArgumentParser(description="Compile and inspect an instruction.")
    ap.add_argument("agent_yaml", type=pathlib.Path)
    ap.add_argument("--surface", default=None,
                    help="override every slot's variant selection (e.g. mobile)")
    ap.add_argument("--text", action="store_true", help="print the prompt only")
    args = ap.parse_args()

    root = args.agent_yaml.parent
    doc = yaml.safe_load(args.agent_yaml.read_text(encoding="utf-8")) or {}
    inst = (doc.get("agent") or {}).get("instruction") or {}

    slots: list[Slot] = []
    for name, spec in (inst.get("slots") or {}).items():
        if isinstance(spec, str):
            slots.append(Slot(name, pathlib.Path(spec)))
        else:
            slots.append(Slot(
                name,
                variants={k: pathlib.Path(v)
                          for k, v in (spec.get("variants") or {}).items()},
                select=args.surface or spec.get("select")))

    try:
        c = compile_instruction(root / inst["template"], root, slots)
    except (SlotError, TemplateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.text:
        print(c.text)
        return 0

    print(f"template: {inst['template']}"
          + (f"   surface={args.surface}" if args.surface else ""))
    print(f"compiled: {len(c.text)} chars, ~{len(c.text) // 4} est. tokens\n")

    if c.slots_filled:
        print("slots filled:")
        for k, n in sorted(c.slots_filled.items()):
            print(f"  {k:32} {n:>6} chars")
        print()
    if c.required_state:
        print(f"required state ({len(c.required_state)}) -- absent at runtime raises:")
        for k in c.required_state:
            print(f"  {k}")
        print()
    if c.optional_state:
        print(f"optional state ({len(c.optional_state)}) -- absent renders empty:")
        print("  " + ", ".join(c.optional_state) + "\n")
    if c.artifact_keys:
        print("artifacts: " + ", ".join(
            f"{k}{'?' if opt else ''}" for k, opt in sorted(c.artifact_keys.items())) + "\n")
    if c.passthrough:
        print("WARNING -- emitted to the model verbatim (invalid state names):")
        for k in c.passthrough:
            print(f"  {{{k}}}")
        print()

    print("--- prompt ---")
    print(c.text)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
