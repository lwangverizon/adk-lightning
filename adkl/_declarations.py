"""Bounded native declaration projection for embedded inspection.

Storage reads only, except exact native FunctionTool schema construction during
host-triggered preparation. Never call prompts, toolsets, callbacks or tools.
This is an internal projection of pinned native contracts, not object export.
"""
from __future__ import annotations

import json
from types import MethodType

MAX_BYTES = 4 * 1024 * 1024
MAX_ITEMS = 256
MAX_NODES = 128
MAX_DEPTH = 16


def stored(value, name, default=None):
    try:
        data = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        return default
    return data.get(name, default) if type(data) is dict else default


class Collector:
    def __init__(self):
        self.nodes = []
        self.seen = {}
        self.characters = 0
        self.item_count = 0

    def text(self, value):
        if type(value) is not str or len(value) > 512 * 1024:
            raise ValueError("Invalid declaration text")
        self.characters += len(value)
        if self.characters > MAX_BYTES:
            raise ValueError("Declaration text budget exceeded")
        return value

    def items(self, value):
        if type(value) not in (dict, list, tuple) or len(value) > MAX_ITEMS:
            raise ValueError("Unsupported or oversized declaration container")
        self.item_count += len(value)
        if self.item_count > 8192:
            raise ValueError("Declaration count exceeded")
        return list(value.values()) if type(value) is dict else list(value)

    def instruction(self, value):
        if type(value) is str:
            return {"kind": "static", "text": self.text(value)}
        return {"kind": "absent" if value is None else "dynamic", "evaluated": False}

    def skill(self, skill, owner, index):
        frontmatter = stored(skill, "frontmatter")
        metadata = stored(frontmatter, "metadata", {})
        tools = metadata.get("adk_additional_tools", []) if type(metadata) is dict else []
        return {
            "id": f"{owner}/skills/{index}", "owner": owner,
            "name": self.text(stored(frontmatter, "name", "unknown")),
            "description": self.text(stored(frontmatter, "description", "")),
            "instructions": self.instruction(stored(skill, "instructions")),
            "requestedTools": [self.text(name) for name in self.items(tools)],
            "resources": "omitted; build artifact required",
            "source": "loaded declaration; source mapping unavailable", "editable": False,
        }

    def tool(self, tool, owner, index, depth):
        from google.adk.tools.function_tool import FunctionTool

        result = {
            "id": f"{owner}/tools/{index}", "owner": owner,
            "name": self.text(stored(tool, "name", type(tool).__name__)),
            "description": self.text(stored(tool, "description", "")),
            "type": type(tool).__name__, "schema": None, "schemaStatus": "not_collected",
        }
        # Subclasses may run arbitrary declaration providers; do not dispatch them.
        if type(tool) is FunctionTool:
            declaration = FunctionTool._get_declaration(tool)
            if declaration is not None:
                schema = declaration.model_dump(mode="json", exclude_none=True)
                self.text(json.dumps(schema, ensure_ascii=True, allow_nan=False))
                result.update(schema=schema, schemaStatus="native_function_declaration")
        nested = stored(tool, "agent")
        if nested is not None:
            result["agent"] = self.agent(nested, depth + 1)
        return result

    def toolset(self, toolset, owner, index, depth):
        from google.adk.tools.skill_toolset import SkillToolset

        key = f"{owner}/toolsets/{index}"
        result = {"id": key, "owner": owner, "type": type(toolset).__name__,
                  "availability": "dynamic; not evaluated", "skills": [], "tools": []}
        if not isinstance(toolset, SkillToolset):
            result["omission"] = "Unsupported toolset; get_tools was not called"
            return result
        result["skills"] = [self.skill(skill, key, i) for i, skill in
                            enumerate(self.items(stored(toolset, "_skills", {})))]
        declared = self.items(stored(toolset, "_tools", [])) + self.items(
            stored(toolset, "_provided_tools_by_name", {}))
        # Native skill toolsets can store the same tool in both inventories.
        declared = list({id(tool): tool for tool in declared}.values())
        if len(declared) > MAX_ITEMS:
            raise ValueError("Tool count exceeded")
        result["tools"] = [self.tool(tool, key, i, depth) for i, tool in enumerate(declared)]
        if stored(toolset, "_provided_toolsets", []):
            result["omission"] = "Nested dynamic toolsets not enumerated"
        return result

    def agent(self, agent, depth=0):
        from google.adk.agents import BaseAgent
        from google.adk.tools.base_toolset import BaseToolset
        from google.adk.workflow import BaseNode
        from adkl.bindings import RuntimeBindings
        from adkl.capability_bindings import CapabilityBindings

        if not isinstance(agent, (BaseAgent, BaseNode)):
            raise ValueError("Expected native agent or workflow node")
        if id(agent) in self.seen:
            return self.seen[id(agent)]
        if depth > MAX_DEPTH or len(self.nodes) >= MAX_NODES:
            raise ValueError("Agent graph exceeds bounds")
        key = f"agents/{len(self.nodes)}"
        self.seen[id(agent)] = key
        model = stored(agent, "model")
        node = {
            "id": key, "name": self.text(stored(agent, "name", "unknown")),
            "type": type(agent).__name__, "description": self.text(stored(agent, "description", "")),
            "instruction": self.instruction(stored(agent, "instruction")),
            "globalInstruction": self.instruction(stored(agent, "global_instruction")),
            "model": self.text(model) if type(model) is str else {
                "type": type(model).__name__, "configuration": "omitted"},
            "generation": {}, "children": [], "tools": [], "toolsets": [], "bindings": [],
        }
        self.nodes.append(node)
        config = stored(agent, "generate_content_config")
        for field in ("temperature", "top_p", "top_k", "max_output_tokens"):
            value = stored(config, field)
            if type(value) in (int, float, bool):
                node["generation"][field] = value
        children = self.items(stored(agent, "sub_agents", []))
        # Native Workflow edges include branch maps and join collections. Export
        # node reachability only; this v1 protocol does not express graph routes.
        def edge_nodes(value, level=0):
            if level > MAX_DEPTH:
                raise ValueError("Workflow edge depth exceeded")
            if type(value) is str:
                return
            if type(value) in (list, tuple, dict):
                for item in self.items(value):
                    edge_nodes(item, level + 1)
            else:
                children.append(value)
        for edge in self.items(stored(agent, "edges", [])):
            edge_nodes(edge)
        attachments = self.items(stored(agent, "_adkl_inspection_bindings", ()))
        callbacks = stored(agent, "before_agent_callback", [])
        if callbacks is None:
            callbacks = []
        if type(callbacks) not in (list, tuple):
            callbacks = [callbacks]
        for callback in self.items(callbacks):
            if type(callback) is MethodType and isinstance(callback.__self__, RuntimeBindings):
                attachments.append(callback.__self__)
        for i, tool in enumerate(self.items(stored(agent, "tools", []))):
            runtime = stored(tool, "runtime")
            if isinstance(runtime, RuntimeBindings):
                attachments.append(runtime)
            if isinstance(tool, BaseToolset):
                node["toolsets"].append(self.toolset(tool, key, i, depth))
            else:
                node["tools"].append(self.tool(tool, key, i, depth))
        for i, binding in enumerate(self.items({id(b): b for b in attachments})):
            if not isinstance(binding, RuntimeBindings):
                raise ValueError("Unsupported inspection attachment")
            group = f"{key}/bindings/{i}"
            if isinstance(binding, CapabilityBindings):
                owner = "host" if stored(binding, "_host_skill_toolset") is not None else "adkl"
                children.extend(self.items(stored(binding, "_workflows", ())))
                children.extend(self.items(stored(binding, "_agents", ())))
            else:
                owner = "host" if (stored(binding, "_native_toolset") is not None
                                   or stored(binding, "_inspection_only", False)) else "adkl"
            node["bindings"].append({
                "id": group, "owner": key, "skillsOwner": owner,
                "configuration": "omitted; build artifact required",
                "skills": [self.skill(skill, group, j) for j, skill in
                           enumerate(self.items(stored(binding, "_skills", {})))],
                "tools": [self.tool(tool, group, j, depth) for j, tool in
                          enumerate(self.items(stored(binding, "_tool_fns", {})))],
            })
        if len(children) > MAX_ITEMS:
            raise ValueError("Child count exceeded")
        node["children"] = list(dict.fromkeys(self.agent(child, depth + 1) for child in children))
        return key


def collect(root):
    """Project one explicit native root (or App), never discover/import a host."""
    from google.adk.apps import App

    if isinstance(root, App):
        root = stored(root, "root_agent")
    collector = Collector()
    root_id = collector.agent(root)
    return root_id, collector.nodes
