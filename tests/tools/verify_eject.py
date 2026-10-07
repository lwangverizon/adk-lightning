#!/usr/bin/env python3
"""Verify ejected code against real ADK, and against the compiler.

Two claims this repo makes that are worth more than an assertion:

  1. `adkl eject` emits plain ADK that actually constructs an agent.
  2. adk-lightning contributes zero runtime, so the ejected prompt is not an
     approximation of what was running -- it is the same thing.

Both are checked here by importing the generated module under an interpreter
that has real ADK installed, building the agent, and diffing the prompt the
generated provider produces against the one the compiler produces.

Not a unit test: it needs an ADK-bearing interpreter, so it lives in tests/tools/
and is run deliberately (in CI, or after an ADK upgrade) rather than in the
normal suite.

Usage:
  python3 tests/tools/verify_eject.py <agent.yaml> [--python /path/to/python]

Exit code is the number of failed checks.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

DEFAULT_PY = REPO / ".venv/bin/python"

# Runs under the ADK interpreter. Imports the generated module, builds the agent,
# and prints the rendered prompt so the caller can diff it.
PROBE = '''
import importlib.util, json, pathlib, sys

spec = importlib.util.spec_from_file_location("ejected", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)          # raises if the generated code is not valid ADK

class Ctx:
    def __init__(self, state):
        self.state = state
        self.agent_name = m.root_agent.name

facts = {
    "agent_name": m.root_agent.name,
    "model": str(m.root_agent.model),
    "app_name": m.app.name,
    "skills": [s.name for s in m.SKILLS],
    "instruction_is_callable": callable(m.root_agent.instruction),
}
print("__FACTS__" + json.dumps(facts))

state = json.loads(sys.argv[2])
sys.stdout.write("__PROMPT__")
sys.stdout.write(m._instruction(Ctx(state)))
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_yaml", type=pathlib.Path)
    ap.add_argument("--python", type=pathlib.Path, default=DEFAULT_PY,
                    help="interpreter with google-adk installed")
    args = ap.parse_args()

    import json

    import yaml

    from adkl import eject, instruction

    if not args.python.is_file():
        print(f"error: no ADK interpreter at {args.python}")
        print("      pass --python /path/to/venv/bin/python")
        return 2

    root = args.agent_yaml.parent
    doc = yaml.safe_load(args.agent_yaml.read_text(encoding="utf-8")) or {}

    failures = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
            if detail:
                print("        " + detail.replace("\n", "\n        "))

    # 1. generate into a temp dir at the same depth as eject.target, so the
    #    relative skills path in the emitted code is exercised for real.
    target_rel = (doc.get("eject") or {}).get("target") or "generated/agent.py"
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        target = pathlib.Path(tmp) / pathlib.Path(target_rel).name
        try:
            code = eject.generate(args.agent_yaml, target)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        target.write_text(code, encoding="utf-8")

        print(f"eject {args.agent_yaml}")
        check(True, f"generated {len(code.splitlines())} lines")

        # 2. only the feature runtime may come from this library
        leaks = [l.strip() for l in code.splitlines()
                 if l.strip().startswith(("import adkl", "from adkl"))
                 and not l.strip().startswith("from adkl.retrieval import")]
        check(not leaks, "imports only adkl.retrieval from this library",
              "; ".join(leaks))

        # 3. required state for the probe
        slots = []
        inst = (doc.get("agent") or {}).get("instruction") or {}
        for name, spec in (inst.get("slots") or {}).items():
            if isinstance(spec, str):
                slots.append(instruction.Slot(name, pathlib.Path(spec)))
            else:
                slots.append(instruction.Slot(
                    name,
                    variants={k: pathlib.Path(v) for k, v
                              in (spec.get("variants") or {}).items()},
                    select=spec.get("select")))
        compiled = instruction.compile_instruction(
            root / inst["template"], root, slots)
        state = {k: f"<{k}>" for k in compiled.required_state}

        # 4. import under real ADK
        proc = subprocess.run(
            [str(args.python), "-c", PROBE, str(target), json.dumps(state)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            check(False, "imports under real ADK",
                  proc.stderr.strip().splitlines()[-1] if proc.stderr else "")
            return failures

        out = proc.stdout
        facts = json.loads(out.split("__FACTS__", 1)[1].split("\n", 1)[0])
        ejected_prompt = out.split("__PROMPT__", 1)[1]

        check(True, "imports under real ADK and constructs LlmAgent + App")
        check(facts["instruction_is_callable"],
              "instruction is a provider callable")
        expected_skills = list((doc.get("skills") or {}).get("include") or [])
        check(facts["skills"] == expected_skills,
              f"loads {len(expected_skills)} skills from disk",
              f"got {facts['skills']}")

        # 5. the claim that matters: same prompt as the compiler
        import asyncio
        loop = asyncio.new_event_loop()
        compiler_prompt = loop.run_until_complete(
            instruction.render(compiled.text, state,
                               agent_name=facts["agent_name"]))
        loop.close()

        if ejected_prompt == compiler_prompt:
            check(True, f"prompt is byte-identical to the compiler "
                        f"({len(ejected_prompt):,} bytes)")
        else:
            import difflib
            d = "\n".join(list(difflib.unified_diff(
                compiler_prompt.splitlines(), ejected_prompt.splitlines(),
                "compiler", "ejected", lineterm="", n=1))[:12])
            check(False, "prompt is byte-identical to the compiler", d)

    print()
    print(f"{failures} failure(s)")
    return failures


if __name__ == "__main__":
    sys.exit(main())
