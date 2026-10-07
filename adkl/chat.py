"""Development conversation adapter over the shared ADKL native runtime."""
from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from adkl import features as features_mod
from adkl import gateway as gw
from adkl.agent_runtime import AgentRuntime, TEST_MODE_NOTE
from adkl.agent_runtime import (
    _read_env, _generation_kwargs, _tools_by_skill, _catalog, _catalog_names,
    _load_tool_fns, _CALLBACK_KINDS, _load_callbacks, _load_skills, _repo_path,
    _prepare_tool_repo, _load_widget_specs, _widget_tools, _tool_cost,
)

@dataclass
class TurnTrace:
    """Everything needed to explain one turn."""

    user_text: str
    model_calls: int = 0
    activated_skills: list = field(default_factory=list)
    matches: list = field(default_factory=list)   # {skill, score, source}
    picked: str | None = None
    margin: float | None = None
    fallback: bool = False
    bound_tools: list = field(default_factory=list)
    unresolved_tools: list = field(default_factory=list)
    prompt_chars: int = 0
    tool_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    # A2UI widgets a tool emitted this turn, from
    # event.actions.render_ui_widgets. Each is {id, provider, payload}, and the
    # payload's `type` is what the renderer dispatches on -- a typeless payload
    # is dropped on the production SSE path, so it is worth surfacing here.
    widgets: list = field(default_factory=list)
    # Bound tools whose module emits a widget. Lets the console distinguish "the
    # agent chose not to render" from "a widget tool was bound but nothing can
    # execute it here", which look identical from an empty `widgets` list.
    widget_tools: list = field(default_factory=list)
    # Bound tools that resolved to a real callable and could actually run this
    # turn. Empty means the reply came from model memory alone.
    executable_tools: list = field(default_factory=list)
    # Bound tools the repo could not import. The dangerous case: the skill
    # declares it, the catalog knows it, and it silently is not there.
    unimportable_tools: list = field(default_factory=list)
    # Tool calls the model actually made, with status. The only field that
    # distinguishes "a tool was available" from "a tool ran".
    tool_calls: list = field(default_factory=list)
    # Loader failures: a tool or the widget emitter that could not be imported.
    # Surfaced because the widget emitter failed silently for a full debugging
    # round -- it returned a problem string that nothing ever read, so the
    # console showed zero widgets with no error anywhere.
    tool_load_problems: list = field(default_factory=list)
    # Staged-widget pipeline. `widget_gates` is the answer to "why did no widget
    # appear" -- every spec that declined, and at which gate. `contract` is the
    # reply shape that was injected before the model wrote, so a reply that
    # restates the card can be traced to a contract that never resolved.
    widget_gates: list = field(default_factory=list)
    contract: str | None = None
    contract_sources: list = field(default_factory=list)
    output_findings: list = field(default_factory=list)
    execution: dict | None = None

    def record_runtime(self, diagnostics: dict) -> None:
        self.matches = diagnostics.get("matches", [])
        self.picked = diagnostics.get("picked")
        self.margin = diagnostics.get("margin")
        self.fallback = diagnostics.get("fallback", False)
        self.model_calls = diagnostics.get("model_calls", 0)
        self.activated_skills = diagnostics.get("activated", [])

    def to_json(self) -> dict:
        return {
            "userText": self.user_text,
            "modelCalls": self.model_calls,
            "activatedSkills": self.activated_skills,
            "matches": self.matches,
            "picked": self.picked,
            "margin": round(self.margin, 4) if self.margin is not None else None,
            "fallback": self.fallback,
            "boundTools": self.bound_tools,
            "unresolvedTools": self.unresolved_tools,
            "promptChars": self.prompt_chars,
            "toolTokens": self.tool_tokens,
            "latencyMs": self.latency_ms,
            "error": self.error,
            "widgets": self.widgets,
            "widgetTools": self.widget_tools,
            "executableTools": self.executable_tools,
            "unimportableTools": self.unimportable_tools,
            "toolCalls": self.tool_calls,
            "toolLoadProblems": self.tool_load_problems,
            "widgetGates": self.widget_gates,
            "contract": self.contract,
            "contractSources": self.contract_sources,
            "outputFindings": self.output_findings,
            "execution": self.execution,
        }


