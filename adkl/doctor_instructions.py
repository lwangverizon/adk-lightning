"""Read explicitly configured synthetic assembled-request fixtures for Doctor.

No factory, model or business tool runs. Saved captures do not establish runtime
freshness: host CI must recapture the final request for every expected variant.
"""
from __future__ import annotations

from pathlib import Path

from adkl import evaluation
from adkl.doctor_tools import _label, _load
from adkl.instruction_audit import InstructionSpan, PHASES, audit_request

CONFIG = "doctor-instructions.json"


def collect(root):
    root = Path(root).resolve()
    path = root / CONFIG
    if not path.exists() and not path.is_symlink():
        return None, [], "Unconfigured; no assembled instruction evidence checked."
    config = _load(root, CONFIG)
    evaluation._fields(config, ("version", "captures"), "instruction captures")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("Unsupported instruction capture version")
    captures = config["captures"]
    if not isinstance(captures, list) or not 1 <= len(captures) <= 100:
        raise ValueError("Declare 1–100 expected instruction captures")
    seen, identities, findings = set(), [], []
    for capture in captures:
        evaluation._fields(capture, ("id", "phase", "source"), "instruction capture")
        _label(capture["id"])
        if capture["id"] in seen or capture["phase"] not in PHASES:
            raise ValueError("Duplicate capture or invalid phase")
        seen.add(capture["id"])
        payload = _load(root, capture["source"])
        if payload.get("phase") != capture["phase"]:
            raise ValueError("Expected capture phase differs")
        identities.append([capture, payload])
        arguments = dict(payload)
        arguments["spans"] = [InstructionSpan(**row) for row in arguments["spans"]]
        result = audit_request(**arguments)
        findings.extend({"rule": row["rule"], "severity": row["severity"],
                         "contract": capture["id"] + ":" + row["subject"],
                         "source": capture["source"], "message": row["message"]}
                        for row in result["findings"])
    from adkl.doctor_scope import digest
    # Include every capture byte/attribution in coverage. Deliberately conservative:
    # editing/removing a fixture cannot claim that a runtime finding was resolved.
    return digest(identities), findings, (
        "Saved synthetic final-request fixtures only; runtime freshness, actual "
        "variant coverage and model behavior are not verified. Recapture in host CI.")
