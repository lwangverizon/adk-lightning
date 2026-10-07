"""Offline agent audit: the configured prompt, skills, tool contracts and costs.

Uses the same rule functions as the editor. Heuristic findings are advisory;
this report is not live model evaluation or production certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

import yaml

from adkl import agent_schema, capabilities, features, instruction, presentation, skill_lint


def audit(root: pathlib.Path | str) -> dict:
    root = pathlib.Path(root).resolve()
    doc = yaml.safe_load((root / "agent.yaml").read_text(encoding="utf-8")) or {}
    errors, warnings = agent_schema.validate(doc, root)
    if (doc.get("agent") or {}).get("factory"):
        return {"version": 1, "passed": not errors, "evidence": "host_descriptor_only",
                "behavioralEvaluation": "not_run", "skills": [],
                "findings": [{"severity": "ERROR", "rule": "CONFIG", "message": e} for e in errors]
                    + [{"severity": "WARN", "rule": "HOST_FACTORY", "message":
                        "Host Python instructions/tools require runtime inspection; static skill audits do not apply."}]}
    findings = [{"severity": "ERROR", "rule": "CONFIG", "skill": None, "message": e}
                for e in errors]
    findings += [{"severity": "WARN", "rule": "CONFIG", "skill": None, "message": w}
                 for w in warnings]
    try:
        compiled = instruction.compile_config(root, doc)
        path = (doc.get("tools") or {}).get("catalog")
        catalog = json.loads((root / path).read_text()) if path else {}
        catalog = presentation.extend_catalog(catalog, doc)
        cfg = doc.get("skills") or {}
        sdir = root / cfg.get("dir", "skills")
        raw, skills, _, _ = skill_lint.lint(sdir, None, set(cfg.get("include", [])), catalog, compiled.text,
                                                     (doc.get("tools") or {}).get("core") or [])
        for sev, rule, skill, message in raw:
            findings.append({"severity": sev, "rule": rule, "skill": skill, "message": message,
                             "source": str(sdir / skill / "SKILL.md") if skill else None})
        summaries = []
        for name, directory, fm, body, declared in skills:
            # These are explicit review prompts, not inferred violations.
            summaries.append({"name": name, "procedureTokensEstimate": len(body) // 4,
                              "tools": declared, "review": [
                                  "Does the description distinguish adjacent intents?",
                                  "Are completion and error recovery explicit?",
                                  "Can fixed tool sequencing avoid another model call?"],
                              "source": str(directory / "SKILL.md")})
        manifest = capabilities.manifest(features.resolve(doc.get("features")), "sdk")
        findings.extend({"severity": "ERROR", "rule": "SDK_UNSUPPORTED", "skill": None,
                         "message": blocker} for blocker in manifest["blockers"])
        digest = hashlib.sha256()
        inputs = [root / "agent.yaml", *sorted(sdir.rglob("*.md"))]
        digest.update(compiled.text.encode())
        digest.update(json.dumps(catalog, sort_keys=True).encode())
        for item in inputs:
            digest.update(str(item.relative_to(root)).encode())
            digest.update(item.read_bytes())
        return {"version": 1, "sourceDigest": digest.hexdigest(), "findings": findings,
                "skills": summaries, "runtime": manifest,
                "promptTokensEstimate": len(compiled.text) // 4,
                "passed": not any(f["severity"] == "ERROR" for f in findings),
                "evidence": "static", "behavioralEvaluation": "not_run"}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        findings.append({"severity": "ERROR", "rule": "AUDIT_INPUT", "skill": None, "message": str(exc)})
        return {"version": 1, "passed": False, "findings": findings,
                "evidence": "static", "behavioralEvaluation": "not_run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=pathlib.Path)
    args = parser.parse_args()
    report = audit(args.project.parent if args.project.is_file() else args.project)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
