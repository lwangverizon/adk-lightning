"""Synthetic broken/fixed consumers for Doctor's retained evidence workflow.

Run: uv run --no-sync python -m examples.incident_contracts
These are toy host boundaries, not an ADK agent or application integration.
Use equivalent instrumentation around real host code in your own evaluator.
"""
from adkl.contract_probes import (
    capability_reachability, delivery_consistency, result_integrity,
)


def consume_result(payload, *, faulty=False):
    """Exercise classification and its independent cache/fallback side effects."""
    cache, cards = {}, []
    if payload is None:
        status = "empty" if faulty else "unknown"
    elif payload.get("error"):
        status = "error"
    else:
        buckets = payload if faulty else {k: v for k, v in payload.items() if k != "metadata"}
        status = ("error" if any(not isinstance(v, list) for v in buckets.values())
                  else "present" if any(buckets.values()) else "empty")
    if status in {"present", "empty"}:
        cache["result"] = payload
    if status in {"unknown", "error"}:
        cards.append("retry")
    return {"status": status, "cached": "result" in cache, "fallback": bool(cards)}


def resolve_followup(*, faulty=False):
    """A workflow replaces an owner; a follow-up ranks a different capability."""
    catalog = ["answer", "other"]
    ranked = ["other"]
    retained = [] if faulty else ["answer"]
    exposed = ranked + retained
    references = {"old_owner": "old_owner" if faulty else "answer"}
    return dict(catalog=catalog, indexed=catalog, exposed=exposed,
                dispatchable=catalog + ["denial_handler"], required=["answer"],
                references=references)


def deliver(*, sink_ok, faulty=False):
    """Record a sink receipt separately from staging and completion claims."""
    staged, accepted, claimed = ["connect"], [], []
    if sink_ok:
        accepted.append("connect")
    if accepted or faulty:
        claimed.append("connect")
    return dict(staged=staged, accepted=accepted, claimed=claimed,
                expected=["connect"] if sink_ok else [])


def checks(*, faulty=False):
    return {
        "metadata": result_integrity(
            observed=consume_result({"items": ["item"], "metadata": "guidance"}, faulty=faulty),
            expected={"status": "present", "cached": True, "fallback": False}),
        "unknown": result_integrity(observed=consume_result(None, faulty=faulty),
            expected={"status": "unknown", "cached": False, "fallback": True}),
        "followup": capability_reachability(**resolve_followup(faulty=faulty)),
        "failed_sink": delivery_consistency(**deliver(sink_ok=False, faulty=faulty)),
    }


if __name__ == "__main__":
    broken, fixed = checks(faulty=True), checks()
    assert not any(broken.values()), broken
    assert all(fixed.values()), fixed
    print("Four synthetic regressions detected; four corrected cases pass.")
