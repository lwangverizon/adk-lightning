#!/usr/bin/env python3
"""Lint declared widget specs, so a broken pipeline fails at authoring time.

Every check here corresponds to a way the staged pattern fails **quietly**. That
is the selection rule: a misconfiguration that raises at the turn boundary needs
no linter, because the traceback names it. What needs a linter is the pipeline
that completes successfully and renders nothing.

The four phases and how each goes quiet:

    stage     a register with no stage tool can never fill -> never emits
    rerank    a rerank tool for an unstaged register reorders nothing
    contract  a role with no emitter shapes a reply for UI that never arrives;
              an emitter with no role renders UI the reply was not shaped for
    emit      no emitter at all: status "staged", clean turn, no widget

Ordered by what each finding costs, and every one names the phase, because
"widget_specs.foo.role is unused" is only actionable if you know which of the four
things to go look at.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml

# The docs tell a reader to run `python3 adkl/widget_lint.py ...`, so support
# script mode as well as package import -- same idiom as agent_schema.py.
if __package__:
    from . import widgets as W
else:  # pragma: no cover -- script mode
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from adkl import widgets as W

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"


class Finding:
    def __init__(self, level: str, spec: str, phase: str, message: str):
        self.level, self.spec, self.phase, self.message = level, spec, phase, message

    def to_json(self) -> dict:
        return {"level": self.level, "spec": self.spec,
                "phase": self.phase, "message": self.message}

    def __str__(self) -> str:
        where = f"{self.spec}" if self.spec else "widgets"
        return f"{self.level:5} [{self.phase:8}] {where}: {self.message}"


def _practice(spec) -> list:
    """Practice findings for one spec, declared or adopted.

    Separated so both paths share ONE implementation. When these lived inline in
    the declared-spec loop, adopted specs got no checks at all -- and those are
    the 18 real production specs, the only corpus the tool has.

    Keyed on what those specs actually look like: 12 of 18 have no
    expected_empty, one revives with an effectively uncapped counter, and every
    one carries a companion floor.
    """
    key = spec.key
    out: list[Finding] = []
    # --- emit practice ------------------------------------------------
    #
    # Structure rules above check the pipeline is WIRED. These check it is
    # wired WELL, keyed on what the 18 real prospect specs actually look
    # like: 12 of 18 have no expected_empty, one revives with an effectively
    # uncapped counter, and every one carries a companion floor.
    #
    # A data role whose register can empty is the sharpest of these: a
    # SYNTHESIS contract tells the model to synthesize over rows, so a build
    # that produces none leaves a reply referring to data that is not there.
    if spec.role and spec.role.value in {"data_primary", "data_with_written_table"} \
            and spec.expected_empty is None:
        out.append(Finding(
            WARN, key, "emit",
            f"{spec.role.value} with no expected_empty. The contract tells the "
            "model to synthesize OVER ROWS, so a build that legitimately "
            "returns none leaves a reply referring to data the customer "
            "cannot see -- and it is reported as an alert rather than a "
            "normal outcome"))
    elif spec.expected_empty is None:
        out.append(Finding(
            INFO, key, "emit",
            "no expected_empty. A build that legitimately produces nothing "
            "will be reported as an alert rather than a normal outcome"))

    if not spec.default_companion and not spec.companion_note:
        out.append(Finding(
            WARN, key, "emit",
            "no companion floor and no note, so this widget can reach the "
            "customer with NO visible text if the model happens to write "
            "nothing. A card with no words is not self-explanatory"))

    # An effectively-uncapped revive. `revive_cap` above ~100 is not a cap,
    # and each revived emit costs a real build -- so the register re-renders
    # on every later turn until something clears it.
    if spec.revive_when is not None and spec.revive_cap > 100:
        out.append(Finding(
            WARN, key, "emit",
            f"revive_cap is {spec.revive_cap}, which is not a cap. Each "
            f"revived emit costs a real build, so this re-renders on every "
            f"later turn until the register is cleared"))

    if spec.role is None and spec.build is not None:
        out.append(Finding(
            INFO, key, "contract",
            "emits UI with no role, so the reply is never shaped for it. "
            "The model may write text that duplicates or contradicts the "
            "card"))

    return out


def _deferred_hook(ref: str):
    """Validate reference syntax without importing customer code."""
    if not isinstance(ref, str) or ":" not in ref or not all(ref.split(":", 1)):
        raise W.WidgetError(f"hook must be 'module:attribute', got {ref!r}")

    def deferred(*args, **kwargs):
        raise W.WidgetError("structural lint hooks cannot execute")

    return deferred


def lint(doc: dict, catalog: dict | None = None,
         structural: bool = False) -> list[Finding]:
    """Check the declared widget block against the tool catalog.

    `structural=True` checks hook syntax without importing project code.
    Adopted registries require runtime checks and are reported as deferred. The pipeline findings -- a spec with
    no emitter, a duplicate widget id, a shared register -- do not need hooks and
    are the ones worth gating CI on. Explicit rather than automatic: silently
    demoting a real broken-hook error is how a typo reaches a turn.
    """
    out: list[Finding] = []
    block = (doc.get("widgets") or {})
    declared = block.get("specs") or {}
    adopted = block.get("adopt")

    # A `widgets:` block nested under `tools:` loads ZERO specs and says nothing.
    # Valid YAML can still put widgets at the wrong level; report that explicitly.
    misplaced = [k for k in ("tools", "agent", "skills", "features")
                 if isinstance(doc.get(k), dict) and "widgets" in doc[k]]
    for parent in misplaced:
        out.append(Finding(
            ERROR, "", "declare",
            f"`widgets:` is nested under `{parent}:`. It is a TOP-LEVEL block, so "
            f"nothing reads it there: zero specs load and no error is raised. "
            f"Move it to column 0."))

    if not declared and not adopted:
        return out

    # Adopted specs are compiled here too, so the PRACTICE rules can run on them.
    #
    # An earlier version returned early on adoption, which meant the 18 real
    # prospect specs got no checks at all -- and those are the specs that matter,
    # since the declared ones are usually a handful an author just wrote. The
    # fields the practice rules read (role, expected_empty, companion, revive)
    # all survive adoption, so skipping them was leaving the tool blind on the
    # only corpus it had.
    #
    # What genuinely cannot be checked on an adopted spec is a WIRING detail the
    # source repo owns -- stage tools, rerank tool, register naming -- because
    # adoption does not carry a declaration for them.
    adopted_specs: list = []
    if adopted and structural:
        out.append(Finding(INFO, "", "declare",
                           f"adopted specs from {adopted}: runtime hook and practice "
                           "checks deferred; structural lint does not import code"))
    elif adopted:
        try:
            adopted_specs = W.adopt(adopted)
        except W.WidgetError as exc:
            out.append(Finding(
                INFO if structural else ERROR, "", "declare",
                f"could not load adopted specs from {adopted}: {exc}"))
        else:
            out.append(Finding(
                INFO, "", "declare",
                f"{len(adopted_specs)} spec(s) adopted from {adopted}. Practice "
                f"checks apply; wiring checks (stage tools, rerank, register "
                f"naming) do not -- adoption carries no declaration for them."))

    pipeline = (doc.get("features") or {}).get("pipeline") or []
    if adopted and "staged_widgets" not in pipeline:
        out.append(Finding(
            ERROR, "", "emit",
            "specs are adopted but 'staged_widgets' is not in "
            "features.pipeline, so nothing will drain the registers."))

    # An emit stage in the pipeline is what actually runs the drain. Declaring
    # specs without it is the exact silent failure this module exists for: the
    # tools stage, the turn completes, and nothing renders.
    pipeline = (doc.get("features") or {}).get("pipeline") or []
    if declared and "staged_widgets" not in pipeline:
        out.append(Finding(
            ERROR, "", "emit",
            f"{len(declared)} widget spec(s) declared but 'staged_widgets' is "
            f"not in features.pipeline. Nothing will drain the registers: tools "
            f"will return status 'staged', turns will complete clean, and no "
            f"widget will ever render."))

    known_tools = {t["name"] for t in (catalog or {}).get("tools", [])}
    widget_tools = {t["name"] for t in (catalog or {}).get("tools", [])
                    if (t.get("emits_widget") or {}).get("value")}

    seen_ids: dict[str, str] = {}
    seen_registers: dict[str, str] = {}
    staged_by: dict[str, list] = {}

    for key, raw in declared.items():
        try:
            spec = W.spec_from_dict(key, raw, hook_resolver=_deferred_hook) \
                if structural else W.spec_from_dict(key, raw)
        except W.WidgetError as exc:
            unresolved = "cannot load hook" in str(exc)
            out.append(Finding(
                INFO if (structural and unresolved) else ERROR,
                key, "declare",
                (f"{exc} (hook checks need the agent repo importable)"
                 if structural and unresolved else str(exc))))
            continue

        # --- stage -------------------------------------------------------
        if not spec.stage_tools:
            out.append(Finding(
                WARN, key, "stage",
                "no stage.tools declared. Nothing writes this register, so it "
                "can only emit if something else stages it out of band."))
        for tool in spec.stage_tools:
            staged_by.setdefault(tool, []).append(key)
            if known_tools and tool not in known_tools:
                out.append(Finding(
                    ERROR, key, "stage",
                    f"stage tool '{tool}' is not in the catalog. A skill that "
                    f"declares it binds nothing, so the register never fills."))
            elif widget_tools and tool in known_tools and tool not in widget_tools:
                # Worth a nudge, not an error: emits_widget is inferred from
                # module source and over-reports per-module, so it can be wrong.
                out.append(Finding(
                    INFO, key, "stage",
                    f"catalog does not mark '{tool}' as widget-emitting. Fine if "
                    f"it stages through a helper; check if nothing renders."))

        # --- rerank ------------------------------------------------------
        if spec.rerank_tool:
            if known_tools and spec.rerank_tool not in known_tools:
                out.append(Finding(
                    ERROR, key, "rerank",
                    f"rerank tool '{spec.rerank_tool}' is not in the catalog."))
            if not spec.stage_tools:
                out.append(Finding(
                    WARN, key, "rerank",
                    f"'{spec.rerank_tool}' reorders a register nothing stages, "
                    f"so it can only ever reorder an empty list."))

        # --- contract ----------------------------------------------------
        if spec.resolve_role is not None and spec.role is None:
            out.append(Finding(
                ERROR, key, "contract",
                "resolve_role is set but role is not. Refinement is additive: "
                "with no static role the spec never enters contract resolution, "
                "so resolve_role is never consulted."))
        out.extend(_practice(spec))

        # --- emit structure -----------------------------------------------
        if spec.revive_cap and spec.revive_when is None:
            out.append(Finding(
                WARN, key, "emit",
                "revive_cap is set with no revive_when, so nothing revives and "
                "the cap is dead config."))
        if spec.revive_when is not None and not spec.revive_cap:
            out.append(Finding(
                WARN, key, "emit",
                "revive_when with no revive_cap is an uncapped sticky register. "
                "Each revived emit costs a real build, so this re-renders on "
                "every later turn until something clears the register."))
        # --- collisions --------------------------------------------------
        if spec.widget_id in seen_ids:
            out.append(Finding(
                ERROR, key, "emit",
                f"widget_id '{spec.widget_id}' is already used by "
                f"'{seen_ids[spec.widget_id]}'. The client keys off the id, so "
                f"one card would replace the other."))
        seen_ids[spec.widget_id] = key

        if spec.state_items in seen_registers:
            out.append(Finding(
                ERROR, key, "stage",
                f"register '{spec.state_items}' is shared with "
                f"'{seen_registers[spec.state_items]}'. Two specs draining one "
                f"register both emit from the same items."))
        seen_registers[spec.state_items] = key

    for spec in adopted_specs:
        out.extend(_practice(spec))

    # A tool staging into several registers is legal but worth naming: one call
    # then dirties several, and every one of them emits at the turn boundary.
    for tool, keys in staged_by.items():
        if len(keys) > 1:
            out.append(Finding(
                INFO, ", ".join(keys), "stage",
                f"'{tool}' stages into {len(keys)} registers, so one call emits "
                f"{len(keys)} widgets."))

    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda f: (order[f.level], f.spec))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Lint declared staged-widget specs.")
    ap.add_argument("agent", type=pathlib.Path, help="agent.yaml")
    ap.add_argument("--catalog", type=pathlib.Path, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--structural", action="store_true",
                    help="Check declarations without importing project hooks or registries.")
    args = ap.parse_args()

    doc = yaml.safe_load(args.agent.read_text(encoding="utf-8")) or {}

    # Structural checks stay credential-free and never import project hooks.
    # Runtime checks respect the same local/remote boundary as preview.
    if not args.structural:
        from adkl import execution, toolbind
        root = args.agent.resolve().parent
        try:
            execution.require_local(root)
            repo = (doc.get("tools") or {}).get("repo")
            if repo:
                toolbind.prepare(execution.inside(root, repo))
        except (execution.ExecutionError, toolbind.ToolBindError) as exc:
            finding = Finding(ERROR, "", "declare", str(exc))
            print(json.dumps([finding.to_json()]) if args.json else finding)
            return 1

    catalog = None
    if args.catalog and args.catalog.is_file():
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    elif (rel := (doc.get("tools") or {}).get("catalog")):
        p = args.agent.parent / rel
        if p.is_file():
            catalog = json.loads(p.read_text(encoding="utf-8"))

    findings = lint(doc, catalog, structural=args.structural)
    if args.json:
        print(json.dumps([f.to_json() for f in findings], indent=2))
    else:
        for f in findings:
            print(f)
        errors = sum(1 for f in findings if f.level == ERROR)
        warns = sum(1 for f in findings if f.level == WARN)
        print(f"{errors} error(s), {warns} warning(s)")
    return 1 if any(f.level == ERROR for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
