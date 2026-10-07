"""Read retained verification evidence for Doctor; never construct a worker.

Only declared local inputs and retained reports are read. Host grading remains a
claim, not attestation. No case inputs, exception messages or transcripts escape.
"""
from __future__ import annotations

from pathlib import Path

from adkl import evaluation, execution, verification
from adkl.contract_probes import CONTRACTS


def _receipt(root):
    folder = verification._storage(root)
    state_path = folder / "state.json"
    state = verification._read(state_path) if state_path.exists() or state_path.is_symlink() else {}
    if not isinstance(state, dict) or state.keys() - {"baseline", "latest"}:
        raise ValueError("Invalid verification state")
    records = {key: verification._record(folder, value) for key, value in state.items()}
    return {"state": state, "records": records, "running": (folder / "running").exists()}


def _validate_record(record, run_id):
    if (not isinstance(record, dict) or record.get("version") != 1 or record.get("id") != run_id
            or record.get("mode") not in {"baseline", "candidate"}
            or record.get("state") not in {"baseline_recorded", "passed", "regressed", "failed"}
            or not isinstance(record.get("identity"), dict)):
        raise ValueError("Invalid verification record")
    if record["state"] == "failed":
        return
    report, identity = record["report"], record["identity"]
    evaluation.validate(report)
    if (report["subject"] != {"revision": identity["artifact_sha256"],
                             "artifact_sha256": identity["artifact_sha256"]}
            or any(report[key] != identity[key] for key in ("suite_sha256", "evaluator_sha256"))):
        raise ValueError("Report identity mismatch")
    if (record["mode"] == "baseline") != (record["state"] == "baseline_recorded"):
        raise ValueError("Report mode mismatch")


def collect(root, profile=None, *, required_contracts=()):
    """Return a sanitized summary (including coverage identity) and findings.

    Exceptions are intentionally handled by Doctor's sanitized check boundary.
    No call to verification.run/evaluate or execution.Worker is made.
    """
    root = Path(root).resolve()
    required_contracts = set(required_contracts)
    config = root / "verification.yaml"
    if not config.exists() and not config.is_symlink():
        return {"state": "unconfigured", "contracts": [
            {"name": name, "required": True, "cases": [], "state": "missing_cases",
             "observations": 0} for name in sorted(required_contracts)
        ], "evidence": "retained_host_report"}, [
            {"rule": "CONTRACT_EVIDENCE_REQUIRED", "severity": "ERROR",
             "contract": name, "source": "verification.yaml",
             "message": "Required host contract evidence is unconfigured."}
            for name in sorted(required_contracts)
        ]
    current = verification.snapshot(root)
    identity = current["identity"]
    selected, _ = execution.profile(root, profile)
    cases = current["suite"]["cases"]
    required = set(current["contracts"]) | required_contracts
    names = required | {name for case in cases for name in case.get("adkl_contracts", [])}
    # Policy/suite/evaluator/profile changes are different coverage, not fixes.
    # Subject content is deliberately excluded so corrected code can resolve a
    # failure when the identical evaluator and suite rerun against that code.
    scope = verification._hash({
        "identity": {key: identity[key] for key in (
            "suite_sha256", "evaluator_sha256", "configuration_sha256", "profile")},
        "required_contracts": sorted(required_contracts),
    })
    summary = {"state": "not_run", "evidence": "retained_host_report", "profile": identity["profile"],
               "subjectDigest": identity["artifact_sha256"], "suiteDigest": identity["suite_sha256"],
               "evaluatorDigest": identity["evaluator_sha256"], "coverageDigest": scope,
               "runId": None, "contracts": [],
               "requestCosts": {"state": "not_run" if current["request_costs"] else "not_configured"}}
    receipt = _receipt(root)
    for key, record in receipt["records"].items():
        _validate_record(record, receipt["state"][key])
    latest = receipt["records"].get("latest")
    rows = []
    if selected != identity["profile"]:
        summary["state"] = "profile_mismatch"
    elif receipt["running"]:
        summary["state"] = "running"
    elif latest:
        summary["runId"] = latest["id"]
        if latest["identity"] != identity:
            summary["state"] = "stale"
        else:
            summary["state"] = latest["state"]
            if latest["state"] != "failed":
                report = latest["report"]
                expected = {(case["id"], n) for case in cases
                            for n in range(1, current["suite"]["repetitions"] + 1)}
                if evaluation.validate(report) != expected:
                    raise ValueError("Incomplete verification observations")
                baseline = receipt["records"].get("baseline")
                if latest["mode"] == "candidate":
                    if (not baseline or baseline["state"] != "baseline_recorded"
                            or latest.get("baseline") != baseline["id"]):
                        raise ValueError("Missing matching baseline")
                    comparison = evaluation.compare(baseline["report"], report, **identity["policy"])
                    if latest["state"] != ("passed" if comparison["passed"] else "regressed"):
                        raise ValueError("Stored outcome disagrees with observations")
                rows = report["results"]
                if current["request_costs"]:
                    from adkl.request_costs import summarize, validate_records
                    validate_records(latest.get("request_costs"), expected)
                    summary["requestCosts"] = summarize(latest["request_costs"])
                    summary["requestCosts"]["state"] = summary["state"]
    if current["request_costs"] and summary["requestCosts"]["state"] == "not_run":
        summary["requestCosts"]["state"] = summary["state"]
    findings = []

    def finding(rule, severity, message, contract=None):
        findings.append({"rule": rule, "severity": severity, "message": message,
                         "contract": contract, "source": "verification.yaml"})

    if summary["state"] in {"failed", "regressed"}:
        finding("VERIFICATION_FAILED", "ERROR", "The current host verification attempt failed or regressed.")
    elif summary["state"] != "passed":
        finding("VERIFICATION_NOT_CURRENT", "WARN",
                f"Host verification is {summary['state']}; no current passing candidate is established.")
    for name in sorted(names):
        case_ids = sorted(case["id"] for case in cases if name in case.get("adkl_contracts", []))
        observed = [row for row in rows if row["case_id"] in case_ids]
        state = ("missing_cases" if not case_ids else "failed" if any(not row["passed"] for row in observed)
                 else "passed" if observed and summary["state"] == "passed"
                 else "baseline_only" if observed and summary["state"] == "baseline_recorded"
                 else summary["state"] if summary["state"] != "passed" else "not_run")
        summary["contracts"].append({"name": name, "required": name in required,
                                     "cases": case_ids, "state": state, "observations": len(observed)})
        if state == "failed":
            finding("CONTRACT_PROBE_FAILED", "ERROR", "A host-reported lifecycle contract observation failed.", name)
        elif state != "passed":
            finding("CONTRACT_EVIDENCE_REQUIRED" if name in required_contracts else
                    "CONTRACT_EVIDENCE_MISSING",
                    "ERROR" if name in required_contracts else "WARN",
                    f"Lifecycle contract evidence is {state}; configure and run the affected host cases.", name)
    summary["unconfiguredContracts"] = sorted(CONTRACTS - names)
    # .adkl is deliberately outside Doctor's source digest. Detect receipt races
    # here, including a failed/new run replacing an earlier passing candidate.
    if verification.snapshot(root)["identity"] != identity or _receipt(root) != receipt:
        raise ValueError("Verification inputs or retained evidence changed during inspection")
    return summary, findings
