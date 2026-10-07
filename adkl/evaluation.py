"""Compare host-produced evaluation results without executing an agent.

Exit codes: 0 meets the configured gates, 1 regression, 2 invalid evidence.
Inputs are claims by the host evaluator, not signed proof of execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import sys


class EvaluationError(ValueError):
    """Evidence is incomplete, incompatible, or malformed."""


def _fields(value, expected, location):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise EvaluationError(f"{location}: expected fields {', '.join(expected)}")


def _number(value, location, minimum=0):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    if not valid:
        raise EvaluationError(f"{location}: expected a finite number >= {minimum}")


def _digest(value, location):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EvaluationError(f"{location}: expected a lowercase SHA-256 digest")


def validate(run):
    """Require complete, comparable observations; no skipped or duplicate cases."""
    _fields(run, ("version", "suite_sha256", "evaluator_sha256", "subject", "results"), "run")
    if type(run["version"]) is not int or run["version"] != 1:
        raise EvaluationError("run.version: expected 1")
    for key in ("suite_sha256", "evaluator_sha256"):
        _digest(run[key], key)
    _fields(run["subject"], ("revision", "artifact_sha256"), "subject")
    revision = run["subject"]["revision"]
    if not isinstance(revision, str) or not revision.strip():
        raise EvaluationError("subject.revision: expected a nonempty revision")
    _digest(run["subject"]["artifact_sha256"], "subject.artifact_sha256")
    if not isinstance(run["results"], list) or not run["results"]:
        raise EvaluationError("results: expected a nonempty list")
    seen = set()
    for row in run["results"]:
        _fields(row, ("case_id", "repetition", "passed", "latency_ms", "cost_usd"), "result")
        if not isinstance(row["case_id"], str) or not row["case_id"].strip():
            raise EvaluationError("case_id: expected a nonempty string")
        if type(row["repetition"]) is not int or row["repetition"] < 1:
            raise EvaluationError("repetition: expected a positive integer")
        key = (row["case_id"], row["repetition"])
        if key in seen:
            raise EvaluationError(f"duplicate observation: {key}")
        seen.add(key)
        if type(row["passed"]) is not bool:
            raise EvaluationError("passed: expected true or false")
        for name in ("latency_ms", "cost_usd"):
            _number(row[name], name)
    return seen


def _summary(run):
    rows = run["results"]
    latencies = sorted(row["latency_ms"] for row in rows)
    cost = math.fsum(row["cost_usd"] for row in rows)
    successes = sum(row["passed"] for row in rows)
    by_case = {}
    for row in rows:
        by_case[row["case_id"]] = by_case.get(row["case_id"], 0) + row["passed"]
    return {"observations": len(rows), "successes": successes,
            "pass_rate": successes / len(rows),
            "p95_latency_ms": latencies[math.ceil(.95 * len(rows)) - 1],
            "total_cost_usd": cost,
            "cost_per_success_usd": cost / successes if successes else None,
            "successes_by_case": dict(sorted(by_case.items()))}


def compare(baseline, candidate, *, min_pass_rate=1.0,
            max_latency_ratio=None, max_cost_ratio=None):
    """Gate per-case success counts, absolute quality, and optional resource ratios.

    Results must cover identical case/repetition pairs and evaluator/suite digests.
    Repetitions are aggregated per case, not treated as identical random trials.
    """
    _number(min_pass_rate, "min_pass_rate")
    if min_pass_rate > 1:
        raise EvaluationError("min_pass_rate: must be <= 1")
    for name, value in (("max_latency_ratio", max_latency_ratio), ("max_cost_ratio", max_cost_ratio)):
        if value is not None:
            _number(value, name)
    base_keys, candidate_keys = validate(baseline), validate(candidate)
    if base_keys != candidate_keys:
        raise EvaluationError("baseline and candidate must cover identical case/repetition pairs")
    for name in ("suite_sha256", "evaluator_sha256"):
        if baseline[name] != candidate[name]:
            raise EvaluationError(f"{name}: baseline and candidate differ")
    try:
        before, after = _summary(baseline), _summary(candidate)
    except OverflowError as exc:
        raise EvaluationError("aggregate metrics overflow") from exc
    failures = []
    if after["pass_rate"] < min_pass_rate:
        failures.append("candidate pass rate is below the configured minimum")
    regressions = [case for case, count in before["successes_by_case"].items()
                   if after["successes_by_case"][case] < count]
    if regressions:
        failures.append("success count decreased for one or more cases")
    for key, limit in (("p95_latency_ms", max_latency_ratio), ("total_cost_usd", max_cost_ratio)):
        if limit is not None and after[key] > before[key] * limit:
            failures.append(f"{key} exceeded the configured baseline ratio")
    return {"version": 1, "evidence": "host_report_comparison", "passed": not failures,
            "suite_sha256": baseline["suite_sha256"],
            "evaluator_sha256": baseline["evaluator_sha256"],
            "subjects": {"baseline": baseline["subject"], "candidate": candidate["subject"]},
            "policy": {"min_pass_rate": min_pass_rate,
                       "max_latency_ratio": max_latency_ratio, "max_cost_ratio": max_cost_ratio},
            "baseline": before, "candidate": after,
            "regressed_cases": regressions, "failures": failures}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=pathlib.Path)
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-latency-ratio", type=float)
    parser.add_argument("--max-cost-ratio", type=float)
    args = parser.parse_args(argv)
    try:
        inputs = [path.read_bytes() for path in (args.baseline, args.candidate)]
        runs = [json.loads(data, object_pairs_hook=_unique_pairs) for data in inputs]
        report = compare(*runs, min_pass_rate=args.min_pass_rate,
                         max_latency_ratio=args.max_latency_ratio, max_cost_ratio=args.max_cost_ratio)
        report["input_sha256"] = dict(zip(("baseline", "candidate"),
                                         (hashlib.sha256(data).hexdigest() for data in inputs)))
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0 if report["passed"] else 1
    except (OSError, ValueError, UnicodeError, OverflowError) as exc:
        print(json.dumps({"version": 1, "passed": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
