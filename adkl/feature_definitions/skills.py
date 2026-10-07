"""Skill selection and typed context for active skills."""

import math
import re

from .model import FeatureError, Kind, Spec, Stage


def validate_ranking(config):
    """Shared numeric policy for feature declarations and direct SDK retrieval."""
    for name in ("top_k", "total_cap", "max_activated_skills"):
        value = config[name]
        minimum = 0 if name == "top_k" else 1
        if type(value) is not int or value < minimum:
            raise FeatureError(f"{name} must be an integer of at least {minimum}")
    if config["total_cap"] < config["top_k"]:
        raise FeatureError("total_cap must be at least top_k; semantic matches would be truncated")
    for name, low, high in (("min_score", -1, 1),
                            ("auto_activate_min_score", -1, 1),
                            ("auto_activate_min_margin", 0, 2)):
        value = config[name]
        if (type(value) not in (int, float) or not low <= value <= high
                or not math.isfinite(value)):
            hint = " Set auto_activate: false to disable activation." if name == "auto_activate_min_score" else ""
            raise FeatureError(f"{name} must be finite and between {low} and {high}.{hint}")
    weight = config["query_history_weight"]
    if (type(weight) not in (int, float) or not 0 < weight < 1
            or not math.isfinite(weight)):
        raise FeatureError("query_history_weight must be between 0 and 1 exclusive; "
                           "set query_history: false to disable history")


def validate_retrieval(config):
    validate_ranking(config)
    minimum = config["min_skills_for_retrieval"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
        raise FeatureError("min_skills_for_retrieval must be a non-negative integer")
    dimensions = config["dimensions"]
    if type(dimensions) is not int or dimensions < 0:
        raise FeatureError("dimensions must be a non-negative integer")
    grace = config["pin_grace_turns"]
    if not isinstance(grace, int) or isinstance(grace, bool) or grace < 0:
        raise FeatureError("pin_grace_turns must be a non-negative integer")
    if any(not isinstance(n, str) or not n for n in config["auto_activate_exclude"]):
        raise FeatureError("auto_activate_exclude must contain skill names")
    pins = config["state_pins"]
    if not isinstance(pins, list):
        raise FeatureError("skill_retrieval.state_pins must be a list")
    for pin in pins:
        if (not isinstance(pin, dict) or set(pin) - {"state_key", "skill", "active_when"}
                or not all(isinstance(pin.get(k), str) and pin[k] for k in ("state_key", "skill"))):
            raise FeatureError("State pins require state_key and skill")
        if pin.get("active_when", "non_empty") not in ("non_empty", "truthy"):
            raise FeatureError("state_pins.active_when must be non_empty or truthy")
    # Enumerated modes, checked here rather than at the use site: two of the three
    # are read inside a callback, where a typo would fall through to the default
    # branch and look exactly like the mode the host asked for.
    for key, allowed in (("native_activation", ("replace", "merge", "own")),
                         ("skill_hook_position", ("last", "first")),
                         ("skill_catalog", ("tool", "prompt"))):
        if config[key] not in allowed:
            raise FeatureError(
                f"skill_retrieval.{key} must be one of {', '.join(allowed)}, "
                f"got {config[key]!r}")


def validate_skill_state(config):
    bindings = config["skills"]
    if not isinstance(bindings, dict):
        raise FeatureError("skill_state_injection.skills must be a mapping")
    for skill, fields in bindings.items():
        if not isinstance(skill, str) or not skill or not isinstance(fields, dict):
            raise FeatureError("Skill state bindings require a skill name and field mapping")
        for field, binding in fields.items():
            if (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field)
                    or not isinstance(binding, dict)
                    or set(binding) - {"from_state", "type", "required"}
                    or not isinstance(binding.get("from_state"), str) or not binding["from_state"]):
                raise FeatureError("Skill state fields require a name and from_state key")
            if binding.get("type", "any") not in ("any", "string", "number", "boolean", "object", "array"):
                raise FeatureError("Unknown skill state field type")
            if not isinstance(binding.get("required", False), bool):
                raise FeatureError("Skill state required must be a boolean")


def validate_scope(resolved, included) -> None:
    """Validate state references against the same skill set used by the runtime."""
    names = set(included)
    unknown = set(resolved.config["skill_state_injection"]["skills"]) - names
    if unknown:
        raise FeatureError(f"State bindings name unavailable skills: {sorted(unknown)}")
    cfg = resolved.config["skill_retrieval"]
    for pin in cfg["state_pins"]:
        if pin["skill"] not in names or pin["skill"] in cfg["excluded_skills"]:
            raise FeatureError(f"State pin names unavailable skill: {pin['skill']}")



