"""Source-backed authoring for explicitly mapped native skills.

The server reads only declared host files; native construction stays in its worker.
"""
from copy import deepcopy
import hashlib
import os
import re
from pathlib import Path
import tempfile

import yaml
from adkl import skill_doc, skill_lint
from adkl.inspection import audit_snapshot


def _parameter_type(schema):
    value = schema.get("type", "")
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    return str(value)


def source_path(root, value):
    root = Path(root).resolve()
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("Skill source must be a relative SKILL.md path")
    path = root / value
    if ".." in Path(value).parts or path.name != "SKILL.md":
        raise ValueError("Skill source must stay inside the host and name SKILL.md")
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
        raise ValueError("Skill source must not traverse symlinks")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Skill source escapes host")
    return path


class Workspace:
    def __init__(self, root, doc, report):
        self.root, self.doc = Path(root), doc
        self.skills, self.tools, self.bindings = {}, {}, []
        self.workflows = []
        def visit(agent):
            for binding in agent.get("adkl", []):
                self.bindings.append((binding, agent))
                graphs = {graph["name"]: graph for graph in binding.get("workflowGraphs", [])}
                for capability in binding.get("capabilities", []):
                    if capability.get("kind") == "workflow":
                        self.workflows.append({"name": capability["name"],
                            "description": capability["description"],
                            "operation": capability["operation"],
                            "agent": agent["name"],
                            "graph": graphs.get(capability["name"])})
                for skill in binding["skills"]:
                    name = skill["name"]
                    if name in self.skills:
                        raise ValueError("Authoring needs unique skill names across the native agent tree")
                    self.skills[name] = skill
                for tool in binding["tools"]:
                    self.tools.setdefault(tool["name"], tool)
            for child in agent.get("children", []):
                visit(child)
        visit(report["agent"])

    def mapping_findings(self):
        """Reconcile optional editor mappings with the current native inventory."""
        findings = [{"severity": "INFO", "rule": "AUTHORING_SOURCE_UNMAPPED",
                     "skill": name,
                     "message": "Native skill has no authoring.skills source mapping; inspection works but editing is unavailable"}
                    for name in sorted(set(self.skills) - set(self.doc.get("authoring", {}).get("skills", {})))]
        for name in self.doc.get("authoring", {}).get("skills", {}):
            if name not in self.skills:
                message = "Mapped skill is absent from the native factory; update authoring.skills after renaming or removing it"
            else:
                try:
                    self.detail(name)
                    continue
                except (ValueError, OSError) as exc:
                    message = str(exc)
            findings.append({"severity": "ERROR", "rule": "AUTHORING_SOURCE_DRIFT",
                             "skill": name, "message": message})
        return findings

    def findings(self, replacement=None):
        findings = []
        for binding, agent in self.bindings:
            if replacement and replacement["name"] in {s["name"] for s in binding["skills"]}:
                changed = deepcopy(binding)
                changed["skills"] = [replacement if s["name"] == replacement["name"] else s for s in binding["skills"]]
                # Inspect never evaluates dynamic instructions. Preserve that limit.
                findings.extend(audit_snapshot(changed, agent.get("staticInstruction"))["findings"])
            else:
                findings.extend(binding.get("audit", {}).get("findings", []))
        return findings

    def diagnostic_findings(self):
        """Saved native declarations, mappings and explicit empty inventories."""
        findings = self.findings() + self.mapping_findings()
        for binding, _ in self.bindings:
            if not binding["tools"]:
                for skill in binding["skills"]:
                    for tool in skill["tools"]:
                        findings.append({"rule": "TOOL_UNRESOLVED", "severity": "ERROR", "skill": skill["name"],
                                         "message": f"Declared tool {tool!r} is absent from the inspected binding."})
        return findings

    def detail(self, name):
        skill = self.skills[name]
        value = self.doc.get("authoring", {}).get("skills", {}).get(name)
        revision, path = None, ""
        editable = value is not None
        if editable:
            target = source_path(self.root, value)
            raw = target.read_bytes()
            fm, body = skill_lint.parse_frontmatter(target)
            if fm.get("name") != name:
                raise ValueError("Mapped skill source name differs from native declaration")
            if (body.strip() != skill["instructions"].strip()
                    or fm.get("description", "").strip() != skill["description"].strip()
                    or (fm.get("metadata") or {}).get("adk_additional_tools", []) != skill["tools"]):
                raise ValueError("Mapped source differs from the native skill; reload its factory or correct the mapping")
            revision, path = hashlib.sha256(raw).hexdigest(), value
        findings = [f for f in self.findings() if f["skill"] == name]
        return {**skill, "body": skill["instructions"], "path": path, "overridden": False,
                "editable": editable, "revision": revision,
                "doc": skill_doc.SkillDoc.parse(skill["instructions"]).to_json(),
                # A skill's instruction is the material selected into a turn.
                # Its tools have independent declaration costs in the tool UI.
                "estTokens": len(skill["instructions"]) // 4,
                "findings": findings,
                "errors": sum(f["severity"] == "ERROR" for f in findings),
                "warnings": sum(f["severity"] == "WARN" for f in findings)}

    def catalog(self):
        result = []
        for tool in self.tools.values():
            declaration = tool.get("declaration") or {}
            schema = declaration.get("parameters_json_schema")
            if not isinstance(schema, dict):
                schema = declaration.get("parameters")
            known = isinstance(schema, dict)
            schema = schema if known else {}
            result.append({"name": tool["name"], "summary": tool["description"],
                "sideEffect": "unknown", "sideEffectInferred": False, "requiresAuth": False,
                "source": "native_factory", "scope": tool.get("scope", "unknown"),
                "schemaKnown": known,
                "estTokens": len(str(tool.get("declaration"))) // 4,
                "params": [{"name": name, "type": _parameter_type(value),
                            "description": value.get("description", ""), "required": name in schema.get("required", [])}
                           for name, value in schema.get("properties", {}).items()]})
        return {"tools": result, "warnings": []}

    def edit(self, name, payload, *, save=False):
        detail = self.detail(name)
        if not detail["editable"]:
            raise ValueError("Register this skill's host source in authoring.skills before editing")
        target = source_path(self.root, detail["path"])
        before = target.read_bytes()
        if save and payload.get("revision") != hashlib.sha256(before).hexdigest():
            raise ValueError("Source changed since it was opened; reload before saving")
        fm, original_body = skill_lint.parse_frontmatter(target)
        description, tools = payload.get("description"), payload.get("tools")
        if not isinstance(description, str) or not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            raise ValueError("Description and tool bindings are required")
        if set(tools) - self.tools.keys():
            raise ValueError("Skill refers to a tool unavailable in its native binding")
        owner = next(binding for binding, _ in self.bindings if any(s["name"] == name for s in binding["skills"]))
        if set(tools) - {t["name"] for t in owner["tools"]}:
            raise ValueError("Tool belongs to another agent binding")
        body = payload.get("body", "")
        if payload.get("doc") is not None:
            body = skill_doc.SkillDoc.from_json(payload["doc"], skill_doc.SkillDoc.parse(original_body)).render()
        if not isinstance(body, str):
            raise ValueError("Skill body must be text")
        replacement = {"name": name, "description": description, "tools": tools, "instructions": body}
        findings = self.findings(replacement)
        mine = [f for f in findings if f["skill"] == name]
        result = {"findings": mine, "allFindings": findings,
                  "estTokens": len(body) // 4}
        if not save:
            return result
        if any(f["severity"] == "ERROR" for f in mine) and not payload.get("force"):
            return {**result, "saved": False, "error": "Resolve lint errors before saving"}
        same = (description == fm.get("description") and body.strip() == original_body.strip()
                and tools == (fm.get("metadata") or {}).get("adk_additional_tools", []))
        if not same:
            same_frontmatter = (description == fm.get("description")
                and tools == (fm.get("metadata") or {}).get("adk_additional_tools", []))
            fm["description"] = description
            fm.setdefault("metadata", {})["adk_additional_tools"] = tools
            text = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body
            prefix = re.match(r"\A(---[^\n]*\n.*?\n---[^\n]*\n)", before.decode(), re.S)
            if same_frontmatter and prefix:
                text = prefix[1] + body
            if target.read_bytes() != before:
                raise ValueError("Source changed during validation; reload before saving")
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text.encode())
            try:
                os.chmod(temporary, target.stat().st_mode)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return {**result, "saved": True, "path": detail["path"], "staleEmbeddings": not same}