class ChatSession(AgentRuntime):
    """One serialized development conversation, using native ADK sessions."""

    def __init__(self, root: pathlib.Path, env=None, *, model=None, embedder=None):
        super().__init__(root, env, model=model, embedder=embedder)
        self.history: list[dict] = []
        self._runner = None
        self._session_id = None

    def _build(self):
        super()._build()
        if self._runner is None:
            from google.adk.runners import Runner
            from google.adk.sessions import InMemorySessionService
            self._sessions = InMemorySessionService()
            self._runner = Runner(app=self._app, session_service=self._sessions)

    def _record_tool_surface(self, trace: TurnTrace, diagnostics: dict) -> None:
        """Resolve the turn's declared tool names into the trace's tool buckets.

        `declared_tools` comes from the runtime (the activated skills' own
        frontmatter); resolving it needs the catalog and the imported callables,
        which only this side has. The console gates its whole tool/LLM-turn
        section on `boundTools`, so an empty list here hides the tool calls,
        token cost and load problems that did happen -- these fields are a
        display contract, not decoration.
        """
        declared = list(diagnostics.get("declared_tools") or [])
        if not declared:
            return
        # `getattr`, because the host-factory path never runs the console's own
        # tool loading -- HostChatSession._build skips it, so the attribute is
        # genuinely absent rather than empty.
        executable = set(getattr(self, "_tool_fns", None) or {})
        # A host factory registers its tools on its bindings instead, and on a
        # sub-agent's binding when that is the agent holding the skills. Reading
        # every binding is what keeps a tool the host does execute from being
        # reported as one nothing could import: such a project may still ship a
        # `tools.catalog` for authoring, and a catalog alone would otherwise put
        # its whole tool surface in the "could not import" bucket.
        from adkl import inspection
        pending = [self._agent]
        while pending:
            agent = pending.pop()
            pending.extend(agent.sub_agents)
            for runtime in inspection.bindings(agent):
                executable.update(getattr(runtime, "_tool_fns", None) or {})
        # Without a catalog there is nothing to resolve against, so what the
        # bindings can execute is the authority -- and it is a strict one:
        # construction refuses a skill declaring a tool it cannot execute, so a
        # name missing from both is a real gap rather than a lookup failure.
        known = set(_catalog_names(self.root, self.doc)) or set(executable)
        trace.bound_tools = [name for name in declared if name in known]
        trace.unresolved_tools = [name for name in declared if name not in known]
        trace.tool_tokens = _tool_cost(self.root, self.doc, trace.bound_tools)
        trace.widget_tools = _widget_tools(self.root, self.doc, trace.bound_tools)
        # In the catalog but not importable is the dangerous case: the skill
        # declares it, the prompt promises it, and it silently is not there.
        trace.executable_tools = [n for n in trace.bound_tools if n in executable]
        trace.unimportable_tools = [n for n in trace.bound_tools
                                    if n not in executable]

    # ------------------------------------------------------------------- turn

    async def send(self, text: str, state: dict[str, Any] | None = None) -> dict:
        """Compatibility JSON result, collected from the same canonical stream."""
        from contextlib import aclosing
        async with aclosing(self.stream(text, state=state)) as frames:
            async for event in frames:
                if event["type"] == "done":
                    return event["result"]
        raise RuntimeError("Chat stream ended without a result")

    async def stream(self, text: str, state: dict[str, Any] | None = None):
        """Run one turn with task-local, read-only execution observations."""
        from adkl.console_trace import Collector, activate
        collector = Collector()
        frames = self._stream_turn(text, state)
        try:
            while True:
                # Never hold a ContextVar token across an outward yield: async
                # generator close may run in a different consumer context.
                with activate(collector):
                    try:
                        frame = await anext(frames)
                    except StopAsyncIteration:
                        break
                if frame["type"] == "done":
                    frame["result"]["trace"]["execution"] = collector.snapshot()
                yield frame
        finally:
            collector.closed = True
            await frames.aclose()

    async def _stream_turn(self, text: str, state: dict[str, Any] | None = None):
        """``state`` seeds a new development session only."""
        yield {"type": "progress", "phase": "preparing"}
        started = time.monotonic()
        trace = TurnTrace(user_text=text)
        # Cleared per turn: stale gate results would be read as this turn's.
        self._last_emit = None
        self._last_resolution = None
        self._last_request_snapshot = None
        self._output_findings = []

        try:
            self._build()
        except Exception as exc:
            trace.error = f"{type(exc).__name__}: {exc}"
            trace.latency_ms = int((time.monotonic() - started) * 1000)
            yield {"type": "done", "result": {"reply": None, "trace": trace.to_json()}}
            return

        trace.tool_load_problems = list(self._tool_problems)

        # --- retrieval first, so the trace exists even if the model call fails
        session_state = dict(state or {})
        yield {"type": "progress", "phase": "running", "matches": []}

        # --- the model call
        from google.genai import types

        session_state["today"] = time.strftime("%Y-%m-%d")
        trace.prompt_chars = len(self._compiled.text)

        try:
            if self._session_id is None:
                s = await self._sessions.create_session(
                    app_name=self._app.name, user_id="console", state=session_state)
                self._session_id = s.id
            else:
                await self._sessions.get_session(
                    app_name=self._app.name, user_id="console",
                    session_id=self._session_id)

            reply_parts: list[str] = []
            partial_pending = False
            reply_size = 0
            retrieval_announced = False
            async for event in self._runner.run_async(
                user_id="console",
                session_id=self._session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=text)]),
            ):
                from adkl.console_trace import node_output
                node_output(event)
                delta = getattr(getattr(event, "actions", None), "state_delta", None) or {}
                diagnostic_key = "adkl:last_turn:" + self._agent.name
                runtime_diagnostics = delta.get(diagnostic_key, delta.get("adkl:last_turn"))
                if runtime_diagnostics is not None:
                    trace.record_runtime(runtime_diagnostics)
                    if trace.matches and not retrieval_announced:
                        yield {"type": "progress", "phase": "running", "matches": trace.matches}
                        retrieval_announced = True
                # Partials are never exposed. ADK's completed response is the
                # after-model filtering boundary, independently for each call.
                if getattr(event, "partial", False):
                    partial_pending = True
                    continue
                content = getattr(event, "content", None)
                if any(getattr(p, "text", None) is not None or getattr(p, "function_call", None) is not None
                       for p in (getattr(content, "parts", None) or [])):
                    partial_pending = False
                for part in (getattr(content, "parts", None) or []):
                    chunk = getattr(part, "text", None)
                    if chunk and not getattr(part, "thought", False):
                        reply_size += len(chunk)
                        if reply_size > 2 * 1024 * 1024:
                            raise ValueError("Reply exceeds streaming text limit")
                        reply_parts.append(chunk)

                    # Tool traffic, both directions. Recorded because "a tool was
                    # bound" and "a tool ran" are different claims, and only the
                    # second one makes a reply trustworthy.
                    call = getattr(part, "function_call", None)
                    if call is not None:
                        yield {"type": "progress", "phase": "tool_call", "name": call.name}
                        trace.tool_calls.append({
                            "name": getattr(call, "name", None),
                            "args": dict(getattr(call, "args", None) or {}),
                            "status": "called",
                        })
                    resp = getattr(part, "function_response", None)
                    if resp is not None:
                        yield {"type": "progress", "phase": "tool_result", "name": resp.name}
                        raw = getattr(resp, "response", None)
                        # Tool payloads run to thousands of characters (one search
                        # returned 5.4k), so the panel gets shape and status, not
                        # the body. The status field is what a reviewer reads.
                        status, size = None, 0
                        if isinstance(raw, dict):
                            status = raw.get("status")
                            size = len(str(raw))
                        trace.tool_calls.append({
                            "name": getattr(resp, "name", None),
                            "status": status or "returned",
                            "responseChars": size,
                        })

                # A2UI widgets ride on the event's actions, not its content.
                request_snapshot = (getattr(getattr(event, "actions", None), "state_delta", None) or {}).get(
                    "adkl:request:" + self._agent.name)
                if request_snapshot:
                    self._last_request_snapshot = request_snapshot
                # This is the canonical path in production: a tool calls
                # tool_context.render_ui_widget(UiWidget(payload=...)) and the
                # widget attaches to event.actions.render_ui_widgets.
                for widget in (getattr(getattr(event, "actions", None),
                                       "render_ui_widgets", None) or []):
                    payload = getattr(widget, "payload", None) or {}
                    trace.widgets.append({
                        "id": getattr(widget, "id", None),
                        "provider": getattr(widget, "provider", None),
                        "type": payload.get("type"),
                        "payload": payload,
                    })
            completed_session = await self._sessions.get_session(
                app_name=self._app.name, user_id="console", session_id=self._session_id)
            diagnostics = completed_session.state.get("adkl:last_turn:" + self._agent.name,
                completed_session.state.get("adkl:last_turn", {}))
            self._last_request_snapshot = completed_session.state.get("adkl:request:" + self._agent.name)
            trace.record_runtime(diagnostics)
            self._record_tool_surface(trace, diagnostics)
            widget_diagnostics = completed_session.state.get("adkl:widgets:" + self._agent.name, {})
            trace.contract = widget_diagnostics.get("contract")
            trace.contract_sources = list(widget_diagnostics.get("sources", []))
            trace.widget_gates = list(widget_diagnostics.get("gates", []))
            if partial_pending:
                raise ValueError("Model stream ended without a completed response")
            from adkl.output_filters import filter_text
            resolved = features_mod.resolve(self.doc.get("features"))
            assembled_reply = "".join(reply_parts)
            if not assembled_reply.strip() and trace.widgets and self._last_emit:
                assembled_reply = self._last_emit.companion
            reply, findings = filter_text(assembled_reply,
                strip="output_cleanup" in resolved.active,
                redact_kinds=resolved.config.get("pii_redaction", {}).get("kinds")
                    if "pii_redaction" in resolved.active else False)
            self._record_output_findings(findings)
            reply = reply.strip() or None

            # A2UI conversion: the payload is not what a customer sees. Running
            # the agent repo's own converter is the only way the console shows
            # the real thing rather than my reading of it.
            if trace.widgets:
                try:
                    from adkl import a2ui as a2ui_mod
                    trace.widgets = a2ui_mod.convert_all(
                        trace.widgets,
                        builtin_kinds=self._presentation_config.get("preview_kinds",
                            self._presentation_config.get("kinds", set())))
                except Exception as exc:
                    # Never lose the widget over its rendering: the structural
                    # view still works, and the reason is worth surfacing.
                    trace.tool_load_problems.append(
                        f"A2UI conversion unavailable: {type(exc).__name__}: {exc}")

            # Pipeline decisions, recorded by the two callbacks during the run.
            if self._last_resolution is not None:
                r = self._last_resolution
                trace.contract = r.contract.value if r.contract else None
                trace.contract_sources = list(r.sources)
            if self._last_emit is not None:
                trace.widget_gates = [
                    {"key": g.key, "passed": g.passed, "gate": g.gate,
                     "detail": g.detail, "revived": g.revived}
                    for g in self._last_emit.gates]
            trace.output_findings = list(self._output_findings)
        except Exception as exc:
            trace.error = f"{type(exc).__name__}: {str(exc)[:400]}"
            reply = None

        trace.latency_ms = int((time.monotonic() - started) * 1000)
        self.history.append({"role": "user", "text": text})
        if reply:
            self.history.append({"role": "agent", "text": reply})
        if reply:
            yield {"type": "text", "text": reply}
        yield {"type": "done", "result": {"reply": reply, "trace": trace.to_json()}}

    def clear(self) -> None:
        self.history.clear()
        self._session_id = None
        self._last_request_snapshot = None
