"""Numeric-only host request measurements; never tokenization or provider billing.

Hosts capture the actual request boundary and supply counts. This module checks
shape/accounting, not instrumentation truth. Kept outside evaluation's v1 report.
"""
from __future__ import annotations

from adkl import evaluation

PARTITIONS = ("system", "tools", "history", "other")
PHASES = frozenset({"cold", "followup", "topic_switch", "recovery"})
BOUNDARIES = frozenset({"adk_request", "provider_request"})


def validate(rows):
    """Validate one case's nonempty ordered request captures (no raw content).

    bytes are disjoint partitions; tokens are independently counted and need not
    sum. tokenizer_sha256 identifies the host's proxy tokenizer, not the model.
    """
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("request_costs needs 1–1000 captures")
    for index, row in enumerate(rows, 1):
        evaluation._fields(row, ("request", "phase", "boundary", "tokenizer_sha256",
                                  "bytes", "proxy_tokens"), "request cost")
        if type(row["request"]) is not int or row["request"] != index:
            raise ValueError("Request captures must be contiguous from 1")
        if (not isinstance(row["phase"], str) or row["phase"] not in PHASES
                or not isinstance(row["boundary"], str) or row["boundary"] not in BOUNDARIES):
            raise ValueError("Unknown request phase or measurement boundary")
        evaluation._digest(row["tokenizer_sha256"], "tokenizer_sha256")
        for metric in ("bytes", "proxy_tokens"):
            values = row[metric]
            evaluation._fields(values, (*PARTITIONS, "total"), metric)
            if any(type(n) is not int or not 0 <= n <= 10**12 for n in values.values()):
                raise ValueError("Request counts must be bounded nonnegative integers")
        if sum(row["bytes"][key] for key in PARTITIONS) != row["bytes"]["total"]:
            raise ValueError("Disjoint byte partitions must sum to total")
    return rows


def validate_records(records, expected):
    """Require every case/repetition once when a run opts into measurements."""
    if not isinstance(records, list) or len(records) != len(expected):
        raise ValueError("Incomplete request cost observations")
    seen = set()
    for record in records:
        evaluation._fields(record, ("case_id", "repetition", "requests"), "cost record")
        if not isinstance(record["case_id"], str) or type(record["repetition"]) is not int:
            raise ValueError("Invalid cost observation identity")
        key = (record["case_id"], record["repetition"])
        if key not in expected or key in seen:
            raise ValueError("Duplicate or unexpected cost observation")
        seen.add(key)
        validate(record["requests"])
    if seen != expected:
        raise ValueError("Incomplete request cost observations")


def summarize(records):
    """Group comparable captures without publishing case text or inferred savings."""
    groups = {}
    for record in records:
        for row in record["requests"]:
            key = (row["boundary"], row["tokenizer_sha256"], row["phase"])
            group = groups.setdefault(key, {"boundary": key[0], "tokenizerDigest": key[1],
                "phase": key[2], "requests": 0,
                "maxBytes": dict.fromkeys((*PARTITIONS, "total"), 0),
                "maxProxyTokens": dict.fromkeys((*PARTITIONS, "total"), 0)})
            group["requests"] += 1
            for metric, dest in (("bytes", "maxBytes"), ("proxy_tokens", "maxProxyTokens")):
                for part, count in row[metric].items():
                    group[dest][part] = max(group[dest][part], count)
    return {"state": "recorded", "observations": len(records),
            "requests": sum(g["requests"] for g in groups.values()),
            "groups": [groups[key] for key in sorted(groups)],
            "measurement": "host_counts_proxy_tokens_not_billing"}


def validate_summary(summary):
    """Validate additive Doctor data at strict downstream receipt boundaries.

    Publishers may accept then discard this local-only summary. It contains no
    raw captures, arbitrary labels, case inputs or provider cost claims.
    """
    states = {"not_configured", "not_run", "profile_mismatch", "running", "stale",
              "failed", "passed", "regressed", "baseline_recorded"}
    if (not isinstance(summary, dict) or not isinstance(summary.get("state"), str)
            or summary["state"] not in states):
        raise ValueError("Invalid request cost summary state")
    if summary["state"] not in {"passed", "regressed", "baseline_recorded"}:
        evaluation._fields(summary, ("state",), "cost summary")
        return
    evaluation._fields(summary, ("state", "observations", "requests", "groups", "measurement"),
                       "cost summary")
    if summary["measurement"] != "host_counts_proxy_tokens_not_billing":
        raise ValueError("Unknown measurement semantics")
    for key, maximum in (("observations", 100000), ("requests", 100000000)):
        if type(summary[key]) is not int or not 1 <= summary[key] <= maximum:
            raise ValueError("Invalid summary counts")
    if not isinstance(summary["groups"], list) or not 1 <= len(summary["groups"]) <= 100000:
        raise ValueError("Invalid measurement groups")
    seen, total = set(), 0
    for group in summary["groups"]:
        evaluation._fields(group, ("boundary", "tokenizerDigest", "phase", "requests",
                                  "maxBytes", "maxProxyTokens"), "cost group")
        if (not isinstance(group["boundary"], str) or group["boundary"] not in BOUNDARIES
                or not isinstance(group["phase"], str) or group["phase"] not in PHASES):
            raise ValueError("Invalid cost group identity")
        evaluation._digest(group["tokenizerDigest"], "tokenizerDigest")
        identity = (group["boundary"], group["tokenizerDigest"], group["phase"])
        if identity in seen:
            raise ValueError("Duplicate cost group")
        seen.add(identity)
        if type(group["requests"]) is not int or not 1 <= group["requests"] <= 100000000:
            raise ValueError("Invalid group count")
        total += group["requests"]
        for metric in ("maxBytes", "maxProxyTokens"):
            evaluation._fields(group[metric], (*PARTITIONS, "total"), metric)
            if any(type(n) is not int or not 0 <= n <= 10**12 for n in group[metric].values()):
                raise ValueError("Invalid maximum counts")
        if any(group["maxBytes"][p] > group["maxBytes"]["total"] for p in PARTITIONS):
            raise ValueError("Byte partition maximum exceeds total maximum")
    if total != summary["requests"] or summary["requests"] < summary["observations"]:
        raise ValueError("Incomplete summary counts")