SPECS = (
    Spec(
        name="skill_retrieval",
        support_status="implemented",
        support_detail=(
            "Selects skills in native ADK callbacks using current text, session-state "
            "pins and optional ambiguous-turn continuation. Native procedure loading, "
            "confident auto-activation and scoped tools share factory/host bindings. Embedding "
            "failure exposes eligible candidates without auto-activation. Optional "
            "query_history widens reachability from prior turns; it never changes "
            "ranking, scores or margin."
        ),
        validate=validate_retrieval,
        kind=Kind.TOOLSET,
        summary="Select skills per turn by embedding similarity, and bind only the "
                "matched skills' tools.",
        stage=Stage.EXPERIMENTAL,
        # Explicit opt-in while the API is experimental.
        default_on=False,
        writes=frozenset({"agent.tools"}),
        options={
            "vectors": ("path", "embeddings/skills.json",
                        "Precomputed skill-description vectors, built by "
                        "`adkl embed`. Retrieval matches the text these were built "
                        "from, so an edited description needs a rebuild."),
            "embedding_model": ("str", "text-embedding-005_standard-us-east4",
                                "Gateway embedding model. Region-pinned by default: "
                                "production measured a tighter tail than the bare "
                                "id. Local models are deliberately unsupported."),
            "dimensions": ("int", 0,
                           "Output width to request. 0 takes the model's native "
                           "width. Only Matryoshka models accept a value, and "
                           "changing it changes the vector SPACE, not just the "
                           "length -- regenerate the vectors."),
            "min_skills_for_retrieval": ("int", 5,
                "Below this eligible catalog size, skip retrieval (including custom "
                "selectors) and put all skill descriptions in the prompt. Exactly "
                "the cutoff still retrieves; 0 disables. No inferred activation."),
            "top_k": ("int", 8, "Embedding matches considered before backfill."),
            "total_cap": ("int", 12, "Candidate cap before explicit state pins; active pins may exceed it."),
            "min_score": ("float", 0.15, "Cosine floor for a match."),
            "lexical": ("bool", True, "Lexical-overlap backfill for terms embeddings miss; not BM25."),
            "bm25": ("bool", True, "Deprecated alias for lexical. Use lexical instead."),
            "auto_activate": ("bool", False,
                              "Inject the top match pre-LLM, saving a turn. Off by "
                              "default: a mis-injection is worse than an extra hop."),
            "auto_activate_min_score": ("float", 0.9,
                                        "Cosine score required to auto-inject."),
            "auto_activate_min_margin": ("float", 0.05,
                                         "Required gap over the runner-up."),
            "auto_activate_exclude": ("list", [], "Skills eligible for manual loading only."),
            "max_activated_skills": ("int", 3,
                                     "Maximum recent skills retained for ambiguous continuation. "
                                     "Read ONLY while continue_active is true; inert otherwise."),
            "continue_active": ("bool", False,
                                "Retain recently active skills on ambiguous follow-ups; a confident new topic clears unpinned skills. "
                                "Defaults FALSE, and it gates the retention block that reads "
                                "max_activated_skills and pin_grace_turns -- neither has any effect until this is true."),
            "pin_grace_turns": ("int", 5,
                                "Competing confident selections that may retain an active pinned skill. "
                                "0 disables pin retention; ambiguous turns do not consume this budget. "
                                "Requires continue_active: true; inert on its own. Retention is bounded "
                                "on purpose -- an enormous value is not a supported way to keep a skill "
                                "loaded, and misreports the agent's behaviour to whoever reads the config."),
            "query_history": ("bool", False,
                              "Blend prior turns into the retrieval query so a terse follow-up "
                              "(\"tell me more\", \"cheapest first\") keeps the skill the customer "
                              "is still discussing reachable. Costs no extra embedding call. "
                              "Widens the match set ONLY: scores, ranking and margin stay "
                              "current-turn, so this can never auto-activate a skill or "
                              "change which one wins. Off by default -- it changes which "
                              "skills are reachable, so calibrate it against real turns."),
            "query_history_weight": ("float", 0.5,
                                     "Weight of accumulated history against the current turn, "
                                     "exclusive of 0 and 1. Higher retains topic longer and "
                                     "dilutes the current turn more. Only read while "
                                     "query_history is true."),
            "state_pins": ("list", [],
                           "Keep configured skills reachable while a session state key is active."),
            "excluded_skills": ("list", [],
                                "Skills unavailable in this agent surface. Use this "
                                "for a role-specific skill such as a teacher-only "
                                "classroom coach, not as a relevance tuning knob."),
            "native_activation": ("str", "replace",
                                  "Who owns ADK's shared _adk_activated_skill_ "
                                  "state key. It has other writers -- ADK's own "
                                  "load_skill tool, which APPENDS to that list and "
                                  "never prunes it, and any host hook that rescues "
                                  "an evicted skill -- while this feature derives "
                                  "its value from THIS turn's ranking. replace "
                                  "(default, current behaviour) overwrites them "
                                  "every turn; merge keeps their entries after "
                                  "ours; own never writes the key and tracks our "
                                  "own activation privately. Prefer merge or own "
                                  "if anything else writes that key -- but note "
                                  "that under both, nothing prunes the key, so its "
                                  "growth is bounded only by what SELECTION lets "
                                  "through crossed with total_cap."),
            "skill_hook_position": ("str", "last",
                                    "Where this feature's before-model hook sits "
                                    "among the agent's before-model callbacks. "
                                    "last (default, current behaviour) puts it "
                                    "after the host's; first puts it before them, "
                                    "which is closer to native ADK -- there the "
                                    "skill instructions come from the toolset's "
                                    "process_llm_request, ahead of every agent "
                                    "callback. final_model_transforms cannot "
                                    "serve this: they are appended after it."),
            "skill_catalog": ("str", "tool",
                              "Where the skill catalogue reaches the model. tool "
                              "(default, current behaviour) exposes list_skills, "
                              "costing an extra model round trip before the model "
                              "can choose a skill. prompt drops that tool, which "
                              "is what makes ADK append the skills XML to the "
                              "instruction instead. Do not use tool_filter for "
                              "this -- it hides the tool without moving the "
                              "catalogue, leaving no way to discover a skill."),
        },
    ),
    Spec(
        name="skill_state_injection",
        support_status="implemented",
        support_detail=(
            "Projects typed declared state for active skills on each model call; "
            "required missing values block the call. State freshness remains the host "
            "provider's responsibility; SDK bindings accept a preparation hook."
        ),
        validate=validate_skill_state,
        kind=Kind.BEFORE_MODEL,
        summary="Inject typed, declared state only for active skills on each model call.",
        reads=frozenset({"session.state"}),
        options={
            "skills": ("dict", {}, "Skill name -> context fields with from_state, type and required."),
        },
    ),
)
