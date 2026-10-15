"""Opt-in, numeric-only inference attempt recorder for host evaluation adapters.

Hosts call begin once at the actual model request boundary (not per stream chunk),
including nested calls and retries. No callbacks are installed automatically.
Owner attribution is host supplied; this recorder cannot detect missed requests.
"""
from __future__ import annotations

from datetime import datetime, timezone

from adkl.doctor_calls import EVIDENCE, input_digest, validate_observations
from adkl.doctor_tools import _label


class InvocationCalls:
    """Record one invocation; synchronous begin is safe between async awaits.

    One instance per invocation; do not share it across threads or invocations.
    Use stable invocation/scenario IDs for paired evaluation cases, not customer
    identifiers. Failure attempts count; a retry uses a new attempt ID with the
    same logical_id. Parallel independent calls use distinct logical IDs.
    """

    def __init__(self, invocation_id, scenario):
        _label(invocation_id)
        _label(scenario)
        self._id = invocation_id
        self._scenario = scenario
        self._attempts = []
        self._pending = set()
        self._closed = False

    def begin(self, *, logical_id, owner):
        """Start an actual request; call again for each observable retry."""
        _label(logical_id)
        _label(owner)
        if self._closed:
            raise ValueError("Invocation already closed")
        attempt_id = f"request-{len(self._attempts) + 1}"
        if len(self._attempts) >= 1000:
            raise ValueError("Invocation attempt limit exceeded")
        self._attempts.append({"id": attempt_id, "logical_id": logical_id, "owner": owner})
        self._pending.add(attempt_id)
        return attempt_id

    def end(self, attempt_id):
        """Mark completion/failure/cancellation in a finally block."""
        if attempt_id not in self._pending:
            raise ValueError("Unknown or already completed attempt")
        self._pending.remove(attempt_id)

    def finish(self, *, passed):
        """Close only after all requests settle; outcome comes from the host grader."""
        if self._closed or self._pending:
            raise ValueError("Invocation closed or has unfinished requests")
        row = {"id": self._id, "scenario": self._scenario, "passed": passed,
               "attempts": [dict(attempt) for attempt in self._attempts]}
        validate_observations([row], [self._scenario])
        self._closed = True
        return row


def evidence(root, inputs, *, capability, configuration, evidence_class, observations,
             started_digest):
    """Make a receipt after a host run, rejecting changes during capture.

    Call input_digest(root, inputs) BEFORE running the host evaluation and pass
    that value as started_digest. Observations must be fresh in this process;
    do not use this helper to relabel imported historical traces. For imported
    production aggregates preserve their original capture time/source identity.
    """
    for name in (capability, configuration):
        _label(name)
    if evidence_class not in EVIDENCE:
        raise ValueError("Unknown evidence class")
    current = input_digest(root, inputs)
    if current != started_digest:
        raise ValueError("Source changed during inference capture")
    validate_observations(observations, {row["scenario"] for row in observations})
    return {"version": 1, "capability": capability, "configuration": configuration,
            "source_digest": current, "recorded_at": datetime.now(timezone.utc).isoformat(),
            "evidence": evidence_class, "observations": observations}
