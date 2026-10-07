"""Read reviewed, publisher-bound Doctor receipts outside the serving container.

A receipt is a publisher assertion, not attestation. Exact snapshot matching does
not independently prove that tested source equals a deployed image. No evaluator,
Doctor, factory, subprocess, model or host tool is invoked here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat

from adkl.connected_schema import document, fields, rows, text
from adkl.contract_probes import CONTRACTS

STATES = {"passed", "regressed", "failed", "stale", "not_run", "running",
          "baseline_recorded", "profile_mismatch", "unconfigured", "unavailable"}
CONTRACT_STATES = STATES | {"missing_cases", "baseline_only"}
DIGEST_FIELDS = {"subjectDigest", "suiteDigest", "evaluatorDigest", "coverageDigest"}


class EvidenceStore:
    def __init__(self, directory=None):
        self.directory = Path(directory).resolve() if directory is not None else None

    def summary(self, snapshot, digest):
        base = {"state": "not_configured", "provenance": "publisher_asserted",
                "executedHere": False}
        if self.directory is None:
            return base
        try:
            # Agent IDs are already validated by the gateway's fixed registry.
            path = self.directory / (snapshot["agentId"] + ".json")
            # No symlink/FIFO/device reads; atomically replace regular receipts.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
                    raise ValueError("Invalid receipt file")
                body = source.read(256 * 1024 + 1)
                if len(body) > 256 * 1024:
                    raise ValueError("Receipt exceeds limit")
                receipt = document(body)
            fields(receipt, {"schemaVersion", "kind", "binding", "publishedAt",
                             "expiresAt", "doctor"})
            if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
                raise ValueError("Invalid receipt version")
            if receipt["kind"] != "adkl_retained_verification_receipt":
                raise ValueError("Invalid receipt kind")
            expected = {key: snapshot[key] for key in (
                "agentId", "environment", "release", "instanceId", "generation")}
            expected["snapshotDigest"] = digest
            fields(receipt["binding"], expected.keys())
            if receipt["binding"] != expected or snapshot["release"] == "unknown":
                return {**base, "state": "identity_mismatch"}
            published = datetime.fromisoformat(receipt["publishedAt"])
            expires = datetime.fromisoformat(receipt["expiresAt"])
            now = datetime.now(timezone.utc)
            if (published.tzinfo is None or expires.tzinfo is None
                    or published > now or expires <= published):
                raise ValueError("Invalid receipt times")
            if now >= expires:
                return {**base, "state": "expired"}
            doctor = receipt["doctor"]
            fields(doctor, {"sourceDigest", "verificationEvidence"})
            text(doctor["sourceDigest"], nonempty=True)
            evidence = doctor["verificationEvidence"]
            fields(evidence, {"state", "evidence", "contracts"},
                   {"profile", "runId", "unconfiguredContracts", "requestCosts"} | DIGEST_FIELDS)
            if "requestCosts" in evidence:
                from adkl.request_costs import validate_summary
                validate_summary(evidence["requestCosts"])
                cost_state = evidence["requestCosts"]["state"]
                if cost_state != "not_configured" and cost_state != evidence["state"]:
                    raise ValueError("Request cost evidence state mismatch")
                # Retained local measurements are deliberately not published by
                # the connected gateway; accepting the additive field is not a
                # deployed cost/latency claim.
            if (evidence["state"] not in STATES
                    or evidence["evidence"] != "retained_host_report"):
                raise ValueError("Invalid retained evidence state")
            for key in DIGEST_FIELDS:
                if key in evidence:
                    text(evidence[key], nonempty=True)
            if evidence.get("profile") is not None:
                text(evidence["profile"])
            if evidence.get("runId") is not None:
                text(evidence["runId"], nonempty=True)
            if evidence["state"] in {"passed", "regressed", "baseline_recorded"}:
                if not DIGEST_FIELDS <= evidence.keys() or not evidence.get("runId"):
                    raise ValueError("Missing evidence identity")
            contracts, names = [], set()
            for contract in rows(evidence["contracts"], len(CONTRACTS)):
                fields(contract, {"name", "state", "required", "cases", "observations"})
                name, state = contract["name"], contract["state"]
                if name not in CONTRACTS or name in names or state not in CONTRACT_STATES:
                    raise ValueError("Invalid contract")
                names.add(name)
                if type(contract["required"]) is not bool:
                    raise ValueError("Invalid contract requirement")
                cases = rows(contract["cases"], 1000)
                for case in cases:
                    text(case, nonempty=True)
                observations = contract["observations"]
                if (len(set(cases)) != len(cases) or type(observations) is not int
                        or not 0 <= observations <= 100000):
                    raise ValueError("Invalid observations")
                if state == "passed" and (evidence["state"] != "passed"
                                           or not cases or observations < len(cases)):
                    raise ValueError("Vacuous passing evidence")
                # Case IDs are not necessary for the browser and may be private.
                contracts.append({"name": name, "state": state,
                                  "required": contract["required"],
                                  "caseCount": len(cases), "observations": observations})
            unconfigured = rows(evidence.get("unconfiguredContracts", []), len(CONTRACTS))
            if (len(set(unconfigured)) != len(unconfigured)
                    or any(name not in CONTRACTS or name in names for name in unconfigured)):
                raise ValueError("Invalid unconfigured contracts")
            return {**base, "state": "matched", "publishedAt": receipt["publishedAt"],
                    "expiresAt": receipt["expiresAt"], "binding": expected,
                    "sourceDigest": doctor["sourceDigest"],
                    "verificationState": evidence["state"],
                    "runId": evidence.get("runId"), "contracts": contracts,
                    "unconfiguredContracts": unconfigured,
                    **{k: evidence[k] for k in DIGEST_FIELDS if k in evidence}}
        except FileNotFoundError:
            # Missing receipt and dangling pointer differ: neither is a pass.
            state = "invalid" if path.is_symlink() else "not_published"
            return {**base, "state": state}
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return {**base, "state": "invalid"}
