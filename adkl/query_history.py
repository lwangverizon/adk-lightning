"""Bounded query-vector history for built-in or host-owned retrieval policies.

This module owns no embedder, ranker, ADK context or process-local session cache.
``prepare`` is pure and works on read-only state. ``commit`` must be called from
an event-producing writable callback, not ADK toolset preprocessing. Keep the
returned update in the host's invocation cache until that callback executes.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

_STATE_VERSION = 1
_PREFIX = "adkl:weighted_query_history:"


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    values = tuple(float(value) for value in vector)
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("query vector must be nonempty and finite")
    norm = math.hypot(*values)
    if norm == 0 or not math.isfinite(norm):
        raise ValueError("query vector must have a finite, nonzero norm")
    return tuple(value / norm for value in values)


@dataclass(frozen=True)
class QueryHistoryUpdate:
    """Immutable prepared query and pending state update; contains no raw text.

    ``vector`` is for retrieval only. Cache keys, repeat detection and other
    consumers must continue using the original current-turn embedding.
    """

    key: str
    invocation_id: str
    fingerprint: str
    vector: tuple[float, ...]
    records: tuple[tuple[str, tuple[float, ...]], ...]
    previous_invocation_id: str | None

    @property
    def turns_used(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class QueryHistory:
    """Recency-weighted window, newest first; disabled by not installing it.

    The number of weights is the maximum number of user turns INCLUDING the
    current turn. Weights must be positive, nonincreasing, and current-heavy.
    Available weights are renormalized for short conversations. Each stored
    vector is independently normalized before blending; blended vectors are
    never recursively stored. Model identity includes backend/model/task/width
    and is supplied by the caller, not inferred from vector dimensions.
    """

    weights: tuple[float, ...] = (0.60, 0.20, 0.10, 0.06, 0.04)

    def __post_init__(self) -> None:
        weights = tuple(float(weight) for weight in self.weights)
        if (not weights or len(weights) > 100
                or not all(math.isfinite(w) and w > 0 for w in weights)
                or any(a < b for a, b in zip(weights, weights[1:]))
                or weights[0] <= sum(weights[1:])):
            raise ValueError(
                "weights must be 1..100 positive finite, newest-first values; "
                "the current weight must exceed all previous weights combined")
        object.__setattr__(self, "weights", weights)

    def _records(
        self, stored: Any, fingerprint: str, dimensions: int,
    ) -> tuple[tuple[str, tuple[float, ...]], ...]:
        if (not isinstance(stored, dict)
                or stored.get("version") != _STATE_VERSION
                or stored.get("fingerprint") != fingerprint
                or stored.get("weights") != list(self.weights)):
            return ()
        rows = stored.get("records")
        if not isinstance(rows, list) or len(rows) > len(self.weights):
            return ()
        result = []
        seen = set()
        try:
            for row in rows:
                ident, vector = row["invocation_id"], _unit(row["vector"])
                if (not isinstance(ident, str) or not ident or ident in seen
                        or len(vector) != dimensions):
                    return ()
                result.append((ident, vector))
                seen.add(ident)
        except (KeyError, TypeError, ValueError, OverflowError):
            return ()
        return tuple(result)

    def prepare(
        self, vector: Sequence[float], state: Mapping[str, Any], *,
        scope: str, invocation_id: str, fingerprint: str,
    ) -> QueryHistoryUpdate:
        """Read history and prepare a blend without modifying state.

        Missing/corrupt/incompatible history starts a new window. Invalid current
        vectors raise ValueError so the caller can use its existing fallback.
        Missing scope, invocation identity or provenance is a configuration error.
        Repeated preparation of a committed invocation reuses its original vector.
        """
        if not scope or not invocation_id or not fingerprint:
            raise ValueError("scope, invocation_id and fingerprint are required")
        current = _unit(vector)
        key = _PREFIX + scope
        prior = self._records(state.get(key), fingerprint, len(current))
        previous = prior[0][0] if prior else None
        if prior and prior[0][0] == invocation_id:
            records = prior
        elif any(ident == invocation_id for ident, _ in prior):
            raise ValueError("cannot prepare an older invocation after a newer turn")
        else:
            records = ((invocation_id, current), *prior[:len(self.weights) - 1])
        weights = self.weights[:len(records)]
        total = sum(weights)
        blended = tuple(
            sum(w * row[1][i] for w, row in zip(weights, records)) / total
            for i in range(len(current))
        )
        return QueryHistoryUpdate(
            key, invocation_id, fingerprint, _unit(blended), records, previous)

    def commit(
        self, state: MutableMapping[str, Any], update: QueryHistoryUpdate,
    ) -> bool:
        """Persist once per invocation using assignment, never nested mutation.

        Returns False for duplicate or stale updates. This detects overlapping
        turns within one state snapshot, not distributed concurrency: hosts must
        serialize turns or use their session store's concurrency control.
        Read-only writes raise; callers must not mistake them for persistence.
        """
        prior = self._records(
            state.get(update.key), update.fingerprint, len(update.vector))
        if any(ident == update.invocation_id for ident, _ in prior):
            return False
        latest = prior[0][0] if prior else None
        if latest != update.previous_invocation_id:
            return False
        state[update.key] = {
            "version": _STATE_VERSION,
            "fingerprint": update.fingerprint,
            "weights": list(self.weights),
            "records": [
                {"invocation_id": ident, "vector": list(vector)}
                for ident, vector in update.records
            ],
        }
        return True
