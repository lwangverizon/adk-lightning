#!/usr/bin/env python3
"""Skill retrieval: choose skills per turn, and bind only their tools.

This is the mechanism adk-lightning exists to generalize. ADK ships a skills
package but leaves selection to the model -- `SearchSkillsTool` is a tool the LLM
decides to call, which costs a turn. Here retrieval runs *before* the model call,
so a confident match is available immediately.

## Shared mechanisms

Application-specific business gates and rollout decisions remain host-owned.
This module provides:

  * semantic match over precomputed skill-description embeddings
  * lexical backfill, because embeddings miss exact product names
  * **state pinning** -- keep an in-progress flow's skill in the match set even
    when a terse turn ("the iPhone", "$35") would not retrieve it. Without this,
    the flow's tools unbind mid-conversation and a multi-turn task breaks.
  * **enforced binding** -- a skill outside the match set cannot be loaded. ADK
    parses `allowed_tools` but never enforces it; this is the piece no other
    framework ships.
  * optional pre-LLM auto-activation, off by default

## Two deliberate divergences

**One embedding backend per agent.** Mixing embedding backends can change both
vector dimensionality and embedding space. Here the backend is fixed by `agent.provider` (`adkl.gateway.PROVIDERS`), it is stamped into the
vector file, and `check_vectors` refuses a file built by a different one. So
several agents can use different backends while no single agent can be ambiguous
about which space it is in.

**Auto-activation is a boolean.** An explicit switch is easier to review than
an unreachable score sentinel.

## Degradation

Embedding calls fail. When they do, retrieval falls back to exposing every skill
rather than raising: selection is a ranking optimization, and a failed ranking
should cost tokens, not the conversation. Backend exceptions need not subclass
`TimeoutError`, so a narrow handler would let other failures escape.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence

logger = logging.getLogger("adkl.retrieval")

# State key ADK uses to track which skills are active in a session.
ACTIVATED_KEY = "_adk_activated_skill_{agent}"
# Rolling query-context vector for `query_history`. Session state, not process
# memory: it is per-conversation, and an instance-level cache would leak one
# customer's topic into another's ranking. Agent-scoped for the usual reason --
# two agents in one native tree share a session and must not blend queries.
QUERY_HISTORY_KEY = "adkl:query_history:{agent}"


class EmbeddingService(Protocol):
    """Anything that turns text into a vector. One method, so a fake is trivial."""

    async def embed(self, text: str) -> Sequence[float]: ...


@dataclass(frozen=True)
class Match:
    skill: str
    score: float
    source: str          # embedding | lexical | pinned


@dataclass
class MatchResult:
    matches: list[Match]
    fallback: bool = False       # True when ranking failed and all skills exposed
    auto_activated: str | None = None
    ranking_margin: float | None = None
    # Names a SELECTOR declares pin-protected this turn, independent of each
    # Match's own `source`. Exists because a selector that already ranked a
    # pinned skill (source "embedding"/"lexical") cannot ALSO list it with
    # source "pinned" -- a duplicate name in `matches` trips `prepare`'s own
    # dedup check and kills the turn -- so a ranked-and-pinned skill can only
    # be represented once, as its ranked entry. Without this field that
    # representation loses its pin status: `prepare`'s eviction test reads
    # `Match.source == "pinned"` only, so being ranked makes a skill MORE
    # likely to be evicted than never being ranked at all. Additive and
    # defaulted empty; the built-in `SkillRetrieval` tags its own pins with
    # source "pinned" directly (never both ranked and pinned at once) and
    # never needs this.
    pinned: frozenset = field(default_factory=frozenset)

    @property
    def names(self) -> list[str]:
        return [m.skill for m in self.matches]

    @property
    def margin(self) -> float:
        """Gap between the top two scores. Low margin means an ambiguous turn."""
        if self.ranking_margin is not None:
            return self.ranking_margin
        if len(self.matches) < 2:
            return 1.0
        return self.matches[0].score - self.matches[1].score


@dataclass(frozen=True)
class StatePin:
    """Keep a skill available while a durable flow is in progress.

    Per-turn re-selection drops an in-progress skill on terse mid-flow turns,
    which unbinds its tools and breaks the flow. A pin is additive and scored
    0.0, so it can never win auto-activation -- it only keeps the skill reachable.
    """

    state_key: str
    skill: str
    active_when: str | tuple = "non_empty"   # non_empty | truthy | ("in", [...])

    def fires(self, value: Any) -> bool:
        if isinstance(self.active_when, tuple):
            kind, values = self.active_when
            if kind == "in":
                return value in values
            return False
        if self.active_when == "truthy":
            return bool(value)
        return value not in (None, "", [], {})


@dataclass
class RetrievalConfig:
    """Mirrors the `features.skill_retrieval` block in agent.yaml."""

    embedding_model: str = "text-embedding-004"
    dimensions: int | None = None
    min_skills_for_retrieval: int = 5
    top_k: int = 8
    total_cap: int = 12
    min_score: float = 0.15
    lexical: bool = True
    timeout_seconds: float = 5.0
    auto_activate: bool = False
    auto_activate_min_score: float = 0.9
    auto_activate_min_margin: float = 0.05
    auto_activate_exclude: frozenset = frozenset()
    excluded_skills: frozenset = frozenset()
    max_activated_skills: int = 3
    state_pins: tuple = ()
    query_history: bool = False
    query_history_weight: float = 0.5

    def __post_init__(self):
        if (isinstance(self.min_skills_for_retrieval, bool)
                or not isinstance(self.min_skills_for_retrieval, int)
                or self.min_skills_for_retrieval < 0):
            raise ValueError("min_skills_for_retrieval must be a non-negative integer")
        from adkl.feature_definitions.skills import validate_ranking
        validate_ranking(vars(self))
        if (self.dimensions is not None and
                (type(self.dimensions) is not int or self.dimensions < 0)):
            raise ValueError("dimensions must be a non-negative integer or None")
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be positive and finite")


def small_catalog_result(names: Iterable[str], minimum: int) -> MatchResult | None:
    """Expose every eligible description below the cutoff, without ranking.

    Shared by native bindings (including custom selectors) and direct retrieval.
    Zero disables the shortcut; exactly the cutoff still retrieves. Callers pass
    the FULL eligible catalog, never a previously ranked slice. This is not a
    fallback: source='catalog' and margin=0 prevent inferred auto-activation.
    """
    names = sorted(set(names))
    if len(names) >= minimum:
        return None
    return MatchResult([Match(name, 0.0, "catalog") for name in names],
                       ranking_margin=0.0)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 on a length mismatch, which is why callers
    must check dimensions explicitly -- a silent 0.0 looks like 'no match'."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""a an the to for of and or with use this when user asks about
is are it its on in at be by from as that if then not do does can could would
you your our their they them my we us""".split())


