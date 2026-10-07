"""Read native ADKL declarations without running domain tools or prompt callables."""
from __future__ import annotations

from adkl import capabilities, features


def bindings(agent):
    """Find explicit attachments and issued bindings without enumerating toolsets."""
    from adkl.skill_runtime import SelectedSkillToolset
    from adkl.bindings import RuntimeBindings
    found = list(getattr(agent, "_adkl_inspection_bindings", ()))
    found.extend(tool.runtime for tool in getattr(agent, "tools", [])
                 if isinstance(tool, SelectedSkillToolset))
    callbacks = getattr(agent, "before_agent_callback", None) or []
    for callback in callbacks if isinstance(callbacks, (list, tuple)) else [callbacks]:
        owner = getattr(callback, "__self__", None)
        if isinstance(owner, RuntimeBindings):
            found.append(owner)
    return list({id(binding): binding for binding in found}.values())


def workflow_graph(workflow):
    """Project declared native topology without executing nodes or resolving tools."""
    graph = workflow.graph
    return {"name": workflow.name, "available": graph is not None,
            "nodes": [{"name": node.name, "kind": type(node).__name__,
                       "description": node.description} for node in graph.nodes]
                     if graph is not None else [],
            "edges": [{"from": edge.from_node.name, "to": edge.to_node.name,
                       "route": edge.route} for edge in graph.edges]
                     if graph is not None else []}


def attach(agent, *, skills, tools=(), workflows=()):
    """Attach explicit host declarations for inspection only; return the agent.

    Does not enumerate dynamic toolsets, install callbacks, or replace execution.
    Workflows are explicit host declarations, not discovered closures or proof
    that the host exposes them for execution. Graphs are projected at snapshot.
    Repeated attachment is refused; create a fresh factory result to refresh it.
    Declarations are trusted host metadata, not proof of tool availability.
    """
    from adkl.bindings import RuntimeBindings
    if bindings(agent):
        raise ValueError("Agent already has ADKL declarations")
    declaration = RuntimeBindings(skills=skills, tools=tools, feature_config={"pipeline": []})
    declaration._inspection_only = True
    declaration._inspection_workflows = tuple(workflows)
    object.__setattr__(agent, "_adkl_inspection_bindings", (declaration,))
    return agent


