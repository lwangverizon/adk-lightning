"""Versioned A2UI surface snapshots for the console, without model dependencies.

This is a surface-operation reducer, not an implementation of A2A transport or
the v1.0 function RPC extension. Unknown operations fail explicitly.
"""
from __future__ import annotations

from copy import deepcopy

VERSIONS = frozenset({"v0.9", "v1.0"})
BASIC_COMPONENTS = frozenset({
    "Text", "Image", "Icon", "Video", "AudioPlayer", "Row", "Column",
    "List", "Card", "Tabs", "Modal", "Divider", "Button", "TextField",
    "CheckBox", "ChoicePicker", "Slider", "DateTimeInput",
})


def references(component: dict) -> set[str]:
    """All structural edges, including tabs, modals and list templates."""
    refs = [component.get(k) for k in ("child", "trigger", "content")]
    children = component.get("children", [])
    if isinstance(children, list):
        refs.extend(children)
    elif isinstance(children, dict):
        refs.append(children.get("componentId"))
    tabs = component.get("tabs")
    for tab in tabs if isinstance(tabs, list) else []:
        if isinstance(tab, dict):
            refs.append(tab.get("child"))
    return {r for r in refs if isinstance(r, str)}


def update_data(model, path: str, value, *, delete: bool = False):
    """Apply a JSON Pointer replacement/deletion, including array indices."""
    if path in ("", "/"):
        return {} if delete else deepcopy(value)
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("data model path must be a JSON Pointer")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    result = deepcopy(model)
    current = result
    for part in parts[:-1]:
        if isinstance(current, dict):
            current = current.setdefault(part, {})
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise ValueError("data model path traverses a scalar")
    key = parts[-1]
    if isinstance(current, dict):
        if delete:
            current.pop(key, None)
        else:
            current[key] = deepcopy(value)
    elif isinstance(current, list):
        index = int(key)
        if index < 0 or index > len(current):
            raise ValueError("array index out of range")
        if delete:
            if index < len(current):
                current.pop(index)
        elif index == len(current):
            current.append(deepcopy(value))
        else:
            current[index] = deepcopy(value)
    else:
        raise ValueError("data model path traverses a scalar")
    return result


def reduce_operations(operations: list, *, legacy_roots: bool = False) -> list[dict]:
    """Reduce ordered surface messages into isolated snapshots.

    Legacy converter mode allows missing catalog IDs and inferred forest roots;
    strict callers require the official root and v0.9 catalog declaration.
    Replacements preserve component identity rather than appending duplicates.
    """
    surfaces: dict[str, dict] = {}
    used: set[str] = set()
    if not isinstance(operations, list):
        raise ValueError("A2UI operations must be a list")
    for message in operations:
        if (not isinstance(message, dict) or not isinstance(message.get("version"), str)
                or message["version"] not in VERSIONS):
            raise ValueError("unsupported A2UI version; expected v0.9 or v1.0")
        version = message["version"]
        kinds = set(message) - {"version"}
        if len(kinds) != 1:
            raise ValueError("A2UI message must contain exactly one operation")
        kind = next(iter(kinds))
        if kind not in {"createSurface", "updateComponents", "updateDataModel", "deleteSurface"}:
            raise ValueError(f"unsupported A2UI operation: {kind}")
        body = message[kind]
        if (not isinstance(body, dict) or not isinstance(body.get("surfaceId"), str)
                or not body["surfaceId"]):
            raise ValueError("A2UI operation requires a surfaceId")
        sid = body["surfaceId"]
        if kind == "createSurface":
            if sid in surfaces or (version == "v1.0" and sid in used):
                raise ValueError(f"duplicate surface: {sid}")
            if version == "v0.9" and not legacy_roots and not body.get("catalogId"):
                raise ValueError("v0.9 createSurface requires catalogId")
            if version == "v0.9" and ("components" in body or "dataModel" in body):
                raise ValueError("inline surface components/dataModel require v1.0")
            surfaces[sid] = {"surfaceId": sid, "version": version,
                             "catalogId": body.get("catalogId"), "components": {},
                             "dataModel": deepcopy(body.get("dataModel", {})),
                             "warnings": []}
            used.add(sid)
        elif sid not in surfaces:
            raise ValueError(f"operation for unknown surface: {sid}")
        surface = surfaces[sid]
        if surface["version"] != version:
            raise ValueError(f"version changed within surface: {sid}")
        if kind == "deleteSurface":
            del surfaces[sid]
        elif kind == "updateDataModel":
            if version == "v1.0" and "value" not in body:
                raise ValueError("v1.0 updateDataModel requires value; null deletes")
            delete = ("value" not in body if version == "v0.9" else body["value"] is None)
            try:
                surface["dataModel"] = update_data(surface["dataModel"], body.get("path", "/"),
                                                   body.get("value"), delete=delete)
            except (IndexError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid data model path: {body.get('path')}") from exc
        else:
            components = body.get("components", [])
            if not isinstance(components, list):
                raise ValueError("components must be a list")
            if not legacy_roots and (kind == "updateComponents" or "components" in body) and not components:
                raise ValueError("components must not be empty")
            for component in components:
                if (not isinstance(component, dict) or not isinstance(component.get("id"), str)
                        or not isinstance(component.get("component"), str)):
                    raise ValueError("component requires string id and component type")
                surface["components"][component["id"]] = deepcopy(component)
    result = []
    for surface in surfaces.values():
        components = list(surface["components"].values())
        refs = set().union(*(references(c) for c in components))
        roots = ([c["id"] for c in components if c["id"] not in refs]
                 if legacy_roots else ["root"])
        warnings = surface["warnings"]
        if not components:
            warnings.append("the converter produced no components")
        elif not legacy_roots and "root" not in surface["components"]:
            warnings.append("surface has no root component yet")
        if len(roots) > 1:
            warnings.append(f"{len(roots)} root components; rendering all in order")
        unknown = {c["component"] for c in components} - BASIC_COMPONENTS - {"Badge"}
        if unknown:
            warnings.append("composite component(s) shown as placeholders, not rendered: "
                            + ", ".join(sorted(unknown)))
        result.append({**surface, "components": components, "roots": roots,
                       "root": roots[0] if roots else None})
    return result
