"""Retained per-capability inference evidence; never executes a model or tool.

Counts are host observations, not proof that a capability requires that many
calls. Explicit source inputs establish freshness only within the declared scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import statistics

from adkl import evaluation
from adkl.doctor_scope import digest
from adkl.doctor_tools import _label, _load
from adkl.verification import _path

CONFIG = "doctor-calls.json"
KINDS = {"skill", "workflow", "specialist", "tool", "orchestrator"}
EVIDENCE = {"SCRIPTED_REPLAY", "MEASURED_EVAL", "MEASURED_PRODUCTION"}


def input_digest(root, inputs):
    """Hash explicit host inputs, rejecting credentials, symlinks and self-reference."""
    if not isinstance(inputs, list) or not inputs or len(inputs) > 20000:
        raise ValueError("Declare 1–20000 source inputs")
    if any(not isinstance(name, str) for name in inputs) or len(set(inputs)) != len(inputs):
        raise ValueError("Duplicate or invalid source input")
    rows = []
    for name in sorted(inputs):
        path = _path(Path(root).resolve(), name)
        if name == CONFIG or path.suffix == ".jsonl":
            raise ValueError("Configuration and trace logs cannot identify source")
        relative = path.relative_to(Path(root).resolve())
        if any((Path(root) / part).is_symlink() for part in (relative, *relative.parents)):
            raise ValueError("Source inputs cannot traverse symlinks")
        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Invalid source input")
        rows.append([name, hashlib.sha256(path.read_bytes()).hexdigest()])
    return digest(rows)


def _integer(value, minimum=0, maximum=1000000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Expected bounded integer")


def validate_observations(rows, scenarios):
    """One row per complete invocation; streaming chunks must not be recorded.

    attempts are actual inference requests, including failed requests/retries.
    logical_id groups retries without hiding their cost. owner attributes nested
    work; all attempts contribute to this invocation's inclusive total.
    """
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError("Expected bounded observations")
    seen = set()
    for row in rows:
        evaluation._fields(row, ("id", "scenario", "passed", "attempts"), "call observation")
        _label(row["id"])
        if row["id"] in seen or row["scenario"] not in scenarios:
            raise ValueError("Duplicate invocation or undeclared scenario")
        seen.add(row["id"])
        if type(row["passed"]) is not bool:
            raise ValueError("Outcome must be boolean")
        if not isinstance(row["attempts"], list) or len(row["attempts"]) > 1000:
            raise ValueError("Invalid attempts")
        attempts = set()
        for attempt in row["attempts"]:
            evaluation._fields(attempt, ("id", "logical_id", "owner"), "inference attempt")
            for value in attempt.values():
                _label(value)
            if attempt["id"] in attempts:
                raise ValueError("Duplicate inference attempt (possibly streaming chunks)")
            attempts.add(attempt["id"])
    return rows


def collect(root):
    """Return sanitized coverage and raw warnings from explicit saved evidence."""
    root = Path(root).resolve()
    if not (root / CONFIG).exists() and not (root / CONFIG).is_symlink():
        return {"state": "unconfigured", "capabilities": []}, []
    config = _load(root, CONFIG)
    config.setdefault("budget", 3)
    config.setdefault("max_age_hours", 168)
    evaluation._fields(config, ("version", "catalog", "coverage", "budget", "max_age_hours",
                                "inputs", "capabilities"), "call budgets")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("Unsupported call budget version")
    if config["catalog"] not in {"prod", "uat", "test"} or config["coverage"] not in {"partial", "complete"}:
        raise ValueError("Invalid catalog coverage")
    _integer(config["budget"], 1, 1000)
    _integer(config["max_age_hours"], 1, 8760)
    identity = input_digest(root, config["inputs"])
    caps = config["capabilities"]
    if not isinstance(caps, list) or not 1 <= len(caps) <= 10000:
        raise ValueError("Declare capability inventory")
    result, findings, seen = [], [], set()

    def finding(rule, capability, message):
        findings.append({"rule": rule, "severity": "WARN", "contract": capability,
                         "source": CONFIG, "message": f"{capability}: {message}"})

    if config["coverage"] == "partial":
        finding("CAPABILITY_LLM_COVERAGE_PARTIAL", "catalog",
                "Declared inventory is partial; all-production coverage is not established.")
    for cap in caps:
        evaluation._fields(cap, ("id", "kind", "surface", "variant", "scenarios", "evidence"),
                           "capability budget")
        for key in ("id", "surface", "variant"):
            _label(cap[key])
        if cap["id"] in seen or cap["kind"] not in KINDS:
            raise ValueError("Duplicate capability or invalid kind")
        seen.add(cap["id"])
        scenarios = cap["scenarios"]
        if not isinstance(scenarios, list) or not scenarios or len(scenarios) > 100:
            raise ValueError("Declare expected scenarios")
        for scenario in scenarios:
            _label(scenario)
        if len(set(scenarios)) != len(scenarios):
            raise ValueError("Duplicate scenario")
        item = {key: cap[key] for key in ("id", "kind", "surface", "variant")}
        item.update(state="NOT_MEASURED", evidence=None, samples=0, scenarios=len(scenarios),
                    coveredScenarios=0, p50=None, p95=None, maximum=None, overBudget=None,
                    successRate=None, retryAttempts=None, caseDigest=None)
        result.append(item)
        if cap["evidence"] is None:
            finding("CAPABILITY_LLM_CALLS_UNMEASURED", cap["id"],
                    "No retained inference evidence; unknown is not zero or a pass.")
            continue
        if cap["evidence"] in config["inputs"]:
            raise ValueError("Evidence cannot be its own source identity")
        payload = _load(root, cap["evidence"])
        evaluation._fields(payload, ("version", "capability", "source_digest", "configuration",
                                     "evidence", "recorded_at", "observations"), "call evidence")
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError("Unsupported evidence version")
        evaluation._digest(payload["source_digest"], "source_digest")
        if payload["capability"] != cap["id"] or payload["evidence"] not in EVIDENCE:
            raise ValueError("Evidence owner or class mismatch")
        _label(payload["configuration"])
        if not isinstance(payload["recorded_at"], str):
            raise ValueError("Evidence timestamp must be ISO-8601 with timezone")
        recorded = datetime.fromisoformat(payload["recorded_at"])
        if recorded.tzinfo is None:
            raise ValueError("Evidence timestamp requires timezone")
        age = (datetime.now(timezone.utc) - recorded).total_seconds()
        if age < -300:
            raise ValueError("Evidence timestamp is in the future")
        rows = validate_observations(payload["observations"], scenarios)
        item["evidence"] = payload["evidence"]
        if (payload["source_digest"] != identity or payload["configuration"] != cap["variant"]
                or age > config["max_age_hours"] * 3600):
            item["state"] = "STALE"
            finding("CAPABILITY_LLM_CALLS_EVIDENCE_STALE", cap["id"],
                    "Source/configuration differs or evidence expired; recapture, do not relabel old evidence.")
            continue
        counts = sorted(len(row["attempts"]) for row in rows)
        covered = {row["scenario"] for row in rows}
        item.update(state="CURRENT" if covered == set(scenarios) else "PARTIAL",
                    caseDigest=digest(sorted((r["id"], r["scenario"]) for r in rows)),
                    samples=len(rows), coveredScenarios=len(covered))
        if counts:
            item.update(p50=statistics.median(counts),
                        p95=counts[math.ceil(.95 * len(counts)) - 1] if len(counts) >= 20 else None,
                        maximum=max(counts), overBudget=sum(n > config["budget"] for n in counts),
                        successRate=sum(row["passed"] for row in rows) / len(rows),
                        retryAttempts=sum(len(row["attempts"]) - len({a["logical_id"] for a in row["attempts"]})
                                          for row in rows))
        if item["state"] == "PARTIAL":
            finding("CAPABILITY_LLM_CALLS_UNMEASURED", cap["id"],
                    f"Only {len(covered)}/{len(scenarios)} declared scenarios observed.")
        if item["overBudget"]:
            finding("CAPABILITY_LLM_CALLS_OVER_BUDGET", cap["id"],
                    f"{item['overBudget']}/{len(rows)} invocations observed >{config['budget']} "
                    f"LLM calls; max={item['maximum']}; evidence={payload['evidence']}. "
                    "Includes unsuccessful runs and retries; observed, not required. "
                    "Preserve consent, grounding and recovery; not a runtime cutoff.")
    summary = {"state": "recorded", "catalog": config["catalog"],
               "coverage": config["coverage"], "budget": config["budget"],
               "sourceDigest": identity, "capabilities": result,
               "coverageDigest": digest([config, [[r["id"], r["evidence"], r["caseDigest"]]
                                                  for r in result]])}
    return summary, findings


def regressions(previous, current):
    """Compare per-owner medians only for matching evidence classes and coverage.

    Baseline source may differ (that is the purpose); configuration, inventory,
    scenarios, budgets and sample counts must match. Host scenario IDs must name
    stable evaluation cases. Production traffic mixes are not paired trials.
    """
    if (previous.get("state") != "recorded" or current.get("state") != "recorded"
            or previous.get("coverageDigest") != current.get("coverageDigest")):
        return []
    old = {row["id"]: row for row in previous["capabilities"]}
    findings = []
    for row in current["capabilities"]:
        before = old.get(row["id"], {})
        if (row["state"] != "CURRENT" or before.get("state") != "CURRENT"
                or row["evidence"] != before.get("evidence")
                or row["evidence"] not in {"SCRIPTED_REPLAY", "MEASURED_EVAL"}
                or row.get("caseDigest") != before.get("caseDigest")
                or not row["samples"] or row["samples"] != before.get("samples")):
            continue
        if row["p50"] > before["p50"]:
            findings.append({"rule": "CAPABILITY_LLM_CALLS_REGRESSION", "severity": "WARN",
                             "contract": row["id"], "source": CONFIG,
                             "message": f"{row['id']}: observed median calls increased "
                             f"{before['p50']} -> {row['p50']} on declared comparable scenarios. "
                             "Review outcomes and traces; below-budget regressions also matter."})
    return findings


def render(summary):
    """Prominent coverage table, never a claim of live production inspection."""
    lines = ["", "## PRODUCTION CAPABILITIES — LLM CALL BUDGET", ""]
    if summary.get("state") != "recorded":
        return lines + ["NOT_MEASURED: configure doctor-calls.json; no call-count coverage established."]
    lines += [f"Catalog: {summary['catalog']}; host-declared inventory: {summary['coverage']}; "
              f"warning above {summary['budget']} inference attempts/customer invocation.",
              "Retained host evidence only. No model executed. Counts include nested work/retries, "
              "not tool calls or customer messages. Rows are inclusive: do not sum across owners.",
              "CURRENT means matching declared inputs/scenarios, not fresh live production traffic. "
              "Scripted replay is not real-model evidence. p95 is n/a below 20 samples.", "",
              "| Capability / surface / variant | Evidence | State | Samples | Scenarios | p50 | p95 | Max | Over budget |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in summary["capabilities"]:
        values = [f"{row['id']} / {row['surface']} / {row['variant']}", row["evidence"],
                  row["state"], row["samples"], f"{row['coveredScenarios']}/{row['scenarios']}",
                  row["p50"], row["p95"], row["maximum"], row["overBudget"]]
        lines.append("| " + " | ".join("n/a" if v is None else str(v) for v in values) + " |")
    return lines
