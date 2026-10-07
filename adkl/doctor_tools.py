"""Explicit output-field contracts and consumers; no host imports or prose inference.

This deliberately small field contract is NOT JSON Schema. Fixtures are synthetic
host-owned examples, not evidence that a tool produced or a consumer used them.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from adkl import evaluation, verification

CONFIG = "doctor-tools.json"
TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array", "null"})


def _load(root, name):
    path = verification._path(root, name)
    if path.suffix != ".json":
        raise ValueError("Output contracts and fixtures must be tracked JSON files")
    relative = path.relative_to(root)
    if any((root / part).is_symlink() for part in (relative, *relative.parents)):
        raise ValueError("Output contract inputs must not traverse symlinks")
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Output contract inputs must be files <= 2 MiB")
    return json.loads(path.read_bytes(), object_pairs_hook=evaluation._unique_pairs)


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Expected bounded contract identifier")


def _fields(fields):
    if not isinstance(fields, dict) or not fields or len(fields) > 1000:
        raise ValueError("Declare nonempty output fields")
    for path, kind in fields.items():
        if (not isinstance(path, str) or len(path) > 512
                or not re.fullmatch(r"(?:/(?:[A-Za-z0-9_-]+|\*))+", path)
                or not isinstance(kind, str) or kind not in TYPES):
            raise ValueError("Invalid output path or type")


def _matches(value, kind):
    return {"string": isinstance(value, str), "number": type(value) in (int, float)
            and (not isinstance(value, float) or math.isfinite(value)),
            "integer": type(value) is int, "boolean": type(value) is bool,
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "null": value is None}[kind]


def _values(value, parts):
    if not parts:
        return [value]
    part, *rest = parts
    if part == "*":
        # Empty arrays provide no field evidence; require a representative fixture.
        if not isinstance(value, list) or not value:
            return []
        result = []
        for child in value:
            found = _values(child, rest)
            if not found:
                return []
            result.extend(found)
        return result
    if not isinstance(value, dict) or part not in value:
        return []
    return _values(value[part], rest)


def collect(root):
    """Return sanitized declarations/findings for Doctor's static check."""
    root = Path(root).resolve()
    if not (root / CONFIG).exists() and not (root / CONFIG).is_symlink():
        return {"state": "unconfigured", "tools": []}, []
    config = _load(root, CONFIG)
    evaluation._fields(config, ("version", "tools"), "output contracts")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ValueError("Unsupported output contracts version")
    tools = config["tools"]
    if not isinstance(tools, list) or not 1 <= len(tools) <= 100:
        raise ValueError("Declare 1–100 tool contracts")
    names, summary, findings = set(), [], []

    def finding(rule, subject, source, message):
        findings.append({"rule": rule, "severity": "ERROR", "contract": subject,
                         "source": source, "message": message})

    for tool in tools:
        evaluation._fields(tool, ("name", "version", "fields", "fixtures", "consumers"), "tool contract")
        for key in ("name", "version"):
            _label(tool[key])
        name = tool["name"]
        if name in names:
            raise ValueError("Duplicate tool contract")
        names.add(name)
        _fields(tool["fields"])
        if (not isinstance(tool["fixtures"], list) or not 1 <= len(tool["fixtures"]) <= 100
                or not isinstance(tool["consumers"], list) or not 1 <= len(tool["consumers"]) <= 100):
            raise ValueError("Each tool needs bounded fixtures and consumers")
        fixtures = []
        for source in tool["fixtures"]:
            fixture = _load(root, source)
            if source in fixtures:
                raise ValueError("Duplicate fixture")
            fixtures.append(source)
            for path, kind in tool["fields"].items():
                values = _values(fixture, path.split("/")[1:])
                if not values or not all(_matches(value, kind) for value in values):
                    finding("TOOL_OUTPUT_FIXTURE_MISMATCH", name, source,
                            f"Fixture does not establish {path} as {kind} for {name}.")
        consumers, seen = [], set()
        for consumer in tool["consumers"]:
            evaluation._fields(consumer, ("name", "source", "version", "fields"), "consumer")
            for key in ("name", "version"):
                _label(consumer[key])
            if consumer["name"] in seen:
                raise ValueError("Duplicate consumer")
            seen.add(consumer["name"])
            # Validate source identity/exclusion without opening its text.
            path = verification._path(root, consumer["source"])
            if not path.is_file():
                raise ValueError("Consumer source must be a file")
            _fields(consumer["fields"])
            subject = name + ":" + consumer["name"]
            if consumer["version"] != tool["version"]:
                finding("TOOL_OUTPUT_VERSION_MISMATCH", subject, consumer["source"],
                        f"Consumer declares version {consumer['version']}; {name} declares {tool['version']}.")
            for field, kind in consumer["fields"].items():
                if tool["fields"].get(field) != kind:
                    finding("TOOL_OUTPUT_CONSUMER_MISMATCH", subject, consumer["source"],
                            f"Consumer requires {field} as {kind}, absent or different in {name}.")
            consumers.append({"name": consumer["name"], "source": consumer["source"]})
        summary.append({"name": name, "version": tool["version"], "consumers": consumers,
                        "fixtures": len(fixtures)})
    # Content changes in declarations alter coverage; don't resolve issues by
    # removing a consumer, expected field or fixture. A new baseline is required.
    return {"state": "findings" if findings else "passed", "tools": summary,
            "coverageDigest": verification._hash(config)}, findings
