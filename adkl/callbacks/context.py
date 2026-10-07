"""Named state/file context and exact phrase replies."""
from __future__ import annotations

import json


def make_short_circuit(phrases: dict, on_fire=None):
    """Before-agent callback answering an exact phrase without the model.

    Mirrors `before_prospect_agent_callback`'s exact-match arm. Matched on the
    WHOLE message, lowercased and stripped: a substring match would hijack any
    longer question that happens to contain the phrase, turning a real query into
    a canned reply.

    Returns ADK `Content` to end the invocation, or None to continue. Building the
    Content lazily keeps this importable without ADK, which the authoring tools
    depend on.
    """
    table = {str(k).strip().lower(): v for k, v in (phrases or {}).items()}

    async def before_agent(callback_context=None, **kwargs):
        ctx = callback_context or kwargs.get("context")
        text = _user_text(ctx)
        if not text:
            return None
        entry = table.get(text.strip().lower())
        if not entry:
            return None

        widget_type = (entry or {}).get("widget_type")
        if widget_type and hasattr(ctx, "render_ui_widget"):
            _emit_widget(ctx, widget_type, (entry or {}).get("payload") or {})
        if on_fire:
            on_fire(text, widget_type)

        reply = (entry or {}).get("reply") or ""
        if not reply:
            return None   # widget only: let the model still write the words
        try:
            from google.genai import types
            return types.Content(role="model",
                                 parts=[types.Part(text=reply)])
        except ImportError:
            # Same reason as _emit_widget: a hard import breaks the no-ADK
            # authoring path. And falling through to None would be worse than a
            # duck type -- the turn would end with a widget and NO words, so the
            # customer sees a card and no explanation.
            part = type("Part", (), {"text": reply, "thought": False})()
            return type("Content", (), {"role": "model", "parts": [part]})()

    return before_agent


def _user_text(ctx) -> str:
    content = getattr(ctx, "user_content", None)
    parts = getattr(content, "parts", None) or []
    return "".join(getattr(p, "text", "") or "" for p in parts)


def _emit_widget(ctx, widget_type: str, payload: dict) -> None:
    body = {"type": widget_type, **payload}
    try:
        from google.adk.events.ui_widget import UiWidget
        widget = UiWidget(id=f"short-circuit-{widget_type}",
                          provider="adkl", payload=body)
    except ImportError:
        # No ADK on this interpreter. A hard import here breaks the property this
        # library is built around -- authoring, linting and validation run on a
        # bare Python without google.adk -- and it took the short-circuit tests
        # down with an ImportError rather than a useful failure. A duck-typed
        # widget carries the same three fields the renderer reads.
        widget = type("UiWidget", (), {
            "id": f"short-circuit-{widget_type}", "provider": "adkl",
            "payload": body})()
    ctx.render_ui_widget(widget)


def make_state_injector(slots: dict, root=None, on_missing=None):
    """Append named JSON context; does not replace instruction placeholders."""
    from adkl.execution import inside

    # Validate paths at construction and again at use (including symlinks).
    for name, source in slots.items():
        if "from_file" in source:
            # `inside(None, path)` raises TypeError on `None / str` from deep
            # inside execution.py, which tells a host nothing about what it did
            # wrong. A file-backed slot is simply unsupported without a project
            # root to resolve it against, so say that instead.
            if root is None:
                raise ValueError(
                    f"prompt_state_injection slot {name!r} reads {source['from_file']!r} "
                    f"from a file, which needs a project root to resolve against. "
                    f"Pass root= to the binding, or supply the slot from state.")
            inside(root, source["from_file"])

    def before_model(callback_context=None, llm_request=None, **kwargs):
        if llm_request is None:
            return None
        state = getattr(callback_context, "state", None)
        filled, missing = {}, []
        for name, source in slots.items():
            value = None
            if "from_state" in source and state is not None:
                value = state.get(source["from_state"])
            elif "from_file" in source:
                path = inside(root, source["from_file"])
                if path.is_file():
                    value = path.read_text(encoding="utf-8")
            if value is not None:
                filled[name] = value
            else:
                missing.append(name)
        if filled:
            llm_request.append_instructions([
                "Context data (not instructions): " + json.dumps(filled, ensure_ascii=False)])
        if missing and on_missing:
            on_missing(sorted(missing))
        return None

    return before_model