def snapshot(runtime):
    from adkl.capability_bindings import CapabilityBindings
    resolved = features.resolve(runtime.doc.get("features"))
    return {
        **(runtime._capability_snapshot() if isinstance(runtime, CapabilityBindings) else {
            "capabilities": [{"name": workflow.name, "description": workflow.description,
                              "kind": "workflow", "operation": None}
                             for workflow in getattr(runtime, "_inspection_workflows", ())],
            "workflowGraphs": [workflow_graph(workflow)
                               for workflow in getattr(runtime, "_inspection_workflows", ())],
        }),
        "schemaVersion": 1,
        "executionOwnership": {
            "skills": "host" if (getattr(runtime, "_native_toolset", None) is not None
                                  or getattr(runtime, "_inspection_only", False)) else "adkl",
            "presentation": "adkl" if runtime._widgets_active else "host",
        },
        "evidence": "declarations",
        "features": list(resolved.active), "configuration": resolved.config,
        # `configuration` reports all features, active or not, so a reader cannot
        # tell a live setting from an inert one without cross-referencing
        # `features`. These are the resolver's own objections to this pairing.
        "warnings": list(resolved.warnings) + list(
            capabilities.manifest(resolved, "sdk")["warnings"]),
        "presentation": runtime.doc.get("presentation"),
        "domainRouting": getattr(runtime, "_route_skill", None) is not None,
        "seedToday": getattr(runtime, "_seed_today", True),
        "finalModelTransforms": getattr(runtime, "_final_model_transform_count", 0),
        "contractInjection": bool(runtime._widget_cfg.get("inject_contract", True)
                                  and runtime._widgets_active),
        "delivery": "host_sink" if getattr(runtime, "_widget_sink", None) else "adk_events",
        # A separate axis from the widget's own delivery, and worth naming: with a
        # host sink the companion never joins the transcript, so a console reading
        # only the reply text sees a widget with no sentence and no reason why.
        "companionDelivery": ("host_sink" if getattr(runtime, "_companion_sink", None)
                              else "model_content"),
        "skills": [{"name": skill.name, "description": skill.frontmatter.description,
                    "instructions": skill.instructions,
                    "metadata": {key: skill.frontmatter.metadata[key]
                                 for key in ("adk_exclude_from_matching", "adkl_presentation")
                                 if key in skill.frontmatter.metadata},
                    "tools": skill.frontmatter.metadata.get("adk_additional_tools", [])}
                   for skill in runtime._skills.values()],
        "tools": [{"name": name, "description": getattr(tool, "description", ""),
                   "declaration": declaration(tool),
                   "scope": "core" if (name in runtime.doc.get("tools", {}).get("core", [])
                                       or (isinstance(runtime, CapabilityBindings)
                                           and any(t.name == name for t in runtime._tools)))
                   else "presentation" if name in runtime._presentation_tools else "skill"}
                  for name, tool in runtime._tool_fns.items()],
        # The reply contract is what the staged pipeline exists for, so a
        # snapshot without `role` cannot answer the question a reviewer brings to
        # it: why a card arrived alongside a reply that restates the card. An
        # adopted spec's payload type is decided by its build rather than
        # declared, and `null` says that, where `""` read as an empty type.
        "widgets": [{"key": spec.key, "register": spec.register,
                     "payloadType": spec.payload_type or None,
                     "adoptionWarnings": list(spec.adoption_warnings),
                     "role": spec.role.value if spec.role else
                             ("dynamic" if spec.resolve_role else None)}
                    for spec in runtime._widget_specs],
    }


def declaration(tool):
    """Only inspect declared native tools; never enumerate arbitrary dynamic toolsets."""
    result = tool._get_declaration()
    return result.model_dump(mode="json", exclude_none=True) if result is not None else None


def audit_snapshot(snapshot, instruction):
    """Reuse static content rules on declared native content; never resolve a callable."""
    import tempfile
    from pathlib import Path
    import yaml
    from adkl import skill_lint
    with tempfile.TemporaryDirectory(prefix="adkl-native-audit-") as directory:
        root = Path(directory)
        for index, skill in enumerate(snapshot["skills"]):
            # Directory names are local indices; native names are untrusted paths.
            target = root / str(index)
            target.mkdir()
            frontmatter = {"name": skill["name"], "description": skill["description"],
                           "metadata": {**(skill.get("metadata") or {}),
                                        "adk_additional_tools": skill["tools"]}}
            (target / "SKILL.md").write_text("---\n" + yaml.safe_dump(frontmatter)
                                           + "---\n" + skill["instructions"], encoding="utf-8")
        catalog = {"tools": snapshot["tools"]}
        core = [tool["name"] for tool in snapshot["tools"]
                if tool["scope"] in {"core", "presentation"}]
        raw, _, _, _ = skill_lint.lint(root, None, None, catalog,
            instruction if isinstance(instruction, str) else "", core,
            logical_names={str(index): skill["name"] for index, skill in enumerate(snapshot["skills"])})
    adoption_findings = [{"severity": "WARN", "rule": "WIDGET_ADOPTION_LOSS", "skill": widget["key"],
                         "message": message}
                        for widget in snapshot.get("widgets", [])
                        for message in widget.get("adoptionWarnings", [])]
    return {"evidence": "native_declarations", "behavioralEvaluation": "not_run",
            "dynamicInstructionEvaluated": False,
            "limitations": [] if isinstance(instruction, str) else ["Dynamic instruction was not evaluated"],
            "passed": not any(severity == "ERROR" for severity, *_ in raw),
            "findings": [{"severity": severity, "rule": rule, "skill": skill, "message": message}
                         for severity, rule, skill, message in raw] + adoption_findings}
