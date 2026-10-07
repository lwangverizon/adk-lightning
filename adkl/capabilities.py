"""Execution support, separate from valid feature configuration.

This is an inventory of the current preview/export implementations, not a
certification of an agent or its external tools. New features default to
unsupported until their target implementation and integration tests exist.
"""
from __future__ import annotations

from . import features


def manifest(resolved: features.Resolved, target: str = "preview") -> dict:
    """Report requested, implemented and partial features without running code."""
    if target not in {"preview", "sdk", "export"}:
        raise ValueError(f"unknown execution target: {target}")
    entries = []
    for name in sorted(features.REGISTRY):
        if target in {"preview", "sdk"}:
            spec = features.REGISTRY[name]
            status, detail = spec.support_status, spec.support_detail
        else:
            status, detail = ("unsupported",
                "Export emits configuration only; this feature is not wired into the generated agent.")
        entries.append({"name": name, "requested": name in resolved.active,
                        "status": status, "detail": detail})
    blockers = [f"{e['name']}: {e['detail']}" for e in entries
                if e["requested"] and e["status"] == "unsupported"]
    warnings = [f"{e['name']}: {e['detail']}" for e in entries
                if e["requested"] and e["status"] == "partial"]
    return {"target": target, "features": entries, "blockers": blockers,
            "warnings": warnings, "canRun": not blockers,
            "complete": not blockers and not warnings}


def require_supported(resolved: features.Resolved, target: str = "preview") -> dict:
    report = manifest(resolved, target)
    if report["blockers"]:
        raise features.FeatureError(
            f"{target} blocked by unsupported features:\n"
            + "\n".join(report["blockers"]))
    return report


def export_report(doc: dict) -> dict:
    """Full export contract, including capabilities outside features.pipeline."""
    report = manifest(features.resolve(doc.get("features")), "export")
    checks = [
        ((doc.get("agent") or {}).get("factory"),
         "host factory: deploy through the existing host application; this descriptor is for inspection."),
        ((doc.get("skills") or {}).get("include"),
         "skills: exported skills are loaded but not attached to the agent."),
        (doc.get("presentation"), "presentation: pack tools and conversion are not wired into export."),
        ((doc.get("tools") or {}).get("core") or (doc.get("tools") or {}).get("repo"),
         "tools: executable tool bindings are not exported."),
        (doc.get("widgets"), "widgets: emission callbacks are not exported."),
        ((doc.get("agent") or {}).get("generation"),
         "generation: planner and generation settings are not exported."),
    ]
    report["blockers"].extend(message for requested, message in checks if requested)
    report["canRun"] = report["complete"] = not report["blockers"]
    return report
