"""Small observation assertions for host-owned, credential-free lifecycle probes.

These do not run a host, infer side effects, or enforce runtime policy. Instrument
actual boundaries in a scripted Runner/fake-backend scenario, then pass complete
observations here. Missing observations fail rather than vacuously passing.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

CONTRACTS = frozenset({"no_replay", "single_emitter", "state_isolation", "rejected_call_recovery",
                       "consent_binding", "outcome_consistency", "handoff_continuity",
                       "result_integrity", "capability_reachability", "delivery_consistency"})
OUTCOMES = frozenset({"success", "empty", "suppressed", "error", "timeout", "cancelled"})
RESULT_STATES = frozenset({"present", "empty", "unknown", "partial", "error"})


def _names(values) -> bool:
    return (isinstance(values, (list, tuple))
            and all(isinstance(value, str) and bool(value) for value in values)
            and len(set(values)) == len(values))


def result_integrity(*, observed: Mapping, expected: Mapping) -> bool:
    """Check a consumer's classification, cache write and fallback observations.

    Run the actual producer/parser/consumer with synthetic envelopes. Record
    status, cached and fallback independently, not from a success-shaped reply.
    The host owns expected semantics: unknown is not empty, and a successful
    response with metadata must not be classified as a source failure.
    """
    for row in (observed, expected):
        if not isinstance(row, Mapping) or set(row) != {"status", "cached", "fallback"}:
            return False
        if (not isinstance(row["status"], str) or row["status"] not in RESULT_STATES
                or type(row["cached"]) is not bool or type(row["fallback"]) is not bool):
            return False
    return dict(observed) == dict(expected)


def capability_reachability(*, catalog: Sequence[str], indexed: Sequence[str],
                            exposed: Sequence[str], dispatchable: Sequence[str],
                            required: Sequence[str], references: Mapping[str, str]) -> bool:
    """Required owners survive catalog, index, final request and dispatch boundaries.

    Capture after host eligibility/alias transforms in cold and follow-up turns.
    References are resolved routing targets, not arbitrary prose mentions.
    Dispatch-only denial handlers may exist outside the exposed declarations.
    This does not infer ranking quality, activation or authorization.
    """
    inventories = (catalog, indexed, exposed, dispatchable, required)
    if not all(_names(value) for value in inventories) or not required:
        return False
    if (not isinstance(references, Mapping)
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                   for k, v in references.items())):
        return False
    return (all(set(required) <= set(values) for values in inventories[:-1])
            and set(references.values()) <= set(exposed))


def delivery_consistency(*, staged: Sequence[str], accepted: Sequence[str],
                         claimed: Sequence[str], expected: Sequence[str]) -> bool:
    """Claims match actual sink acceptances, including failed staged deliveries.

    Record logical IDs independently at staging, sink acknowledgement and output
    claim boundaries. Expected is host policy for this invocation; [] is a valid
    failed-sink expectation but an entirely unobserved scenario cannot pass.
    Acceptance here means the named sink, NOT browser display or queue admission.
    """
    if not all(_names(value) for value in (staged, accepted, claimed, expected)):
        return False
    return (bool(staged) and set(accepted) <= set(staged)
            and list(accepted) == list(claimed) == list(expected))


def no_replay(executions: Sequence[str], *, expected: Sequence[str]) -> bool:
    """Each expected logical effect executes exactly once, in the expected order.

    Record at the fake backend, not at model tool-call generation. Exercise an
    empty response, timeout or persistence failure *after* the effect executes.
    Distinct intentionally repeated effects need distinct logical IDs.
    """
    return (bool(expected) and len(set(expected)) == len(expected)
            and list(executions) == list(expected))


def single_emitter(attempts: Sequence[tuple[str, str]], *, owner: str,
                   expected: Sequence[str]) -> bool:
    """Exactly the expected delivery attempts, by one owner, without replay.

    Record (owner, logical item ID) before calling the sink, including rejected
    and failed attempts. For partial/cancelled scenarios, expected names only the
    attempted prefix. This checks attempts, not frontend acknowledgement.
    """
    return (bool(owner) and bool(expected) and len(set(expected)) == len(expected)
            and list(attempts) == [(owner, item) for item in expected])


def state_isolation(actual: Mapping, *, expected: Mapping) -> bool:
    """Compare explicitly selected persisted AND returned state observations.

    Include invocation/owner and pending-output observations as appropriate.
    Test failed/empty/cancelled refreshes and a deliberately interleaved older
    refresh. Expected values are host policy, not inferred from key names.
    """
    return bool(expected) and dict(actual) == dict(expected)


def rejected_call_recovery(outcomes: Sequence[bool]) -> bool:
    """A rejected call followed by a valid call in the same invocation succeeds.

    Supply actual admission/execution outcomes, not success-shaped text. The
    host scenario must additionally assert rejection caused no forbidden effect.
    """
    return (len(outcomes) == 2 and outcomes[0] is False and outcomes[1] is True)


def outcome_consistency(observed: Mapping[str, str], *, expected: str,
                        observers: Sequence[str]) -> bool:
    """All declared boundaries report the expected normalized terminal outcome.

    Observe the workflow, callback, analytics and completion independently. The
    host normalizes its envelopes; missing observers or all-wrong agreement fail.
    Use separate cases for empty/suppressed, error, timeout and cancellation.
    """
    return (isinstance(expected, str) and expected in OUTCOMES
            and bool(observers) and len(set(observers)) == len(observers)
            and all(isinstance(name, str) and bool(name) for name in observers)
            and set(observed) == set(observers)
            and all(value == expected for value in observed.values()))


def consent_binding(*, checks: Mapping[str, bool], effects: Sequence[str],
                    expected_effects: Sequence[str]) -> bool:
    """Check observed admission predicates AND fake-backend execution IDs.

    Predicates must come from instrumentation at the actual write boundary, not
    model arguments. Passing a refusal case requires zero effects even when a
    model attempted submission. Account/target digests stay host-local. This is
    not a consent implementation, identity verification or distributed lock.
    """
    required = {"affirmative", "account_matches", "target_matches", "fresh",
                "next_user_turn", "not_consumed"}
    if set(checks) != required or any(type(value) is not bool for value in checks.values()):
        return False
    if (isinstance(effects, (str, bytes)) or isinstance(expected_effects, (str, bytes))
            or any(not isinstance(item, str) or not item for item in [*effects, *expected_effects])
            or len(set(expected_effects)) != len(expected_effects)):
        return False
    admitted = all(checks.values())
    return (bool(expected_effects) is admitted
            and list(effects) == list(expected_effects))


def handoff_continuity(*, observed: Mapping, expected: Mapping) -> bool:
    """Compare explicit handoff/session and discovery-boundary observations.

    Session/invocation IDs and query digests are synthetic or host-local. Observe
    discovery before before_model; a correct final prompt alone cannot pass.
    Negatives/topic changes use handoff_used=False with their own expected query.
    This asserts continuity, not consent or natural-language intent accuracy.
    """
    fields = {"session", "invocation", "query_digest", "tools", "handoff_used"}
    for row in (observed, expected):
        if set(row) != fields:
            return False
        if any(not isinstance(row[key], str) or not row[key]
               for key in ("session", "invocation", "query_digest")):
            return False
        if (type(row["handoff_used"]) is not bool or not isinstance(row["tools"], list)
                or any(not isinstance(name, str) or not name for name in row["tools"])
                or len(set(row["tools"])) != len(row["tools"])):
            return False
    return (all(observed[key] == expected[key] for key in fields - {"tools"})
            and set(observed["tools"]) == set(expected["tools"]))
