"""Public feature configuration API: declare, activate, validate and describe.

Built-ins live in feature_definitions/ by responsibility. This module owns only
registry and pipeline rules; it imports neither ADK nor project code. The public
Spec, Kind, Stage, FeatureError, REGISTRY and resolve/describe imports are stable.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field

from .feature_definitions.model import FeatureError, Kind, Spec, Stage
from .feature_definitions.skills import validate_scope as validate_skill_scope
from .feature_definitions import conversation, presentation, skills, tools


__all__ = [
    "FeatureError", "Kind", "Spec", "Stage", "Resolved", "REGISTRY",
    "register", "resolve", "defaults", "describe", "validate_skill_scope",
]

REGISTRY: dict[str, Spec] = {}


def register(spec: Spec) -> Spec:
    if spec.name in REGISTRY:
        raise FeatureError(f"feature {spec.name!r} already registered")
    REGISTRY[spec.name] = spec
    return spec


RETIRED = {
    "analytics": "Use host telemetry and ADKL observer hooks; no analytics plugin was implemented.",
    "identity_gate": "Keep authorization at the host's trusted identity/tool boundary.",
    "semantic_cache": "Use a host-owned cache with explicit identity, admission and delivery policy.",
}

for _family in (skills, conversation, tools, presentation):
    for _spec in _family.SPECS:
        register(_spec)


@dataclass
class Resolved:
    """A validated feature configuration, ready for the compiler or the UI."""

    active: list[str]                       # in pipeline order
    declared: list[str]                      # configured but not activated
    config: dict[str, dict]                  # name -> merged options
    warnings: list[str] = field(default_factory=list)

    def plugin_name(self, feature: str) -> str:
        """ADK requires unique plugin names, so include a config hash."""
        blob = json.dumps(self.config.get(feature, {}), sort_keys=True)
        return f"{feature}#{hashlib.sha1(blob.encode()).hexdigest()[:6]}"


def defaults(name: str) -> dict:
    """Registry defaults for one feature, as a fresh mutable dict.

    Exists so a wiring call site can never write its own literal fallback. Two
    already did: `int(cfg.get("recent_turns") or 5)` turned a deliberate 0 into
    5, and `int(cfg.get("max_chars") or 12000)` ran 12000 against a declared
    default of 4000. A literal there is invisible to `describe()`, so the console
    shows one number while the agent runs another -- and it only diverges for a
    caller that passes an unmerged config, which is the hardest case to notice.
    """
    return {k: deepcopy(v[1]) for k, v in REGISTRY[name].options.items()}


def resolve(features: dict | None) -> Resolved:
    """Validate a `features:` block. Raises FeatureError on anything that would
    misbehave silently; returns warnings for things that merely deserve a look.
    """
    features = dict(features or {})
    pipeline = features.pop("pipeline", None)
    if pipeline is None:
        pipeline = [n for n, s in REGISTRY.items() if s.default_on]
    if not isinstance(pipeline, list):
        raise FeatureError("features.pipeline must be a list of feature names")

    errors: list[str] = []
    warnings: list[str] = []

    retired = sorted(set(list(features) + list(pipeline)) & RETIRED.keys())
    if retired:
        raise FeatureError("Retired feature configuration: " + "; ".join(
            f"{name}: {RETIRED[name]}" for name in retired))
    if "progressive_auth" in features or "progressive_auth" in pipeline:
        warnings.append("progressive_auth is deprecated: it records attempts, not authorization; use a host observer.")
    if "output_cleanup" in features or "output_cleanup" in pipeline:
        warnings.append("output_cleanup's blanket text policy is deprecated; use explicit host output transforms.")
    if "short_circuit" in features or "short_circuit" in pipeline:
        warnings.append("short_circuit is a legacy exact-response recipe; host routes own predicates and widget transport.")
    unknown = [n for n in list(features) + list(pipeline) if n not in REGISTRY]
    if unknown:
        raise FeatureError(
            f"unknown feature(s): {', '.join(sorted(set(unknown)))}. "
            f"Available: {', '.join(sorted(REGISTRY))}")

    dupes = sorted({n for n in pipeline if pipeline.count(n) > 1})
    if dupes:
        raise FeatureError(f"features.pipeline lists {', '.join(dupes)} twice")

    # Merge declared config over defaults; reject unknown option keys.
    config: dict[str, dict] = {}
    for name, spec in REGISTRY.items():
        declared = features.get(name)
        if declared is None:
            declared = {}
        if not isinstance(declared, dict):
            raise FeatureError(f"features.{name} must be a mapping")
        bad = sorted(set(declared) - set(spec.options) - {"enabled"})
        if bad:
            raise FeatureError(
                f"features.{name}: unknown option(s) {', '.join(bad)}. "
                f"Valid: {', '.join(sorted(spec.options)) or '(none)'}")
        # The declared TYPE is checked here because no wiring call site checks
        # it: an option declared "int" arriving as "5" from an env var or a YAML
        # quirk reaches the callback factory unconverted, where it either raises
        # somewhere unrelated or -- worse -- coerces and runs. bool is excluded
        # from the numeric kinds on purpose: isinstance(True, int) is True, so
        # `recent_turns: true` would otherwise slice history to one turn.
        kinds = {"str": (str,), "path": (str,), "int": (int,),
                 "float": (int, float), "bool": (bool,), "list": (list,),
                 "dict": (dict,)}
        for key, value in declared.items():
            if key == "enabled":
                if not isinstance(value, bool):
                    raise FeatureError(
                        f"features.{name}.enabled must be a boolean; anything "
                        f"else is compared against False and silently activates "
                        f"the feature.")
                continue
            type_name = spec.options[key][0]
            expected = kinds.get(type_name)
            if expected is None:
                continue  # a family may name a type this form does not model
            if (not isinstance(value, expected)
                    or (type_name in ("int", "float") and isinstance(value, bool))):
                raise FeatureError(
                    f"features.{name}.{key} must be {type_name}, got "
                    f"{type(value).__name__} ({value!r})")
        merged = {k: deepcopy(v[1]) for k, v in spec.options.items()}
        merged.update(deepcopy({k: v for k, v in declared.items() if k != "enabled"}))
        if name == "skill_retrieval" and "bm25" in declared:
            warnings.append("skill_retrieval.bm25 is deprecated; use lexical (lexical overlap, not BM25).")
            if "lexical" in declared and declared["lexical"] != declared["bm25"]:
                raise FeatureError("skill_retrieval: bm25 and lexical disagree; remove the deprecated bm25 alias")
            merged["lexical"] = declared["bm25"]
        if name == "history_slicing" and "preserve_tool_pairs" in declared:
            warnings.append("history_slicing.preserve_tool_pairs is deprecated and inert; remove it. Turn boundaries preserve pairs.")
        config[name] = merged

    for name, spec in REGISTRY.items():
        if spec.validate is not None:
            spec.validate(config[name])

    # `enabled: false` is an ops kill-switch and wins over pipeline membership.
    active: list[str] = []
    for name in pipeline:
        if (features.get(name) or {}).get("enabled") is False:
            warnings.append(
                f"{name} is listed in features.pipeline but has enabled: false, "
                f"so it is INACTIVE. The kill-switch wins; remove one of them.")
            continue
        active.append(name)

    for name in active:
        spec = REGISTRY[name]
        if spec.stage is not Stage.STABLE:
            warnings.append(
                f"{name} is {spec.stage.value}: its options may change without "
                f"a major version bump.")

    # Some features are DATA policy, not tuning. For those, a pipeline entry is
    # not consent: naming the feature activates it on registry defaults, and a
    # default that silently drops rows from a tool result is not a safe starting
    # point -- it is an undeclared decision that surfaces to the customer as a
    # plausible answer built from less data. Reported as an error, not a warning,
    # and appended rather than raised early so it lands alongside any ordering
    # problem instead of hiding one.
    for name in active:
        spec = REGISTRY[name]
        missing = sorted(spec.requires_explicit_config - set(features.get(name) or {}))
        if missing:
            key = missing[0]
            errors.append(
                f"{name} is in features.pipeline but does not configure "
                f"{', '.join(missing)}.\n"
                f"    Why: this feature changes the DATA the model sees, so it "
                f"refuses to inherit a default -- see its option help.\n"
                f"    Fix: set features.{name}.{key} explicitly (the registry "
                f"default is {spec.options[key][1]!r}, and writing that same "
                f"value down is a valid answer), or remove {name} from "
                f"features.pipeline.")

    # An option the host wrote but a gate makes inert is worse than one it never
    # wrote: the config reads as configured and nothing contradicts it. A real
    # host shipped `pin_grace_turns` under a ten-line rationale while
    # `continue_active` was false, which skips the entire block that reads it.
    # Keyed off the DECLARED keys, never the merged config -- these options have
    # non-empty defaults, so warning on the merged value would fire on every
    # resolve and train readers to ignore warnings. `state_pins` is deliberately
    # NOT listed: it is also read by native state pinning, which is not gated.
    if "skill_retrieval" in active and not config["skill_retrieval"]["continue_active"]:
        inert = sorted(set(features.get("skill_retrieval") or {})
                       & {"max_activated_skills", "pin_grace_turns"})
        if inert:
            warnings.append(
                f"skill_retrieval: {', '.join(inert)} is configured but inert "
                f"while continue_active is false -- the retention block that "
                f"reads it is skipped entirely. Set continue_active: true if you "
                f"meant it to apply, or drop the option.")

    # --- conflict detection
    index = {n: i for i, n in enumerate(active)}

    for name in active:
        spec = REGISTRY[name]
        for dep in sorted(spec.must_run_after):
            if dep not in index:
                continue
            if index[dep] > index[name]:
                errors.append(
                    f"{name} must run after {dep}, but the pipeline lists it "
                    f"before.\n"
                    f"    Why: {name} reads "
                    f"{', '.join(sorted(spec.reads)) or 'shared state'} and "
                    f"{dep} writes "
                    f"{', '.join(sorted(REGISTRY[dep].writes)) or 'it'}. "
                    f"Running {name} first would key off data {dep} has not "
                    f"transformed yet -- which fails silently, not loudly.\n"
                    f"    Fix: reorder features.pipeline to "
                    f"[{', '.join(_reordered(active, name, dep))}]")

    # write-write on the same field with no declared order
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            sa, sb = REGISTRY[a], REGISTRY[b]
            clash = sa.writes & sb.writes
            if clash and b not in sa.must_run_after and a not in sb.must_run_after:
                errors.append(
                    f"{a} and {b} both write {', '.join(sorted(clash))} with no "
                    f"declared order between them. One will silently overwrite "
                    f"the other; give one a must_run_after.")

    # short-circuit shadowing: ADK stops the hook on the first non-None result
    for i, a in enumerate(active):
        if not REGISTRY[a].may_short_circuit:
            continue
        shadowed = [b for b in active[i + 1:] if REGISTRY[b].kind is REGISTRY[a].kind]
        also = f" {', '.join(shadowed)} will not run, and" if shadowed else ""
        warnings.append(
            f"{a} can short-circuit its hook. When it fires,{also} no "
            f"agent-level callback on that hook runs either -- an ADK plugin "
            f"short-circuit pre-empts agent callbacks "
            f"(base_llm_flow.py:270-292), so hand-written before_model logic is "
            f"silently skipped on a cache hit.")

    if errors:
        raise FeatureError("\n".join(errors))

    return Resolved(
        active=active,
        declared=sorted(set(features) - set(active)),
        config=config,
        warnings=warnings,
    )


def _reordered(active: list[str], name: str, dep: str) -> list[str]:
    out = [n for n in active if n != name]
    out.insert(out.index(dep) + 1, name)
    return out


def describe() -> list[dict]:
    """Registry as JSON, for the console's feature cards."""
    return [
        {
            "name": s.name,
            "kind": s.kind.value,
            "summary": s.summary,
            "stage": s.stage.value,
            "defaultOn": s.default_on,
            "mayShortCircuit": s.may_short_circuit,
            "reads": sorted(s.reads),
            "writes": sorted(s.writes),
            "mustRunAfter": sorted(s.must_run_after),
            # `requiresExplicitConfig` rides on the OPTION, not the feature, and
            # sits next to `default` on purpose: those two are contradictory for
            # such a key. A form that renders `default` as the current value is
            # showing a number that will be REJECTED at resolve() unless the host
            # writes it down, so the flag has to be visible wherever the default
            # is. It was previously unreachable from any consumer -- unlike
            # `support_status`, which the console gets via capabilities.manifest()
            # -- which made a validity rule invisible to the only UI that writes
            # config.
            "options": [
                {"key": k, "type": t, "default": deepcopy(d), "help": h,
                 "requiresExplicitConfig": k in s.requires_explicit_config}
                for k, (t, d, h) in sorted(s.options.items())
            ],
        }
        for s in sorted(REGISTRY.values(), key=lambda x: x.name)
    ]
