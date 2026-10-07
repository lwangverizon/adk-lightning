#!/usr/bin/env python3
"""adkl explain — print the resolved agent.

Reports configuration intent separately from preview/export implementation
support. A valid pipeline is not evidence that its callbacks were attached.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

if __package__:
    from . import agent_schema, capabilities, features, instruction, skill_lint
else:  # pragma: no cover -- script mode
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from adkl import agent_schema, capabilities, features, instruction, skill_lint


def _c(text: str, code: str, on: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if on else text


def explain(agent_yaml: pathlib.Path, env: str | None = None,
            color: bool = True) -> int:
    root = agent_yaml.parent
    doc = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}

    dim = lambda s: _c(s, "2", color)          # noqa: E731
    bold = lambda s: _c(s, "1", color)         # noqa: E731
    red = lambda s: _c(s, "31", color)         # noqa: E731
    amber = lambda s: _c(s, "33", color)       # noqa: E731
    green = lambda s: _c(s, "32", color)       # noqa: E731

    errors, warnings = agent_schema.validate(doc, root, env)
    agent = doc.get("agent") or {}

    print(bold(f"agent {agent.get('name', '?')}")
          + dim(f"  ({agent_yaml})") + (dim(f"  env={env}") if env else ""))
    print(dim(f"  model {agent.get('model', '?')}"))
    print()

    # ---- instruction
    inst = agent.get("instruction") or {}
    if inst.get("template"):
        try:
            slots = []
            for name, spec in (inst.get("slots") or {}).items():
                if isinstance(spec, str):
                    slots.append(instruction.Slot(name, pathlib.Path(spec)))
                else:
                    slots.append(instruction.Slot(
                        name,
                        variants={k: pathlib.Path(v) for k, v
                                  in (spec.get("variants") or {}).items()},
                        select=spec.get("select")))
            c = instruction.compile_instruction(root / inst["template"], root, slots)
            print(bold("instruction"))
            print(f"  template          {inst['template']}")
            print(f"  compiled          {len(c.text):,} chars  "
                  + dim(f"~{len(c.text) // 4:,} est. tokens"))
            for k, n in sorted(c.slots_filled.items()):
                print(f"  slot {k:<28} {n:>6,} chars")
            if c.required_state:
                print(f"  required state     {', '.join(c.required_state)}"
                      + dim("   absent raises KeyError"))
            if c.optional_state:
                print(f"  optional state     {len(c.optional_state)} keys"
                      + dim("   absent renders empty"))
            for p in c.passthrough:
                print("  " + red(f"passthrough        {{{p}}} reaches the model "
                                 "as literal text"))
        except (instruction.SlotError, instruction.TemplateError) as exc:
            print(bold("instruction") + "  " + red(str(exc)))
        print()

    # ---- skills and their tool cost
    skills = doc.get("skills") or {}
    include = list(skills.get("include") or [])
    overrides = (skills.get("overrides") or {}).get(env or "", {})
    sdir = root / (skills.get("dir") or "skills")

    cat_rel = (doc.get("tools") or {}).get("catalog")
    cost: dict[str, int] = {}
    known: set = set()
    if cat_rel and (root / cat_rel).is_file():
        cat = json.loads((root / cat_rel).read_text(encoding="utf-8"))
        known = {t["name"] for t in cat.get("tools", [])}
        cost = {t["name"]: t.get("estimated_input_tokens_per_turn", 0)
                for t in cat.get("tools", [])}

    print(bold(f"skills ({len(include)})") + dim("   token cost is per turn, "
                                                 "charged whenever the skill is active"))
    worst = 0
    for name in include:
        rel = overrides.get(name, name)
        md = sdir / rel / "SKILL.md"
        if not md.is_file():
            print("  " + red(f"{name:<38} SKILL.md not found at {rel}/"))
            continue
        fm, body = skill_lint.parse_frontmatter(md)
        tools = (fm.get("metadata") or {}).get("adk_additional_tools") or []
        tools = tools if isinstance(tools, list) else []
        c_ = sum(cost.get(t, 0) for t in tools)
        worst = max(worst, c_)
        missing = [t for t in tools if known and t not in known]
        flag = red("  !") if missing else "   "
        tag = dim(f"  [{env}]") if name in overrides else ""
        print(f"  {name:<38} {len(tools):>2} tools  ~{c_:>6,} tok{flag}{tag}")
        for t in missing:
            print("      " + red(f"{t} is not in the catalog -- silently dropped "
                                 "at bind time"))
    if cost:
        allcost = sum(cost.values())
        print(dim(f"  worst single skill ~{worst:,} tok/turn; "
                  f"all {len(cost)} catalog tools bound at once would be "
                  f"~{allcost:,}"))
    print()

    # ---- features
    print(bold("features"))
    try:
        r = features.resolve(doc.get("features"))
        if not r.active:
            print(dim("  none active (features.pipeline is empty)"))
        for i, name in enumerate(r.active, 1):
            spec = features.REGISTRY[name]
            bits = [spec.kind.value]
            if spec.stage is not features.Stage.STABLE:
                bits.append(spec.stage.value)
            if spec.may_short_circuit:
                bits.append("short-circuits")
            print(f"  {i}. {green(name):<28} " + dim(f"[{', '.join(bits)}]"))
            print(dim(f"       {spec.summary}"))
            if spec.writes:
                print(dim(f"       writes {', '.join(sorted(spec.writes))}"))
            if spec.reads:
                print(dim(f"       reads  {', '.join(sorted(spec.reads))}"))
            cfg = r.config.get(name) or {}
            nondefault = {k: v for k, v in cfg.items()
                          if k in spec.options and v != spec.options[k][1]}
            if nondefault:
                print(dim("       set    "
                          + ", ".join(f"{k}={v}" for k, v in sorted(nondefault.items()))))
        for name in r.declared:
            print(f"     {dim(name):<28} " + dim("declared, not in pipeline"))
        for w in r.warnings:
            print("  " + amber("warn  ") + w)

        # Configuration is not proof that a callback/plugin was attached.
        print()
        print(bold("execution support"))
        for target in ("preview", "export"):
            report = (capabilities.export_report(doc) if target == "export"
                      else capabilities.manifest(r))
            print(f"  {target}: " + ("blocked" if report["blockers"] else
                  "partial" if report["warnings"] else "feature checks passed"))
            for message in report["blockers"]:
                print("    " + red(message))
                errors.append(f"{target}: {message}")
            for message in report["warnings"]:
                print("    " + amber(message))
    except features.FeatureError as exc:
        for line in str(exc).split("\n"):
            print("  " + red(line) if line.strip() else "")
    print()

    # ---- validation last, so it is the final word on screen
    if errors:
        print(bold(red(f"{len(errors)} error(s)")))
        for e in errors:
            print("  " + red(e))
    if warnings:
        print(bold(amber(f"{len(warnings)} warning(s)")))
        for w in warnings:
            print("  " + amber(w))
    if not errors and not warnings:
        print(green("no findings"))

    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Explain the resolved agent.")
    ap.add_argument("agent_yaml", type=pathlib.Path)
    ap.add_argument("--env", default=None, help="apply skills.overrides.<env>")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()
    return explain(args.agent_yaml, args.env,
                   color=not args.no_color and sys.stdout.isatty())


if __name__ == "__main__":
    sys.exit(main())
