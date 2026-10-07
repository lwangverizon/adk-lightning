#!/usr/bin/env python3
"""adkl agent.yaml — schema + validator.

Fills the gap verified in ADK itself: `LlmAgentConfig` has fields for
`instruction`, `tools`, callbacks and `generate_content_config`, but **no
`skills` field**. ADK ships a skills package and a YAML agent config and they
do not connect. This schema is that join.

Design rules carried in from the research:

* Text on disk is canonical. This file and the SKILL.md tree are the source of
  truth; the console reads and writes them and validates against the *text*.
  If the console can express something this validator rejects, that is a
  one-way door and a bug in the console.

* Explicit skill list, not globs. A new directory must not silently join an
  agent -- that is the config drift the linter exists to catch.

* Environment overrides are first-class, because skills/uat/ already does this
  by name-swap in Python. A skill can pass lint in prod and fail in uat, so
  validation is per-environment.

* Every agent declares an eject target. AWS's Bedrock Agents Classic post-mortem
  is the lesson: config with no ladder out is the dead end. `eject.target` is
  required so the escape hatch is exercised, not aspirational.

Stdlib + PyYAML only, so it runs in CI without the ADK wheel or GCP creds.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

# Importable as a package (`from adkl import agent_schema`) and runnable as a
# script (`python3 adkl/agent_schema.py`). The latter is how CI and the README
# invoke it, and a bare `from adkl import ...` fails in that mode.
if __package__:
    from . import features as features_mod
    from . import gateway as gateway_mod
    from . import instruction as instruction_mod
else:  # pragma: no cover -- script mode
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from adkl import features as features_mod
    from adkl import gateway as gateway_mod
    from adkl import instruction as instruction_mod

SCHEMA_VERSION = 1

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
# ADK's own rule, from _is_valid_state_name: a bare identifier, or one of the
# three scope prefixes plus an identifier. Anything else is returned VERBATIM
# with no error -- so `{my-key}` fails silently at runtime. Reject it here.
_STATE_KEY = re.compile(r"^(app:|user:|temp:)?[A-Za-z_][A-Za-z0-9_]*$")
# A trailing '?' marks the placeholder optional: instructions_utils.py:136 strips
# it and, when the key is absent from state, substitutes empty instead of
# raising. Optional placeholders such as {pageName?} must remain valid even
# when their keys are absent from the initial state.
_OPTIONAL_SUFFIX = "?"
# Build-time slot vs runtime state key: UPPER_SNAKE vs lower_snake.
_SLOT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CALLBACK_REF = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_CALLBACK_KINDS = {
    "before_agent_callback", "after_agent_callback", "before_model_callback",
    "after_model_callback", "before_tool_callback", "after_tool_callback",
    "on_model_error_callback", "on_tool_error_callback",
}

TEMPLATE = """\
# adkl agent definition. Canonical source of truth -- the console edits this file.
version: 1

agent:
  name: example_agent
  model: gemini-3-flash
  # Public Gemini API through LiteLLM; provision GOOGLE_API_KEY locally.
  # Existing configs that omit provider retain the legacy gateway default.
  # To use an OpenAI-compatible gateway, set provider: gateway explicitly
  # and provision its URL/key through the project's execution profile.
  provider: gemini
  # One template plus named slots. The compiler emits an InstructionProvider.
  # NOTE: a callable instruction sets bypass_state_injection=True in ADK, so the
  # generated provider performs {state_key} interpolation itself.
  instruction:
    template: identity.md
    slots:
      RESPONSE_STANDARDS:            # per-surface variants of one block
        variants:
          web: response_standard.md
          mobile: response_standard.md
        select: web
      SAFETY: safety.md              # simple form: SLOT_NAME -> path
  # Optional, orthogonal to behavior: how it speaks. Per-locale, A/B-able.
  persona:
    default: neutral-concise
    locales:
      es-US: neutral-concise-es

skills:
  dir: skills
  include:
    - prospect-device-exploration
    - prospect-offer-exploration
    - prospect-cart-management
    - safety-and-refusal
  # Mirrors skills/uat/ name-swapping done in Python today.
  overrides:
    uat:
      customer-billing-explanation: uat/customer-billing-explanation

retrieval:
  embedding_model: text-embedding-005
  top_k: 8
  total_cap: 12
  min_score: 0.15
  # Score at/above which the top match is injected pre-LLM, saving a turn.
  auto_activate_min_score: 0.9
  auto_activate_min_margin: 0.05
  lexical: true
  keyword_table: keyword_tables/prospect.yaml