def _tokens(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


class SkillRetrieval:
    """Per-turn skill selection with enforced tool binding.

    Usage:
        r = SkillRetrieval(config, vectors, embedder)
        result = await r.match(user_text, state)
        tools = r.bind_tools(result, state, declared_tools_by_skill, catalog)
    """

    # A CAPABILITY, not a class check. Callers pass `scope=` only when the
    # selector advertises it, so a host selector written against the two-argument
    # `match(text, state)` protocol keeps working untouched. Sniffing for a
    # concrete class instead is how a host silently disabled this same mechanism
    # for its own subclassed matchers.
    accepts_scope = True

    def __init__(
        self,
        config: RetrievalConfig,
        vectors: dict[str, Sequence[float]],
        embedder: EmbeddingService,
        descriptions: dict[str, str] | None = None,
    ):
        self.config = config
        self.vectors = dict(vectors)
        self.embedder = embedder
        # Lexical backfill needs the raw text; without it, `lexical` is a no-op.
        self.descriptions = dict(descriptions or {})
        self._warned: set[str] = set()

        if config.dimensions and self.vectors:
            got = len(next(iter(self.vectors.values())))
            if got != config.dimensions:
                raise ValueError(
                    f"vector file has {got}-dimensional embeddings but config "
                    f"declares dimensions={config.dimensions}. Cosine similarity "
                    f"returns 0.0 on a length mismatch rather than failing, so "
                    f"this would silently disable retrieval. Rebuild the vectors "
                    f"for {config.embedding_model!r}.")

    # ------------------------------------------------------------------ match

    def _roll_query_history(self, query, state, scope):
        """Advance the rolling query vector; return the blend, or None if off.

        An exponential moving average over ONE stored vector, so the cost is
        fixed: no extra embedding call, no growing state, and older turns decay
        geometrically. Returns None on the first turn of a conversation, since a
        blend of one turn is just that turn and reporting it as history would be
        a lie in the diagnostics.

        The write is NOT wrapped in a bare except. A host implementation of this
        exact mechanism swallowed `TypeError` from a read-only state mapping, and
        because the write was the state key's only writer, the average could
        never accumulate and the feature silently did nothing in production for
        the entire time it was configured on. A failed write here means the
        feature is off, so it says so, once per instance.
        """
        if not self.config.query_history:
            return None
        if scope is None:
            # No agent scope means no safe key: two agents in one tree share the
            # session, and blending their queries together is worse than not
            # blending at all. Callers that cannot scope get the feature off.
            self._warn_once("query_history needs an agent scope; not blending")
            return None
        key = QUERY_HISTORY_KEY.format(agent=scope)
        stored = state.get(key)
        blended = query
        if isinstance(stored, list) and len(stored) == len(query):
            weight = self.config.query_history_weight
            blended = [weight * p + (1.0 - weight) * c
                       for p, c in zip(stored, query)]
        try:
            state[key] = list(blended)
        except (TypeError, AttributeError):
            self._warn_once(
                f"query_history could not write {key} (read-only state); the "
                "rolling average cannot accumulate, so ranking uses the current "
                "turn only")
            return None
        return None if blended is query else blended

    def _warn_once(self, message: str) -> None:
        if message not in self._warned:
            self._warned.add(message)
            logger.warning("%s", message)

    async def match(self, user_text: str, state: dict | None = None,
                    *, scope: str | None = None) -> MatchResult:
        # `state or {}` would substitute a throwaway dict for an EMPTY caller
        # mapping, which is exactly what a session looks like on its first turn.
        # Reads did not care; `query_history` writes back, so under `or` the
        # rolling average could never survive turn one. Identity, not truthiness.
        state = {} if state is None else state
        skills = sorted(set(self.vectors) - set(self.config.excluded_skills))
        catalog = small_catalog_result(skills, self.config.min_skills_for_retrieval)
        if catalog is not None:
            return catalog

        if not user_text.strip():
            # Nothing to rank against. Expose everything rather than nothing:
            # an empty match set would make every skill unloadable.
            return MatchResult([Match(s, 0.0, "fallback") for s in skills],
                               fallback=True)

        try:
            query = await asyncio.wait_for(
                self.embedder.embed(user_text), self.config.timeout_seconds)
            if self.vectors and any(len(vector) != len(query) for vector in self.vectors.values()):
                raise ValueError("Query/index embedding dimensions differ")
            if not all(math.isfinite(value) for value in query):
                raise ValueError("Query embedding contains non-finite values")
        except Exception:
            # Catch broadly, not just TimeoutError: backend clients raise their
            # own types that do NOT subclass it (litellm.exceptions.*,
            # custom service errors), and those escaping into request handling turns a
            # ranking hiccup into a failed conversation.
            logger.warning("skill embedding failed; exposing all skills", exc_info=True)
            return MatchResult([Match(s, 0.0, "fallback") for s in skills],
                               fallback=True)

        history = self._roll_query_history(query, state, scope)

        # Ranking, margin and every score below come from the CURRENT turn only.
        # History widens which skills stay reachable; it never changes confidence.
        # That split is deliberate: a blended vector is broadly similar to a lot
        # of things, so letting it drive scores would shrink margins and let a
        # topic the customer has moved on from auto-inject its skill.
        scored = [(s, cosine(query, self.vectors[s])) for s in skills]
        scored.sort(key=lambda x: -x[1])
        ranking_margin = scored[0][1] - scored[1][1] if len(scored) > 1 else 1.0
        scored = [(s, v) for s, v in scored if v >= self.config.min_score]

        matches = [Match(s, v, "embedding") for s, v in scored[:self.config.top_k]]
        chosen = {m.skill for m in matches}

        # Lexical backfill. Embeddings miss exact product names and codes, which
        # is precisely what users type. Scored 0.0 so it is additive only and can
        # never win auto-activation.
        if self.config.lexical and self.descriptions:
            q = _tokens(user_text)
            if q:
                overlaps = []
                for s in skills:
                    if s in chosen:
                        continue
                    d = _tokens(self.descriptions.get(s, ""))
                    if d and (hits := len(q & d)):
                        overlaps.append((hits / len(q), s))
                overlaps.sort(reverse=True)
                for _ratio, s in overlaps:
                    if len(matches) >= self.config.total_cap:
                        break
                    matches.append(Match(s, 0.0, "lexical"))
                    chosen.add(s)

        # Rolling-history backfill. A terse follow-up ("tell me more", "cheapest
        # first") carries almost no retrievable signal on its own, and the skill
        # the customer is still talking about drops out of the match set -- which
        # unbinds its tools mid-task. Blending prior turns into the query keeps it
        # reachable. Scored 0.0 with its own source, so like lexical backfill it
        # is additive only: auto-activation requires source == "embedding", so a
        # skill that only history surfaced can never be injected without the model
        # asking for it. State pinning covers the same symptom for a DECLARED
        # in-progress flow; this covers the undeclared case, where nothing knew in
        # advance which skill to protect.
        if history is not None:
            ranked = sorted(((cosine(history, self.vectors[s]), s) for s in skills),
                            reverse=True)
            for value, s in ranked[:self.config.top_k]:
                if s in chosen or value < self.config.min_score:
                    continue
                if len(matches) >= self.config.total_cap:
                    break
                matches.append(Match(s, 0.0, "history"))
                chosen.add(s)

        # State pins, last: an in-progress flow must stay reachable even if the
        # cap is already full, so they are appended and the cap is exceeded by
        # design rather than dropping them.
        for pin in self.config.state_pins:
            if pin.skill in chosen or pin.skill not in skills:
                continue
            if pin.state_key in state and pin.fires(state[pin.state_key]):
                matches.append(Match(pin.skill, 0.0, "pinned"))
                chosen.add(pin.skill)

        result = MatchResult(matches, ranking_margin=ranking_margin)

        if self.config.auto_activate and result.matches:
            top = result.matches[0]
            if (top.source == "embedding"
                    and top.skill not in self.config.auto_activate_exclude
                    and top.score >= self.config.auto_activate_min_score
                    and result.margin >= self.config.auto_activate_min_margin):
                result.auto_activated = top.skill
        return result

    # ------------------------------------------------------------------- bind

    def allows(self, skill: str, result: MatchResult) -> bool:
        """Whether `skill` may be loaded this turn.

        This is the enforcement ADK does not do: it parses `allowed_tools` but
        never checks it. Without this, retrieval is advisory and the model can
        load anything.
        """
        return skill in result.names and skill not in self.config.excluded_skills

    def bind_tools(
        self,
        result: MatchResult,
        state: dict,
        tools_by_skill: dict[str, Sequence[str]],
        catalog: Iterable[str],
        agent_name: str = "agent",
    ) -> tuple[list[str], list[str]]:
        """Resolve tools for the activated skills.

        Returns (bound, unresolved). Report unresolvable names rather than silently
        dropping them: a frontmatter typo otherwise tells the agent to call a tool it
        does not have. Returning them makes that visible.
        """
        known = set(catalog)
        activated = state.get(ACTIVATED_KEY.format(agent=agent_name)) or []
        if result.auto_activated and result.auto_activated not in activated:
            activated = [result.auto_activated, *activated]

        # Intersect with this turn's matches: an activated skill that no longer
        # matches must not keep its tools bound.
        allowed = [s for s in activated if self.allows(s, result)]
        allowed = allowed[:self.config.max_activated_skills]

        bound: list[str] = []
        unresolved: list[str] = []
        for skill in allowed:
            for tool in tools_by_skill.get(skill, ()):
                if tool not in known:
                    unresolved.append(tool)
                elif tool not in bound:
                    bound.append(tool)
        return bound, unresolved


# ------------------------------------------------------------------- loading

@dataclass
class VectorFile:
    vectors: dict
    descriptions: dict
    backend: str | None = None
    task_type: str | None = None
    fingerprint: str | None = None

    @property
    def dimensions(self) -> int | None:
        return len(next(iter(self.vectors.values()))) if self.vectors else None


def load_vectors(path: pathlib.Path) -> VectorFile:
    """Read a precomputed vector file.

    Two layouts are accepted. The metadata-bearing layout:

        {"embedding_metadata": {"fingerprint": ..., "backend": "local",
                                "task_type": "RETRIEVAL_DOCUMENT"},
         "skills": {"name": {"description": str, "embedding": [float, ...]}}}

    and a flat `{"name": {...}}` form, which is what a hand-written fixture
    tends to look like.

    `backend` is carried through deliberately: a vector file built by one backend
    and queried by another produces meaningless similarities rather than an
    error, so the caller can compare it against the configured model.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("embedding_metadata") or {}
    entries = data.get("skills") if isinstance(data.get("skills"), dict) else data

    vectors, descriptions = {}, {}
    for name, entry in entries.items():
        if name == "embedding_metadata":
            continue
        if isinstance(entry, dict):
            if "embedding" in entry:
                vectors[name] = entry["embedding"]
            descriptions[name] = entry.get("description", "")
        elif isinstance(entry, list):
            vectors[name] = entry

    return VectorFile(
        vectors=vectors,
        descriptions=descriptions,
        backend=meta.get("backend"),
        task_type=meta.get("task_type"),
        fingerprint=meta.get("fingerprint"),
    )


def config_from_dict(d: dict) -> RetrievalConfig:
    """Build a config from the resolved `features.skill_retrieval` options."""
    pins = []
    for p in d.get("state_pins") or ():
        aw = p.get("active_when", "non_empty")
        if isinstance(aw, dict) and "in" in aw:
            aw = ("in", tuple(aw["in"]))
        pins.append(StatePin(p["state_key"], p["skill"], aw))
    d = dict(d)
    if "bm25" in d and "lexical" not in d:
        d["lexical"] = d["bm25"]
    known = {f.name for f in RetrievalConfig.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in d.items() if k in known and k != "state_pins"}
    for key in ("auto_activate_exclude", "excluded_skills"):
        if key in kwargs:
            kwargs[key] = frozenset(kwargs[key])
    return RetrievalConfig(state_pins=tuple(pins), **kwargs)
