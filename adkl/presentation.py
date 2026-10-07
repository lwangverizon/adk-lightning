"""Versioned, stdlib-only presentation packs and invocation-scoped staging.

Domain tools supply semantic data; this module owns its portable display.
Native ADK tool adapters live in presentation_runtime, behind the runtime extra.
"""
from __future__ import annotations

import copy
import json

from adkl import widgets


PACKS = {
    "adkl/common@1": {"greeting", "fact", "step", "celebration"},
    "adkl/tutoring@1": {"greeting", "fact", "step", "celebration", "quiz", "cpa_lesson"},
    "adkl/commerce@1": {"greeting", "fact", "step", "navigation", "product_list"},
}
TOOL_NAMES = {
    "greeting": "render_greeting", "fact": "render_fact_card", "step": "render_steps",
    "celebration": "render_celebration", "cpa_lesson": "render_cpa_lesson",
    "quiz": "present_quiz", "navigation": "present_navigation", "product_list": "present_products",
}
ROLES = {"quiz": widgets.Role.INPUT_REQUEST, "navigation": widgets.Role.NAVIGATION}
QUEUE = "temp:adkl_presentation_items"
SEEN = "temp:adkl_presentation_calls"
HINT = "Presentation staged. Write brief companion text; do not repeat the card content."


class PresentationError(ValueError):
    pass


def configuration(doc: dict) -> dict:
    block = doc.get("presentation")
    if block is None:
        return {}
    if not isinstance(block, dict) or set(block) - {"version", "packs", "bindings", "exclude_kinds"}:
        raise PresentationError("presentation: expected version, packs, optional bindings and exclude_kinds")
    if type(block.get("version")) is not int or block["version"] != 1:
        raise PresentationError("presentation.version must be 1")
    packs = block.get("packs")
    if not isinstance(packs, list) or not packs or any(
        not isinstance(p, str) or p not in PACKS for p in packs
    ):
        raise PresentationError("presentation.packs must name supported versioned ADKL packs")
    from adkl.features import resolve
    if "staged_widgets" not in resolve(doc.get("features")).active:
        raise PresentationError("presentation requires staged_widgets in features.pipeline")
    kinds = set().union(*(PACKS[p] for p in packs))
    preview_kinds = set(kinds)
    excluded = block.get("exclude_kinds", [])
    if (not isinstance(excluded, list) or any(not isinstance(kind, str) or kind not in kinds for kind in excluded)):
        raise PresentationError("presentation.exclude_kinds must name kinds from the selected packs")
    kinds -= set(excluded)
    if not kinds:
        raise PresentationError("presentation must retain at least one kind")
    bindings = block.get("bindings", {})
    if not isinstance(bindings, dict):
        raise PresentationError("presentation.bindings must be a mapping")
    for name, binding in bindings.items():
        if (not isinstance(name, str) or not name.isidentifier()
                or not isinstance(binding, dict)
                or set(binding) != {"result_field", "kind"}
                or not isinstance(binding["result_field"], str)
                or not binding["result_field"].isidentifier()
                or not isinstance(binding["kind"], str)
                or binding["kind"] not in kinds):
            raise PresentationError(f"presentation binding {name!r}: invalid result_field or kind")
    return {"kinds": kinds, "preview_kinds": preview_kinds,
            "bindings": bindings, "packs": list(dict.fromkeys(packs))}