# Keeps an in-progress flow's skill in the match set on terse mid-flow turns
# ("the iPhone", "$35"), which would otherwise drop its tools and break the flow.
state_pins:
  - state_key: passwordless_transaction_id
    skill: passwordless-auth
    active_when: non_empty
  - state_key: live_agent_step
    skill: customer-live-agent-request
    active_when: { in: [CATEGORY_SHOWN, SUBCATEGORY_SHOWN] }

tools:
  catalog: catalogs/prospect.json   # generated; the console's tool picker reads this
  core:                             # always bound, regardless of skill match
    - search_products

# Required. Config must always have a ladder out.
eject:
  target: generated/prospect_agent.py
"""

_ACTIVE_WHEN = {"non_empty", "truthy"}
# ADK ThinkingLevel values.
_THINKING_LEVELS = {"NONE", "LOW", "MEDIUM", "HIGH"}


def _err(errors: list[str], where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


# Every block the library reads, and which module reads it. A block absent from
# here is config nothing consumes -- see `_check_unknown_blocks`.
#
# Kept as data rather than a comment because the check below reads it: a new block
# has to be added here to be accepted. Runtime support is checked separately
# through the capability manifest; recognition does not imply execution.
KNOWN_BLOCKS: dict[str, str] = {
    "authoring": "native_authoring",
    "version": "agent_schema",
    "agent": "chat, eject, explain",
    "skills": "chat, embed, eject",
    "retrieval": "eject, server (legacy: features.skill_retrieval supersedes it)",
    "features": "chat, runtime, eject",
    "tools": "chat, toolbind, explain",
    "widgets": "widgets, widget_lint",
    "presentation": "presentation, chat",
    "eject": "eject",
    "state_pins": "agent_schema (legacy declaration validation)",
    "artifacts": "agent_schema, instruction",
}


def _check_unknown_blocks(doc: dict, errors: list, warnings: list) -> None:
    """Reject a top-level block no module reads.

    This closes a class of bug rather than an instance. Nothing validated unknown
    keys, so a typo (`feature:` for `features:`) or an aspirational block
    (`language:` before the feature exists) was silently ignored -- and every
    layer would report the agent as valid. That is the same shape as the bug where
    four activated features ran as no-ops while config, registry, API and console
    all confirmed they were on.

    An ERROR rather than a warning: silently ignored config is worse than a
    refusal, because the author believes it took effect.
    """
    for key in doc:
        if key in KNOWN_BLOCKS:
            continue
        if not isinstance(key, str):
            _err(errors, repr(key), "top-level block name must be a string")
            continue
        near = [k for k in KNOWN_BLOCKS if k.startswith(key[:4])
                or key.startswith(k[:4])]
        hint = f" Did you mean {near[0]!r}?" if near else ""
        _err(errors, key,
             f"unknown top-level block -- no module reads it, so everything under "
             f"it is silently ignored.{hint} Known: "
             f"{', '.join(sorted(KNOWN_BLOCKS))}")


def validate(doc: dict, root: pathlib.Path, env: str | None = None):
    """Return (errors, warnings). Errors block; warnings inform."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(doc, dict):
        return ["document root is not a mapping"], []

    _check_unknown_blocks(doc, errors, warnings)
    if isinstance(doc.get("agent"), dict) and "factory" in doc["agent"]:
        agent = doc["agent"]
        if doc.get("version") != 1 or not _IDENT.fullmatch(str(agent.get("name", ""))):
            errors.append("Host project needs version: 1 and a lower_snake_case agent.name")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", str(agent["factory"])):
            errors.append("agent.factory must be module:callable")
        if set(agent) - {"name", "factory"}:
            errors.append("Host factory owns its model, instructions and callbacks; configure them in Python")
        if set(doc) - {"version", "agent", "authoring"}:
            errors.append("Host factory owns runtime configuration; the descriptor accepts version, agent and authoring")
        authoring = doc.get("authoring", {})
        if not isinstance(authoring, dict) or set(authoring) - {"skills"} or not isinstance(authoring.get("skills", {}), dict):
            errors.append("authoring must contain a skills source mapping")
        else:
            from adkl.native_authoring import source_path
            for name, value in authoring.get("skills", {}).items():
                try:
                    if not isinstance(name, str) or not _SKILL_NAME.fullmatch(name):
                        raise ValueError("Invalid authoring skill name")
                    source_path(root, value)
                except ValueError as exc:
                    errors.append(str(exc))
        return errors, ["Host factory inspection uses disposable local sessions; host behavior is not certified."]
    if "authoring" in doc:
        errors.append("authoring source mappings require a native host factory")
    from adkl import presentation
    try:
        presentation.configuration(doc)
        if doc.get("presentation"):
            import json
            catalog_path = (doc.get("tools") or {}).get("catalog")
            catalog = {}
            if isinstance(catalog_path, str) and (root / catalog_path).is_file():
                catalog = json.loads((root / catalog_path).read_text(encoding="utf-8"))
            presentation.extend_catalog(catalog, doc)
    except (ValueError, features_mod.FeatureError) as exc:
        _err(errors, "presentation", str(exc))

    if (root / "execution.yaml").is_file():
        from adkl import execution
        try:
            execution.profiles(root)
        except (execution.ExecutionError, yaml.YAMLError) as exc:
            _err(errors, "execution", str(exc))

    v = doc.get("version")
    if v != SCHEMA_VERSION:
        _err(errors, "version", f"expected {SCHEMA_VERSION}, got {v!r}")

    # ---- agent
    agent = doc.get("agent") or {}
    if not isinstance(agent, dict):
        _err(errors, "agent", "must be a mapping")
        agent = {}
    name = agent.get("name")
    if not name:
        _err(errors, "agent.name", "required")
    elif not _IDENT.match(str(name)):
        _err(errors, "agent.name", f"{name!r} must be a lower_snake_case identifier")
    if not agent.get("model"):
        _err(errors, "agent.model", "required")

    # Which backend serves that model. Optional and defaulted, because every
    # agent that existed before this field was a gateway agent. Validated
    # against the registry rather than a literal list so the two cannot drift.
    provider = agent.get("provider")
    if provider is not None and provider not in gateway_mod.PROVIDERS:
        _err(errors, "agent.provider",
             f"{provider!r} is not a known provider; expected one of: "
             f"{', '.join(sorted(gateway_mod.PROVIDERS))}")

    callbacks = agent.get("callbacks") or {}
    if not isinstance(callbacks, dict):
        _err(errors, "agent.callbacks", "must be a mapping of ADK callback kind -> refs")
    else:
        for kind, refs in callbacks.items():
            at = f"agent.callbacks.{kind}"
            if kind not in _CALLBACK_KINDS:
                _err(errors, at, "is not a supported ADK callback kind")
                continue
            if not isinstance(refs, list) or not refs:
                _err(errors, at, "must be a non-empty list of module.symbol references")
                continue
            for ref in refs:
                if not isinstance(ref, str) or not _CALLBACK_REF.match(ref):
                    _err(errors, at, f"{ref!r} must be a dotted module.symbol reference")

    # ---- instruction: one template plus named slots.
    #
    # Was `layers: [a.md, b.md]` concatenated. Changed after reading the real
    # prospect prompt: it is a template with two NAMED slots
    # ({SIMPLICITY_SOURCE_OF_TRUTH}, {RESPONSE_STANDARDS}) substituted by
    # position, not a list joined by order. Concatenation cannot express that.
    instruction = agent.get("instruction") or {}
    template = instruction.get("template")
    if not template:
        _err(errors, "agent.instruction.template", "required (path to the prompt template)")
    elif not (root / template).is_file():
        _err(errors, "agent.instruction.template", f"missing file: {template}")

    slots = instruction.get("slots") or {}
    if not isinstance(slots, dict):
        _err(errors, "agent.instruction.slots", "must be a mapping of SLOT_NAME -> spec")
        slots = {}

    for slot_name, spec in slots.items():
        at = f"agent.instruction.slots.{slot_name}"
        if not _SLOT_NAME.match(str(slot_name)):
            _err(errors, at, f"{slot_name!r} must be UPPER_SNAKE_CASE; lower_snake names "
                             "are runtime state keys, not build-time slots")
        if isinstance(spec, str):
            if not (root / spec).is_file():
                _err(errors, at, f"missing file: {spec}")
            continue
        if not isinstance(spec, dict):
            _err(errors, at, "must be a path string or a mapping with `variants`/`select`")
            continue
        variants = spec.get("variants") or {}
        if not variants:
            _err(errors, at, "mapping form requires `variants`")
            continue
        select = spec.get("select")
        if select is None:
            _err(errors, at, f"declares variants ({', '.join(sorted(variants))}) "
                             "but no `select`")
        elif select not in variants:
            _err(errors, at, f"selects {select!r}, not one of: "
                             f"{', '.join(sorted(variants))}")
        for vname, vpath in variants.items():
            if not (root / vpath).is_file():
                _err(errors, f"{at}.variants.{vname}", f"missing file: {vpath}")

    # Compile for real. This is the only way to catch an unfilled slot, a slot
    # declared but never referenced, or a placeholder ADK would emit verbatim --
    # all three are silent at runtime.
    if template and (root / template).is_file() and not errors:
        try:
            slot_objs = []
            for slot_name, spec in slots.items():
                if isinstance(spec, str):
                    slot_objs.append(instruction_mod.Slot(slot_name, pathlib.Path(spec)))
                else:
                    slot_objs.append(instruction_mod.Slot(
                        slot_name,
                        variants={k: pathlib.Path(v)
                                  for k, v in (spec.get("variants") or {}).items()},
                        select=spec.get("select")))
            compiled = instruction_mod.compile_instruction(
                root / template, root, slot_objs)
            for ph in compiled.passthrough:
                _err(errors, str(template),
                     f"placeholder {{{ph}}} is not a valid ADK state name; it will "
                     "be emitted to the model verbatim with no error")
            if compiled.artifact_keys and not doc.get("artifacts"):
                warnings.append(
                    f"instruction reads artifact(s) "
                    f"{', '.join(sorted(compiled.artifact_keys))} but no artifact "
                    "service is configured; a required artifact will raise at runtime")
        except (instruction_mod.SlotError, instruction_mod.TemplateError) as exc:
            _err(errors, "agent.instruction", str(exc))

    # ---- skills
    skills = doc.get("skills") or {}
    skills_dir = root / (skills.get("dir") or "skills")
    include = skills.get("include") or []
    if not isinstance(include, list) or not include:
        _err(errors, "skills.include", "must be a non-empty list of skill names")
        include = []
    if len(set(include)) != len(include):
        dupes = sorted({s for s in include if include.count(s) > 1})
        _err(errors, "skills.include", f"duplicate entries: {', '.join(dupes)}")

    overrides = (skills.get("overrides") or {})
    active = dict.fromkeys(include)
    if env:
        for logical, replacement in (overrides.get(env) or {}).items():
            if logical not in active:
                warnings.append(f"skills.overrides.{env}: {logical!r} is not in "
                                "skills.include; override has no effect")
            else:
                active[logical] = replacement

    for logical, replacement in active.items():
        rel = replacement or logical
        if not _SKILL_NAME.match(logical):
            _err(errors, "skills.include", f"{logical!r} must be lower-kebab-case")
        if not (skills_dir / rel / "SKILL.md").is_file():
            _err(errors, "skills", f"{logical!r} -> {rel}/SKILL.md not found")

    # ---- retrieval
    r = doc.get("retrieval") or {}
    top_k, cap = r.get("top_k"), r.get("total_cap")
    for key, lo, hi in (("top_k", 1, 64), ("total_cap", 1, 64)):
        val = r.get(key)
        if val is not None and not (isinstance(val, int) and lo <= val <= hi):
            _err(errors, f"retrieval.{key}", f"must be an int in [{lo},{hi}], got {val!r}")
    if isinstance(top_k, int) and isinstance(cap, int) and cap < top_k:
        _err(errors, "retrieval.total_cap",
             f"({cap}) is below top_k ({top_k}); semantic matches would be truncated")

    for key in ("min_score", "auto_activate_min_score", "auto_activate_min_margin"):
        val = r.get(key)
        if val is not None and not (isinstance(val, (int, float)) and 0.0 <= val <= 1.0):
            _err(errors, f"retrieval.{key}",
                 f"must be a cosine score in [0,1], got {val!r}")
    aa = r.get("auto_activate_min_score")
    if isinstance(aa, (int, float)) and aa >= 1.0:
        warnings.append("retrieval.auto_activate_min_score >= 1.0 is unreachable for cosine "
                        "similarity; auto-activation is effectively disabled (observe-only)")
    mn = r.get("min_score")
    if isinstance(aa, (int, float)) and isinstance(mn, (int, float)) and aa < mn:
        _err(errors, "retrieval.auto_activate_min_score",
             f"({aa}) below min_score ({mn}); a skill could auto-activate "
             "without qualifying as a match")
    kt = r.get("keyword_table")
    if kt and not (root / kt).is_file():
        _err(errors, "retrieval.keyword_table", f"missing file: {kt}")
    excluded = r.get("excluded_skills") or []
    if not isinstance(excluded, list) or not all(isinstance(s, str) for s in excluded):
        _err(errors, "retrieval.excluded_skills", "must be a list of skill names")
    else:
        unknown_excluded = sorted(set(excluded) - set(active))
        if unknown_excluded:
            _err(errors, "retrieval.excluded_skills",
                 f"{', '.join(unknown_excluded)} is not in skills.include")

    # ---- state pins
    for i, pin in enumerate(doc.get("state_pins") or []):
        at = f"state_pins[{i}]"
        if not isinstance(pin, dict):
            _err(errors, at, "must be a mapping")
            continue
        sk = pin.get("state_key")
        if not sk:
            _err(errors, f"{at}.state_key", "required")
        elif not _STATE_KEY.match(str(sk)):
            _err(errors, f"{at}.state_key",
                 f"{sk!r} is not a valid ADK state name (identifier, optionally "
                 "prefixed app:/user:/temp:)")
        target = pin.get("skill")
        if not target:
            _err(errors, f"{at}.skill", "required")
        elif target not in active:
            _err(errors, f"{at}.skill",
                 f"{target!r} is pinned but not in skills.include; the pin can never fire")
        aw = pin.get("active_when")
        if isinstance(aw, str):
            if aw not in _ACTIVE_WHEN:
                _err(errors, f"{at}.active_when",
                     f"{aw!r} not one of {sorted(_ACTIVE_WHEN)} or {{in: [...]}}")
        elif isinstance(aw, dict):
            vals = aw.get("in")
            if not isinstance(vals, list) or not vals:
                _err(errors, f"{at}.active_when", "{in: [...]} requires a non-empty list")
        elif aw is not None:
            _err(errors, f"{at}.active_when", "must be a string or {in: [...]}")

    # ---- tools
    t = doc.get("tools") or {}
    cat = t.get("catalog")
    if not cat:
        warnings.append("tools.catalog is unset; the console tool picker has no source and "
                        "TOOL_UNRESOLVED cannot be checked")
    elif not (root / cat).is_file():
        _err(errors, "tools.catalog", f"missing file: {cat} (run `adkl catalog`)")

    # ---- generation: planner and output budget.
    #
    # The real prospect agent sets BuiltInPlanner(thinking_level=LOW) and
    # max_output_tokens=8192. The floor matters: a thinking model spends the
    # budget on internal reasoning and returns EMPTY CONTENT without one, which
    # looks like a broken agent rather than a misconfigured budget.
    gen = agent.get("generation") or {}
    if gen:
        if not isinstance(gen, dict):
            _err(errors, "agent.generation", "must be a mapping")
            gen = {}
        level = gen.get("thinking_level")
        if level is not None and str(level).upper() not in _THINKING_LEVELS:
            _err(errors, "agent.generation.thinking_level",
                 f"{level!r} not one of: {', '.join(sorted(_THINKING_LEVELS))}")
        budget = gen.get("max_output_tokens")
        if budget is not None:
            if not isinstance(budget, int) or budget <= 0:
                _err(errors, "agent.generation.max_output_tokens",
                     f"must be a positive int, got {budget!r}")
            elif level and str(level).upper() != "NONE" and budget < 2048:
                warnings.append(
                    f"agent.generation.max_output_tokens={budget} with "
                    f"thinking_level={level}: a thinking model can spend the "
                    f"whole budget on internal reasoning and return empty "
                    f"content. The production agent uses 8192.")
        it = gen.get("include_thoughts")
        if it is True:
            warnings.append(
                "agent.generation.include_thoughts is on, which streams the "
                "model's reasoning to the caller. Useful in a console, usually "
                "wrong in production.")

    # ---- features: declare-then-activate, validated by the registry so a
    # misordered pipeline fails here rather than degrading silently in prod.
    try:
        resolved = features_mod.resolve(doc.get("features"))
        features_mod.validate_skill_scope(resolved, (doc.get("skills") or {}).get("include") or [])
        warnings.extend(resolved.warnings)
    except features_mod.FeatureError as exc:
        for line in str(exc).split("\n"):
            if line.strip():
                _err(errors, "features", line)

    # ---- eject
    ej = doc.get("eject") or {}
    if not ej.get("target"):
        _err(errors, "eject.target", "required — config must always have a ladder out "
                                     "to generated code")
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate an adkl agent.yaml.")
    ap.add_argument("path", type=pathlib.Path, nargs="?")
    ap.add_argument("--env", default=None, help="apply skills.overrides.<env> before validating")
    ap.add_argument("--print-template", action="store_true")
    args = ap.parse_args()

    if args.print_template:
        print(TEMPLATE)
        return 0
    if not args.path:
        ap.error("path required (or use --print-template)")

    try:
        doc = yaml.safe_load(args.path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"invalid YAML: {exc}")
        return 1

    errors, warnings = validate(doc, args.path.parent, args.env)
    label = f"{args.path}" + (f" [env={args.env}]" if args.env else "")
    for w in warnings:
        print(f"  WARN  {w}")
    for e in errors:
        print(f"  ERROR {e}")
    print(f"\n{label}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
