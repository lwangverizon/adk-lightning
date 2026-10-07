"""Explicit model-facing row projections; source/state data stays intact."""
from __future__ import annotations

from copy import deepcopy


def validate_rules(rules):
    if not isinstance(rules, dict):
        raise ValueError("projection tools must be a mapping")
    for tool, rule in rules.items():
        if not isinstance(tool, str) or not tool or not isinstance(rule, dict):
            raise ValueError("projection requires tool names and rule mappings")
        if set(rule) - {"path", "fields", "max_string_chars"}:
            raise ValueError("projection accepts path, fields and max_string_chars")
        for key in ("path", "fields"):
            value = rule.get(key)
            if (not isinstance(value, list) or (key == "fields" and not value)
                    or any(not isinstance(v, str) or not v for v in value)
                    or len(value) != len(set(value)) and key == "fields"):
                raise ValueError(f"projection {key} must be a list of distinct non-empty field names")
        limits = rule.get("max_string_chars", {})
        if (not isinstance(limits, dict) or set(limits) - set(rule["fields"])
                or any(type(v) is not int or v < 1 for v in limits.values())):
            raise ValueError("projection string limits require selected fields and positive integers")


def _note_mismatch(tool_context, name: str) -> None:
    """Record a shape mismatch in turn state, so silence is not the only report.

    Pass-through on drift is deliberate (see make_result_projector) and stays
    deliberate -- this reports, it does not raise and does not change the
    projected result. `on_finding` reaches the console; a host built through
    `RuntimeBindings` has no console and, until this, no channel at all: it saw
    a tool whose payload was simply not projected, with nothing anywhere
    saying so.
    """
    state = getattr(tool_context, "state", None)
    if state is None:
        return
    try:
        notes = list(state.get("temp:adkl_projection_findings") or [])
        if name not in notes:
            notes.append(name)
        state["temp:adkl_projection_findings"] = notes
    except Exception:
        pass   # a diagnostic must never break a turn


def make_result_projector(tools, on_finding=None):
    """Project one declared list path, retaining envelope and every row.

    Unknown tools and shape drift pass through unchanged. A mixed/non-mapping
    row list is left whole; silently dropping those rows would change results.
    False, zero, null and empty field values are preserved. This is not a hard
    size cap or a privacy filter; field selection belongs to the host contract.
    """
    validate_rules(tools)
    rules = deepcopy(tools)

    def after_tool(tool, args, tool_context, tool_response):
        rule = rules.get(getattr(tool, "name", None))
        if rule is None:
            return None
        rows = tool_response
        for key in rule["path"]:
            if not isinstance(rows, dict) or key not in rows:
                _note_mismatch(tool_context, tool.name)
                if on_finding:
                    on_finding([f"Projection shape mismatch: {tool.name}"])
                return None
            rows = rows[key]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            _note_mismatch(tool_context, tool.name)
            if on_finding:
                on_finding([f"Projection shape mismatch: {tool.name}"])
            return None
        projected, clipped = [], 0
        for row in rows:
            item = {key: deepcopy(row[key]) for key in rule["fields"] if key in row}
            for key, limit in rule.get("max_string_chars", {}).items():
                value = item.get(key)
                if isinstance(value, str) and len(value) > limit:
                    item[key] = value[:limit] + "…"
                    clipped += 1
            projected.append(item)
        result = deepcopy(tool_response)
        if rule["path"]:
            parent = result
            for key in rule["path"][:-1]:
                parent = parent[key]
            parent[rule["path"][-1]] = projected
        else:
            result = projected
        if on_finding:
            on_finding([f"Projected {tool.name}: {len(rows)} rows retained, {clipped} strings clipped"])
        return result

    return after_tool