def extend_catalog(catalog: dict, doc: dict) -> dict:
    """Expose selected pack declarations without importing ADK or project code.

    Project catalogs contain domain tools only. Authoring and execution use this
    same merge so the picker and skill linter describe the actual tool surface.
    """
    config = configuration(doc)
    if not config:
        return catalog
    import ast
    import pathlib
    from adkl import tool_catalog
    source = pathlib.Path(__file__).with_name("presentation_runtime.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    entries = copy.deepcopy(catalog.get("tools", []))
    names = {entry["name"] for entry in entries}
    missing = set(config["bindings"]) - names
    if missing:
        raise PresentationError(f"presentation binding tools absent from catalog: {', '.join(sorted(missing))}")
    bound_kinds = {b["kind"] for b in config["bindings"].values()}
    for kind in sorted(config["kinds"] - bound_kinds):
        name = TOOL_NAMES[kind]
        if name in names:
            raise PresentationError(f"project tool {name!r} conflicts with a presentation pack tool")
        entry = tool_catalog.describe(tool_catalog.find_function(tree, name), str(source))
        entry["provider"] = "adkl/presentation"
        entry["emits_widget"] = {"value": True, "basis": "configured presentation pack", "inferred": False}
        entries.append(entry)
    for entry in entries:
        if entry["name"] in config["bindings"]:
            entry["emits_widget"] = {"value": True, "basis": "configured result binding", "inferred": False}
    return {**catalog, "tools": entries}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise PresentationError(f"{field} must be nonempty text")
    return value.strip()


def validate(payload: dict, kinds: set[str]) -> dict:
    if (not isinstance(payload, dict) or not isinstance(payload.get("type"), str)
            or payload["type"] not in kinds):
        raise PresentationError("presentation type is not enabled")
    try:
        encoded = json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PresentationError("presentation must be JSON data") from exc
    if len(encoded.encode()) > 64 * 1024:
        raise PresentationError("presentation exceeds 64 KiB")
    p = copy.deepcopy(payload)
    kind = p["type"]
    required = {
        "greeting": ("greeting", "subtitle"), "fact": ("title", "text"),
        "celebration": ("message",), "quiz": ("question",),
        "cpa_lesson": ("topic", "standard"),
        "navigation": ("title", "text", "label", "url"),
        "product_list": ("title",), "step": (),
    }
    for field in required[kind]:
        p[field] = _text(p.get(field), field)
    if kind == "greeting":
        for field in ("stars", "streak"):
            if type(p.get(field, 0)) is not int or p.get(field, 0) < 0:
                raise PresentationError(f"{field} must be a nonnegative integer")
    if kind == "step":
        values = p.get("steps")
        if not isinstance(values, list) or not 2 <= len(values) <= 6:
            raise PresentationError("steps must contain 2–6 entries")
        p["steps"] = [_text(v, "step") for v in values]
    if kind == "quiz":
        options = p.get("options")
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise PresentationError("quiz needs 2–4 options")
        labels = []
        for option in options:
            if not isinstance(option, dict):
                raise PresentationError("quiz option must be an object")
            labels.append(_text(option.get("label"), "option label"))
            _text(option.get("text"), "option text")
        if len(set(labels)) != len(labels):
            raise PresentationError("quiz labels must be unique")
        # Display does not implement grading. Never publish the legacy fallback key.
        p.pop("answer", None)
    if kind == "cpa_lesson":
        stages = p.get("stages")
        if not isinstance(stages, list) or len(stages) != 3:
            raise PresentationError("CPA lesson needs three stages")
        for stage, expected in zip(stages, ("concrete", "pictorial", "abstract")):
            if not isinstance(stage, dict) or stage.get("stage") != expected:
                raise PresentationError(f"CPA stage must be {expected}")
            _text(stage.get("explanation"), "explanation")
            _text(stage.get("visual"), "visual")
            # The established ClawTutor tool accepts snake_case input and
            # normalizes it to the React-facing camelCase field. Keep that
            # contract at the shared boundary.
            if "teacher_tip" in stage and "teacherTip" not in stage:
                stage["teacherTip"] = stage.pop("teacher_tip")
    if kind == "navigation":
        from urllib.parse import urlparse
        url = p["url"]
        if not (url.startswith("/") and not url.startswith("//")):
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise PresentationError("navigation needs a relative path or HTTPS URL without credentials")
    if kind == "product_list":
        products = p.get("products")
        if not isinstance(products, list) or not 1 <= len(products) <= 20:
            raise PresentationError("product_list needs 1–20 products")
        for product in products:
            if not isinstance(product, dict):
                raise PresentationError("product must be an object")
            _text(product.get("name"), "product name")
            _text(product.get("description"), "product description")
            _text(product.get("price"), "product price (display text)")
    return p


def convert(payload: dict, surface_id: str) -> dict:
    """Validated semantic card -> a single supported primitive tree."""
    p = validate(payload, set().union(*PACKS.values()))
    nodes, children = [], []

    def text(value, variant="body"):
        node_id = f"{surface_id}:{len(nodes)}"
        nodes.append({"id": node_id, "component": "Text", "text": str(value), "variant": variant})
        children.append(node_id)
        return node_id

    def button(label, event):
        label_id = text(label)
        children.pop()  # label belongs only to the button, never a sibling
        node_id = f"{surface_id}:{len(nodes)}"
        nodes.append({"id": node_id, "component": "Button", "child": label_id,
                      "action": {"event": event}})
        children.append(node_id)

    kind = p["type"]
    if kind == "greeting":
        text(f"{p.get('emoji', '👋')} {p['greeting']}", "h3")
        text(p["subtitle"])
        text(f"Stars: {p.get('stars', 0)} · Streak: {p.get('streak', 0)}", "caption")
    elif kind == "fact":
        text(f"{p.get('icon', '✨')} {p['title']}", "h3")
        text(p["text"])
    elif kind == "step":
        text("Step by step", "h3")
        for index, value in enumerate(p["steps"], 1):
            text(f"{index}. {value}")
    elif kind == "celebration":
        text("Great work!", "h3")
        text(p["message"])
    elif kind == "quiz":
        text("Quick check", "h3")
        text(p["question"])
        text("Display preview — answer submission is unavailable.", "caption")
        for option in p["options"]:
            button(f"{option['label']}. {option['text']}", {
                "name": "answer_quiz", "context": {"answer": option["label"],
                **({"question_id": p["question_id"]} if p.get("question_id") else {})}})
    elif kind == "cpa_lesson":
        text(p["topic"], "h3")
        text(p["standard"], "caption")
        for stage in p["stages"]:
            text(stage["stage"].title(), "h4")
            text(stage["explanation"])
            text(stage["visual"])
            if stage.get("teacherTip"):
                text(stage["teacherTip"], "caption")
    elif kind == "navigation":
        text(p["title"], "h3")
        text(p["text"])
        button(p["label"], {"name": "navigate", "context": {"url": p["url"]}})
        text("Display preview — navigation is unavailable.", "caption")
    elif kind == "product_list":
        text(p["title"], "h3")
        for product in p["products"]:
            text(product["name"], "h4")
            text(product["description"])
            text(product["price"])
    column, root = f"{surface_id}:content", f"{surface_id}:card"
    nodes.extend([{"id": column, "component": "Column", "children": children},
                  {"id": root, "component": "Card", "child": column}])
    return {"components": nodes, "root": root, "roots": [root],
            "surfaceId": surface_id, "widgetType": kind, "warnings": []}


def stage(context, payload: dict, kinds: set[str]) -> dict:
    p = validate(payload, kinds)
    queue = list(context.state.get(QUEUE) or [])
    call_id = getattr(context, "function_call_id", None)
    seen = dict(context.state.get(SEEN) or {})
    if call_id and call_id in seen:
        if seen[call_id] != p:
            raise PresentationError("tool call ID reused for different presentation data")
        return {"status": "staged", "card": p, "agent_instruction": HINT}
    if len(queue) >= 16:
        raise PresentationError("turn presentation limit is 16 cards")
    # Convert before staging so malformed data never receives a success claim.
    convert(p, "validation")
    queue.append(p)
    widgets.stage(context.state, spec(), queue)
    if call_id:
        seen[call_id] = p
        context.state[SEEN] = seen
    return {"status": "staged", "card": p, "agent_instruction": HINT}


def spec() -> widgets.WidgetSpec:
    async def build(ctx, items, request_id):
        return items

    def role(state):
        roles = [ROLES.get(p["type"], widgets.Role.DATA_PRIMARY)
                 for p in state.get(QUEUE, [])]
        return (widgets.Role.INPUT_REQUEST if widgets.Role.INPUT_REQUEST in roles
                else roles[-1] if roles else None)

    return widgets.WidgetSpec(
        key="adkl_presentation", widget_id="adkl-presentation", payload_type="presentation",
        build=build, register=QUEUE, resolve_role=role,
        default_companion="Here’s something to help you get started.")
