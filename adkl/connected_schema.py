"""Closed disclosure schema for loaded declarations, not generic object export.

Unknown fields are refused, including nested schema extensions. Free text and
schema literal values still need publisher disclosure review; this is not a
secret detector. No imports of host code or runtime declaration providers.
"""
from __future__ import annotations

import json
import math

MAX_BYTES = 4 * 1024 * 1024


def document(body: bytes):
    if len(body) > MAX_BYTES:
        raise ValueError("Document too large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Nonfinite JSON number")

    data = json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    pending, count = [(data, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if count > 100000 or depth > 32:
            raise ValueError("Document structure exceeds limits")
        if type(value) is str and len(value) > 512 * 1024:
            raise ValueError("Text exceeds limit")
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Nonfinite JSON number")
        if type(value) is dict:
            pending.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            pending.extend((v, depth + 1) for v in value)
    return data


def fields(value, required, optional=()):
    if (type(value) is not dict or not set(required) <= value.keys()
            or value.keys() - set(required) - set(optional)):
        raise ValueError("Missing or undisclosed fields")


def text(value, *, nonempty=False):
    if type(value) is not str or (nonempty and not value):
        raise ValueError("Invalid text")


def rows(value, limit=256):
    if type(value) is not list or len(value) > limit:
        raise ValueError("Invalid list")
    return value


def strings(value):
    for item in rows(value):
        text(item)


def instruction(value):
    fields(value, {"kind"}, {"text", "evaluated"})
    if value["kind"] == "static":
        fields(value, {"kind", "text"})
        text(value["text"])
    elif value["kind"] in {"dynamic", "absent"}:
        fields(value, {"kind", "evaluated"})
        if value["evaluated"] is not False:
            raise ValueError("Instructions must not be evaluated")
    else:
        raise ValueError("Unknown instruction kind")


def schema(value):
    """Supported GenAI/OpenAPI and JSON Schema subset; extensions fail closed.

    Property names and enum/default/const values are declared schema content,
    not host debug/config fields. External $refs are refused; nothing resolved.
    """
    if type(value) is bool:
        return
    string_keys = {"type", "format", "description", "title", "$ref", "pattern"}
    bool_keys = {"nullable", "readOnly", "writeOnly", "uniqueItems"}
    number_keys = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                   "multipleOf", "minLength", "maxLength", "minItems", "maxItems",
                   "minProperties", "maxProperties"}
    allowed = string_keys | bool_keys | number_keys | {
        "properties", "$defs", "defs", "definitions", "items", "additionalProperties",
        "anyOf", "oneOf", "allOf", "prefixItems", "not", "required", "propertyOrdering",
        "enum", "const", "default", "example", "examples"}
    fields(value, (), allowed)
    for key, item in value.items():
        if key in string_keys:
            # JSON schema permits a list for type (GenAI uses strings).
            if key == "type" and type(item) is list:
                strings(item)
            else:
                text(item)
            if key == "$ref" and not item.startswith("#/"):
                raise ValueError("External schema reference")
        elif key in bool_keys:
            if type(item) is not bool:
                raise ValueError("Invalid schema boolean")
        elif key in number_keys:
            # Native GenAI serializes some int64 constraints as decimal strings.
            if type(item) not in (int, float) and not (
                    type(item) is str and item.isdecimal()):
                raise ValueError("Invalid schema number")
        elif key in {"properties", "$defs", "defs", "definitions"}:
            if type(item) is not dict or len(item) > 256:
                raise ValueError("Invalid schema map")
            for child in item.values():
                schema(child)
        elif key in {"items", "additionalProperties", "not"}:
            schema(item)
        elif key in {"anyOf", "oneOf", "allOf", "prefixItems"}:
            for child in rows(item):
                schema(child)
        elif key in {"required", "propertyOrdering"}:
            strings(item)
        elif key in {"enum", "examples"}:
            rows(item)
        # default/const/example are literal JSON values, already globally bounded.


def function_schema(value):
    fields(value, {"name"}, {"description", "parameters", "parametersJsonSchema",
                           "parameters_json_schema", "response", "responseJsonSchema",
                           "response_json_schema", "behavior"})
    text(value["name"], nonempty=True)
    for key, item in value.items():
        if key in {"name", "description", "behavior"}:
            text(item)
        else:
            schema(item)


def declarations(data):
    fields(data, {"schemaVersion", "kind", "agentId", "environment", "instanceId",
                  "release", "generation", "capturedAt", "scope", "root", "agents",
                  "capabilities", "checks", "limitations"}, {"releaseProvenance"})
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise ValueError("Invalid version")
    if data["kind"] != "adkl_loaded_declarations":
        raise ValueError("Invalid kind")
    for key in ("agentId", "environment", "instanceId", "release", "generation",
                "capturedAt", "scope", "root"):
        text(data[key], nonempty=True)
        if len(data[key]) > 1024:
            raise ValueError("Identity too long")
    if data.get("releaseProvenance", "host_asserted") != "host_asserted":
        raise ValueError("Unsupported provenance")
    fields(data["capabilities"], {"chat", "edit", "sessions"})
    if any(v is not False for v in data["capabilities"].values()):
        raise ValueError("Mutation capability refused")
    if data["checks"] != {"doctor": "not_run", "behavioral": "not_run"}:
        raise ValueError("Adapter checks must not imply execution")
    strings(data["limitations"])
    nodes = rows(data["agents"], 128)
    if not nodes:
        raise ValueError("Empty agent graph")
    ids, agent_ids, references = set(), set(), []

    def identity(row, owner=None):
        key = row["id"]
        text(key, nonempty=True)
        if len(key) > 1024 or key in ids:
            raise ValueError("Duplicate/invalid declaration id")
        ids.add(key)
        if owner is not None and row["owner"] != owner:
            raise ValueError("Declaration owner does not match container")
        return key

    def tools(items, owner):
        for tool in rows(items):
            fields(tool, {"id", "owner", "name", "description", "type", "schema",
                          "schemaStatus"}, {"agent"})
            identity(tool, owner)
            for key in ("name", "description", "type"):
                text(tool[key])
            if tool["schemaStatus"] == "not_collected":
                if tool["schema"] is not None:
                    raise ValueError("Uncollected schema has content")
            elif tool["schemaStatus"] == "native_function_declaration":
                function_schema(tool["schema"])
            else:
                raise ValueError("Unknown schema status")
            if "agent" in tool:
                references.append(tool["agent"])

    def skills(items, owner):
        for skill in rows(items):
            fields(skill, {"id", "owner", "name", "description", "instructions",
                           "requestedTools", "resources", "source", "editable"})
            identity(skill, owner)
            for key in ("name", "description", "resources", "source"):
                text(skill[key])
            if skill["editable"] is not False:
                raise ValueError("Editable content refused")
            instruction(skill["instructions"])
            strings(skill["requestedTools"])

    for node in nodes:
        fields(node, {"id", "name", "instruction", "globalInstruction", "children",
                      "tools", "toolsets", "bindings"},
               {"type", "description", "model", "generation"})
        owner = identity(node)
        agent_ids.add(owner)
        for key in ("name", "description", "type"):
            if key in node:
                text(node[key])
        instruction(node["instruction"])
        instruction(node["globalInstruction"])
        references.extend(rows(node["children"]))
        if "model" in node:
            model = node["model"]
            if type(model) is str:
                text(model)
            else:
                fields(model, {"type", "configuration"})
                text(model["type"])
                if model["configuration"] != "omitted":
                    raise ValueError("Private model configuration refused")
        generation = node.get("generation", {})
        fields(generation, (), {"temperature", "top_p", "top_k", "max_output_tokens"})
        if any(type(v) not in (int, float, bool) for v in generation.values()):
            raise ValueError("Invalid generation configuration")
        tools(node["tools"], owner)
        for group in rows(node["toolsets"]):
            fields(group, {"id", "owner", "type", "availability", "skills", "tools"},
                   {"omission"})
            group_id = identity(group, owner)
            for key in ("type", "availability", "omission"):
                if key in group:
                    text(group[key])
            skills(group["skills"], group_id)
            tools(group["tools"], group_id)
        for group in rows(node["bindings"]):
            fields(group, {"id", "owner", "skillsOwner", "configuration", "skills", "tools"})
            group_id = identity(group, owner)
            if group["skillsOwner"] not in {"host", "adkl"}:
                raise ValueError("Unknown execution owner")
            if group["configuration"] != "omitted; build artifact required":
                raise ValueError("Private binding configuration refused")
            skills(group["skills"], group_id)
            tools(group["tools"], group_id)
    for reference in [data["root"], *references]:
        text(reference)
        if reference not in agent_ids:
            raise ValueError("Dangling agent reference")
    return data
