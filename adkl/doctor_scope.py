"""Comparison coverage, not content identity or runtime attestation.

Fingerprints exclude editable instruction bodies so a correction can resolve a
finding. Coverage changes are deliberately conservative: rerun a baseline for the
new scope rather than claim a removed consumer was repaired.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


SCOPE_VERSION = 1
# Include diagnostic producers, not just report presentation. A changed checker
# must not silently resolve a finding merely because its wording/logic changed.
SCANNER_FILES = (
    "doctor.py", "doctor_scope.py", "diagnostics.py", "audit.py", "projects.py",
    "execution.py", "inspection.py", "native_authoring.py", "skill_lint.py",
    "lint_prompt.py", "lint_conflict.py", "lint_cost.py", "tool_catalog.py",
    "features.py", "capabilities.py", "doctor_evidence.py", "verification.py",
    "evaluation.py", "contract_probes.py", "request_costs.py", "doctor_tools.py",
    "instruction_audit.py", "doctor_instructions.py", "doctor_variants.py",
    "construction_log.py", "doctor_host.py", "doctor_calls.py",
    "doctor_environment.py", "host.py", "agent_schema.py", "worker.py",
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scanner_identity() -> str:
    """Identify the installed checker bytes, not its pre-release package version."""
    root = Path(__file__).parent
    files = [root / name for name in SCANNER_FILES]
    files += sorted((root / "feature_definitions").glob("*.py"))
    return digest([(path.relative_to(root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest()) for path in files])


def source_scope(root, doc, profile):
    """Fingerprint configuration and inventory; never open credential files.

    The complete descriptor/profile declarations conservatively invalidate scope
    on configuration changes. Skill body/metadata edits do not change inventory.
    Unmapped file additions/removals do, even with the same descriptor.
    """
    from adkl import execution
    root = Path(root)
    inventory = {}
    # Reuse the tracked-source walk, including its symlink/exclusion validation.
    execution.source_digest(root, _metadata_only=True, _inventory=inventory)
    paths = sorted(path.relative_to(root).as_posix() for path in inventory
                   if not path.is_dir())
    execution_path = root / "execution.yaml"
    configuration = (hashlib.sha256(execution_path.read_bytes()).hexdigest()
                     if execution_path.is_file() else None)
    import yaml
    skill_names = []
    for relative in paths:
        if Path(relative).name == "SKILL.md":
            from adkl.skill_lint import parse_frontmatter
            metadata, _body = parse_frontmatter(root / relative)
            skill_names.append((relative, metadata.get("name")))
    catalog = (doc.get("tools") or {}).get("catalog")
    catalog_digest = None
    if catalog:
        path = Path(catalog)
        settings = yaml.safe_load(execution_path.read_text()) if execution_path.is_file() else {}
        secrets = {Path(p.get("env_file", ".env")) for p in
                   (settings or {}).get("profiles", {}).values() if isinstance(p, dict)}
        if (path.is_absolute() or path in secrets or any(
                p.startswith(".") or p in execution.SOURCE_EXCLUDED for p in path.parts)
                or any((root / p).is_symlink() for p in [path, *path.parents])):
            raise ValueError("Catalog must be tracked project source")
        catalog_digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
    return digest({"descriptor": doc, "profile": profile,
                   "execution": configuration, "trackedPaths": paths,
                   "skillNames": skill_names, "catalogDigest": catalog_digest})


def native_scope(agent):
    """Retain owner/tree identity and inventory, not text or audit findings."""
    return {
        "name": agent.get("name"), "type": agent.get("type"),
        "instructionKind": agent.get("instruction"),
        "bindings": [{
            "ownership": binding.get("executionOwnership"),
            "features": binding.get("features"),
            "configuration": binding.get("configuration"),
            "skills": sorted(skill["name"] for skill in binding["skills"]),
            "tools": sorted(tool["name"] for tool in binding["tools"]),
        } for binding in agent.get("adkl", [])],
        "children": [native_scope(child) for child in agent.get("children", [])],
    }


def comparison_coverage(previous, current):
    """Return safely comparable checks and human-readable exclusions.

    Legacy v1 reports still load, but have no affirmative resolution evidence.
    Reported findings can remain present across scopes; that is not a fix claim.
    """
    reasons = []
    old_identity, new_identity = previous.get("comparisonIdentity"), current.get("comparisonIdentity")
    if (not isinstance(old_identity, dict) or not isinstance(new_identity, dict)
            or old_identity.get("version") != SCOPE_VERSION
            or new_identity.get("version") != SCOPE_VERSION):
        return set(), ["Baseline or current report lacks supported comparison coverage; absent findings are not verified."]
    if (not old_identity.get("scannerDigest")
            or old_identity.get("scannerDigest") != new_identity.get("scannerDigest")):
        return set(), ["Diagnostic implementation changed or is unknown; absent findings are not verified."]
    if previous.get("freshness") != "current" or current.get("freshness") != "current":
        return set(), ["Baseline or current scan is stale/unknown; absent findings are not verified."]
    if previous.get("context") != current.get("context"):
        return set(), ["Scan context changed; absent findings are not verified."]
    old_checks = previous.get("checks")
    if not isinstance(old_checks, list):
        return set(), ["Baseline check coverage is missing; absent findings are not verified."]
    old = {c["id"]: c for c in old_checks if isinstance(c, dict) and isinstance(c.get("id"), str)}
    comparable = set()
    for check in current["checks"]:
        if check["status"] not in {"passed", "findings"}:
            continue
        before = old.get(check["id"], {})
        scope = check.get("scopeDigest")
        if (isinstance(scope, str) and scope and scope == before.get("scopeDigest")
                and before.get("status") in {"passed", "findings"}):
            comparable.add(check["id"])
        else:
            reasons.append(f"Coverage changed or was incomplete for {check['id']}; absent findings are not verified.")
    for name, before in old.items():
        if (before.get("status") in {"passed", "findings"} and name not in comparable
                and not any(c["id"] == name and c["status"] in {"passed", "findings"}
                            for c in current["checks"])):
            reasons.append(f"Check {name} did not complete in this scan; absent findings are not verified.")
    return comparable, reasons
