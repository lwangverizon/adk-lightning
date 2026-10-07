"""Offline snapshot export and static portal assembly. Never a serving process.

Run export in an isolated host environment, not in a production container.
Assembly accepts reviewed JSON only and does not import agent code.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import yaml

from adkl import doctor, execution, skill_doc, skill_lint

MAX_BYTES = 16 * 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


def _json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Expected a regular JSON file no larger than 16 MiB")
    return json.loads(path.read_text(encoding="utf-8"))


def _source(root, value):
    path = Path(value)
    if (path.is_absolute() or not path.parts or any(
            p.startswith(".") or p in execution.SOURCE_EXCLUDED for p in path.parts)):
        raise ValueError("Inventory input must be a project source path")
    target = root / path
    if any(p.is_symlink() for p in [target, *target.parents] if p != root and root in p.parents):
        raise ValueError("Inventory inputs cannot traverse symlinks")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Inventory input escapes project")
    return target


def _inventory(root, doc, snapshot, include_instructions):
    skills, tools, agents, workflows = [], [], [], []
    def skill(value, owner):
        skills.append({"name": str(value["name"]), "owner": owner,
                       "description": str(value.get("description", "")),
                       "tools": value.get("tools", []),
                       "doc": skill_doc.SkillDoc.parse(value.get("instructions", "")).to_json()
                              if include_instructions else None,
                       "instructions": value.get("instructions", "") if include_instructions else None})

    def visit(agent, parent=""):
        owner = parent + "/" + agent["name"]
        agents.append({"name": agent["name"], "path": owner, "type": agent.get("type"),
                       "instruction": agent.get("instruction"), "callbacks": agent.get("callbacks", {})})
        for index, binding in enumerate(agent.get("adkl", [])):
            binding_owner = f"{owner} [binding {index}]"
            graphs = {graph["name"]: graph for graph in binding.get("workflowGraphs", [])}
            for capability in binding.get("capabilities", []):
                if capability.get("kind") == "workflow":
                    workflows.append({"name": capability["name"],
                        "description": capability["description"], "agent": agent["name"],
                        "owner": binding_owner, "operation": capability.get("operation"),
                        "graph": graphs.get(capability["name"])})
            for value in binding.get("skills", []):
                skill(value, binding_owner)
            for value in binding.get("tools", []):
                tools.append({key: value.get(key) for key in ("name", "description", "declaration", "scope")} | {"owner": binding_owner})
        for child in agent.get("children", []):
            visit(child, owner)

    if snapshot is not None:
        visit(snapshot["agent"])
        scope = "native_declarations"
    else:
        paths = dict(doc.get("authoring", {}).get("skills", {}))
        if not doc.get("agent", {}).get("factory"):
            cfg = doc.get("skills", {})
            directory = _source(root, cfg.get("dir", "skills"))
            include = set(cfg.get("include", []))
            for path in sorted(directory.glob("*/SKILL.md")):
                if not include or path.parent.name in include:
                    paths[path.parent.name] = path.relative_to(root).as_posix()
        for name, value in paths.items():
            path = _source(root, value)
            if path.name != "SKILL.md":
                raise ValueError("Skill input must name SKILL.md")
            fm, body = skill_lint.parse_frontmatter(path)
            skill({"name": fm.get("name", name), "description": fm.get("description", ""),
                   "tools": (fm.get("metadata") or {}).get("adk_additional_tools", []),
                   "instructions": body}, "saved_files")
        # Catalog loading/path checks belong to Doctor. Do not guess native tools
        # from Python or mistake mapped skill declarations for resolved inventory.
        scope = "saved_skill_files_only"
    return {"scope": scope, "agents": agents, "skills": skills, "tools": tools,
            "workflows": workflows,
            "instructionsIncluded": include_instructions,
            "limitations": ["Not a live process snapshot. Dynamic tools and instructions are not evaluated.",
                            "File-only exports do not resolve executable tool inventory."]}


def export(root, *, agent_id, name, artifact, sdk_revision, inspect=False,
           profile=None, include_instructions=False):
    """Collect a read-only bundle; artifact and SDK revision are publisher assertions."""
    if not ID.fullmatch(agent_id) or not name or not artifact or not sdk_revision:
        raise ValueError("Agent ID, display name, artifact identity and SDK revision are required")
    root = Path(root).resolve()
    before = execution.source_digest(root)
    doc = yaml.safe_load((root / "agent.yaml").read_text()) or {}
    report = doctor.diagnose(root, inspect=inspect, profile=profile, include_snapshot=True)
    snapshot = report.pop("inspectionSnapshot", None)
    inventory = _inventory(root, doc, snapshot, include_instructions)
    if before != execution.source_digest(root) or before != report["sourceDigest"]:
        raise ValueError("Project source changed during export; rerun against an immutable artifact")
    # Do not distribute the build machine's absolute workspace path.
    report["project"] = "."
    return {"schemaVersion": 1, "kind": "adkl_static_inspection",
            "agent": {"id": agent_id, "name": name},
            "identity": {"artifact": artifact, "sdkRevision": sdk_revision,
                         "provenance": "publisher_asserted", "deployment": "not_verified"},
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "report": report, "inventory": inventory,
            "capabilities": {"readOnly": True, "chat": False, "edit": False, "liveInspection": False}}


def _encoded(value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("Portal JSON exceeds 16 MiB")
    return data


def write_bundle(path, bundle):
    """Create, never overwrite, a private output file for review before publication."""
    data = _encoded(bundle)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)


def assemble(registry, out, *, ui_build=None):
    """Build a new immutable data directory from a trusted publisher registry.

    Registry bundle paths are local publisher inputs, never browser-supplied URLs.
    Entries without a bundle remain visible as unavailable; no fake snapshots.
    """
    registry, out = Path(registry).resolve(), Path(out).absolute()
    config = _json(registry)
    entries = config.get("agents") if isinstance(config, dict) else None
    if not isinstance(entries, list) or not entries or len(entries) > 200:
        raise ValueError("Registry requires 1–200 agents")
    if out.exists() or out.is_symlink():
        raise ValueError("Output already exists; build a new version")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".portal-", dir=out.parent))
    try:
        seen, published = set(), []
        for entry in entries:
            agent_id, name = entry.get("id", ""), entry.get("name", "")
            if not isinstance(agent_id, str) or not ID.fullmatch(agent_id) or agent_id in seen or not isinstance(name, str) or not name:
                raise ValueError("Registry agent IDs must be unique slugs with display names")
            seen.add(agent_id)
            row = {"id": agent_id, "name": name, "snapshot": None}
            if entry.get("bundle"):
                bundle = _json(registry.parent / entry["bundle"])
                if (bundle.get("schemaVersion") != 1 or bundle.get("kind") != "adkl_static_inspection"
                        or bundle.get("agent") != {"id": agent_id, "name": name}
                        or not isinstance(bundle.get("report", {}).get("findings"), list)
                        or not isinstance(bundle.get("report", {}).get("checks"), list)
                        or not isinstance(bundle.get("inventory", {}).get("skills"), list)
                        or not isinstance(bundle.get("inventory", {}).get("tools"), list)
                        or bundle.get("identity", {}).get("deployment") != "not_verified"):
                    raise ValueError("Bundle identity or schema does not match registry")
                data = _encoded(bundle)
                digest = hashlib.sha256(data).hexdigest()
                filename = f"{agent_id}-{digest}.json"
                (temp / filename).write_bytes(data)
                row.update(snapshot=filename, sha256=digest)
            published.append(row)
        publication = {"schemaVersion": 1, "agents": published}
        if ui_build is not None:
            if not isinstance(ui_build, str) or not re.fullmatch(r"[a-f0-9]{16}", ui_build):
                raise ValueError("UI build must be a 16-character source digest")
            publication["uiBuild"] = ui_build
        (temp / "registry.json").write_bytes(_encoded(publication))
        # Published data must be readable by the non-root static server.
        for path in temp.iterdir():
            path.chmod(0o644)
        temp.chmod(0o755)
        temp.rename(out)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("export", help="Run ONLY in an isolated host job")
    command.add_argument("project", type=Path)
    command.add_argument("--id", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--artifact", required=True, help="Exact artifact digest or source revision; publisher assertion")
    command.add_argument("--sdk-revision", required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--inspect", action="store_true", help="Construct trusted native factory in worker; may have side effects")
    command.add_argument("--profile")
    command.add_argument("--include-instructions", action="store_true", help="Include potentially sensitive skill bodies")
    command = commands.add_parser("assemble")
    command.add_argument("registry", type=Path)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--ui-build", help="Compiled portal source digest")
    command.add_argument("--approve-content", action="store_true", required=True,
                         help="Confirm manual review of all published content; no automatic secret redaction")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            write_bundle(args.out, export(args.project, agent_id=args.id, name=args.name,
                artifact=args.artifact, sdk_revision=args.sdk_revision, inspect=args.inspect,
                profile=args.profile, include_instructions=args.include_instructions))
        else:
            assemble(args.registry, args.out, ui_build=args.ui_build)
    except (OSError, ValueError, TypeError, KeyError, execution.ExecutionError):
        parser.exit(2, "portal: invalid or changing inputs; review paths, schema and identity locally\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
