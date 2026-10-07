#!/usr/bin/env python3
"""Reduce native A2UI payloads and shared presentation payloads for the console.

Native widgets use {"type": "a2ui", "operations": [...]} and need no application
converter registry. Shared packs use SDK converters. Legacy custom semantic
payloads may still use an optional host-owned converter registry.

The console renders the 18 basic-catalog primitives shared by v0.9 and the v1.0
candidate. Unknown custom composites remain named placeholders. See
docs/a2ui-support.md for local-interaction, media and protocol boundaries.

## Shape of the output

Each op is `{"version": "v0.9", "createSurface": {...}}` or
`{"version": "v0.9", "updateComponents": {"surfaceId": ..., "components": [...]}}`.
Components reference each other by id, so the renderer resolves `child` /
`children` into a tree. The versioned reducer preserves data bindings and surface
identity while producing snapshots consumable by the Flutter renderer.
"""

from __future__ import annotations

from typing import Any

from adkl.a2ui_protocol import BASIC_COMPONENTS, reduce_operations

# Components the console renders faithfully. Anything else is a composite and
# gets a named placeholder -- see the module docstring.
PRIMITIVES = BASIC_COMPONENTS | {"Badge"}  # Badge is a legacy extension.


class A2uiError(RuntimeError):
    """The converter registry could not be reached, or a payload has no type."""


def converters(channel: str = "") -> dict:
    """The agent repo's converter registry for a channel.

    Imported lazily and through the repo's own dispatcher rather than reaching
    into the shared dict: channels layer overrides on top of the shared
    converters, and picking the wrong layer would render a card the target
    channel never produces.
    """
    try:
        from a2ui_renderers.v09.widget_converters import get_converters
    except Exception as exc:
        raise A2uiError(
            f"A2UI converters unavailable: {type(exc).__name__}: {exc}. "
            f"They live in the agent repo, so tools.repo must be set.") from exc
    return get_converters(channel)


def convert(payload: dict, *, surface_id: str = "adkl-surface",
            channel: str = "") -> dict:
    """Payload -> {components, root, surfaceId, warnings}.

    Returns a flattened component list and the root id, because a renderer wants
    a tree and the wire format is a list of operations. Never raises for a
    payload the converter dislikes: the console's job is to show what happened,
    and an exception would replace a diagnosable widget with a stack trace.
    """
    widget_type = (payload or {}).get("type")
    if not widget_type:
        # Worth saying plainly: production drops a typeless payload on the SSE
        # path, so this widget would never reach a customer.
        return {"components": [], "root": None, "surfaceId": surface_id,
                "warnings": ["payload has no `type`, so production would drop "
                             "it before it reached the customer"]}

    registry = converters(channel)
    fn = registry.get(widget_type)
    if fn is None:
        return {"components": [], "root": None, "surfaceId": surface_id,
                "warnings": [f"no converter registered for {widget_type!r}. "
                             f"The client would receive nothing to render."]}

    try:
        ops = fn(payload, surface_id)
    except Exception as exc:
        return {"components": [], "root": None, "surfaceId": surface_id,
                "warnings": [f"converter raised {type(exc).__name__}: {exc}"]}

    try:
        surfaces = reduce_operations(ops or [], legacy_roots=True)
    except ValueError as exc:
        return {"components": [], "root": None, "surfaceId": surface_id,
                "warnings": [f"invalid A2UI operations: {exc}"]}
    if not surfaces:
        return {"components": [], "root": None, "surfaceId": surface_id,
                "warnings": ["the converter produced no components"]}
    # Preserve the historical single-surface shape for existing consumers.
    # Multiple surfaces remain separate: duplicate IDs never cross boundaries.
    return {**surfaces[0], "widgetType": widget_type,
            **({"surfaces": surfaces} if len(surfaces) > 1 else {})}


def convert_all(widgets: list, *, channel: str = "", builtin_kinds=frozenset()) -> list:
    """Convert each widget a turn emitted, attaching ops to the widget dict.

    One failed conversion never costs the others: a turn can emit several
    widgets, and losing the readable ones because a later payload is malformed
    would hide more than it reveals.
    """
    out = []
    for index, w in enumerate(widgets or []):
        payload = (w or {}).get("payload") or {}
        entry = dict(w)
        try:
            surface_id = f"{w.get('id') or 'widget'}-{index}"
            if payload.get("type") == "a2ui":
                operations = payload.get("operations")
                if not isinstance(operations, list) or not operations:
                    raise ValueError("a2ui payload requires a nonempty operations list")
                surfaces = reduce_operations(operations)
                if not surfaces:
                    raise ValueError("a2ui operations produced no surfaces")
                entry["a2ui"] = {**surfaces[0], "surfaces": surfaces}
            elif payload.get("type") in builtin_kinds:
                from adkl import presentation
                entry["payload"] = presentation.validate(payload, builtin_kinds)
                entry["a2ui"] = presentation.convert(entry["payload"], surface_id)
            else:
                entry["a2ui"] = convert(payload, surface_id=surface_id, channel=channel)
        except (A2uiError, ValueError) as exc:
            entry["a2ui"] = {"components": [], "root": None,
                             "warnings": [str(exc)]}
        out.append(entry)
    return out
