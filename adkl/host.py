"""Explicit opt-in local inspection of an existing native ADK factory.

Only the project worker imports host code. Console sessions remain disposable;
the host's production Runner and services are never replaced or attached live.
"""
from __future__ import annotations

import importlib
import re
from types import SimpleNamespace

from adkl.chat import ChatSession


def entrypoint(doc):
    value = (doc.get("agent") or {}).get("factory")
    if value is not None and (not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value)):
        raise ValueError("agent.factory must be module:callable")
    return value


def describe_agent(agent):
    """Inspect constructed objects without calling tools or resolving stateful toolsets."""
    model = getattr(agent, "model", None)
    from adkl.inspection import audit_snapshot, bindings, snapshot
    snapshots = [snapshot(binding) for binding in bindings(agent)]
    for item in snapshots:
        item["audit"] = audit_snapshot(item, getattr(agent, "instruction", None))
    return {"name": agent.name, "type": type(agent).__name__,
            "adkl": snapshots,
            "model": model if isinstance(model, str) else getattr(model, "model", None),
            "instruction": "dynamic" if callable(getattr(agent, "instruction", None)) else "static",
            "staticInstruction": getattr(agent, "instruction", None) if isinstance(getattr(agent, "instruction", None), str) else None,
            "tools": [getattr(t, "name", getattr(t, "__name__", type(t).__name__))
                      for t in getattr(agent, "tools", [])],
            "callbacks": {kind: len(value) if isinstance(value, list) else 1
                          for kind in ("before_agent_callback", "before_model_callback",
                                       "before_tool_callback", "after_tool_callback",
                                       "after_agent_callback", "after_model_callback")
                          if (value := getattr(agent, kind, None))},
            "callbackOrder": {kind: [getattr(fn, "__qualname__", type(fn).__name__)
                                     for fn in (value if isinstance(value, list) else [value])]
                              for kind in ("before_agent_callback", "before_model_callback",
                                           "before_tool_callback", "after_tool_callback",
                                           "after_agent_callback", "after_model_callback")
                              if (value := getattr(agent, kind, None))},
            "children": [describe_agent(a) for a in agent.sub_agents]}


class HostChatSession(ChatSession):
    def preflight(self):
        try:
            self._build()
            return []
        except Exception as exc:
            return [f"Host factory could not load ({type(exc).__name__}); check its project dependencies."]

    def _build(self):
        if self._runner is not None:
            return
        from adkl.toolbind import prepare
        from google.adk.agents import BaseAgent
        from google.adk.apps import App
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from adkl import construction_log
        # Import and construction run unchanged; only their log records are kept.
        with construction_log.capture() as self._construction_log:
            prepare(self.root)
            module, symbol = entrypoint(self.doc).split(":")
            factory = getattr(importlib.import_module(module), symbol)
            if not callable(factory):
                raise TypeError("Host factory must be callable")
            result = factory()
        if isinstance(result, App):
            self._app = result
            self._agent = result.root_agent
        elif isinstance(result, BaseAgent):
            self._agent = result
            self._app = App(name=result.name, root_agent=result)
        else:
            raise TypeError("Host factory must return a native ADK Agent or App")
        prompt = getattr(self._agent, "instruction", "")
        self._compiled = SimpleNamespace(text=prompt if isinstance(prompt, str) else "")
        self._tool_problems = []
        self._sessions = InMemorySessionService()
        self._runner = Runner(app=self._app, session_service=self._sessions)
        from adkl.inspection import bindings
        kinds = set()
        pending = [self._agent]
        while pending:
            agent = pending.pop()
            pending.extend(agent.sub_agents)
            for binding in bindings(agent):
                binding._capture_requests = True
                kinds.update(binding._presentation_config.get("preview_kinds", set()))
        self._presentation_config = {"kinds": kinds}

    def runtime_manifest(self, problems):
        """Preview support for the features this host's own bindings run.

        The descriptor's `features:` block is not what runs here -- a host
        passes `feature_config` to `RuntimeBindings` in code -- so resolving the
        descriptor would report a pipeline nobody activated. Reporting an empty
        one instead dropped the limits that do apply: `RuntimeBindings` refuses
        an unsupported feature outright, but a partial one (`progressive_auth`
        records public-help attempts and enforces no authorization) then reached
        the console with no warning at all, while the same feature in an ADKL
        project warned in the chat panel.
        """
        from adkl import capabilities, features
        from adkl.inspection import bindings
        active: list[str] = []
        # Sub-agents too: the agent holding the skills is often not the root.
        pending = [self._agent] if problems == [] else []
        while pending:
            agent = pending.pop()
            pending.extend(agent.sub_agents)
            for binding in bindings(agent):
                for name in features.resolve(binding.doc.get("features")).active:
                    if name not in active:
                        active.append(name)
        # Only `active` decides support. A config merged across bindings would
        # describe settings no single binding has, so none is claimed here.
        report = capabilities.manifest(features.Resolved(active, [], {}))
        report["target"] = "host"
        report["blockers"] = [*report["blockers"], *problems]
        report["canRun"] = not report["blockers"]
        # Never complete: shared features are one part of a host agent, and its
        # own callbacks, services and tools are outside what ADKL reports on.
        report["complete"] = False
        report["warnings"].append(
            "Runtime behavior is owned by the host factory; inspect its callback adoption.")
        return report

    def inspect(self):
        self._build()
        report = {"source": "host_factory", "factory": entrypoint(self.doc),
                "lastRequest": getattr(self, "_last_request_snapshot", None),
                "sessionMode": "disposable_local", "agent": describe_agent(self._agent),
                "constructionLog": getattr(self, "_construction_log", None),
                "consistency": "Host factory executes unchanged; ADKL behavior depends on host adapter adoption."}
        from adkl.native_authoring import Workspace
        report["authoringFindings"] = Workspace(self.root, self.doc, report).mapping_findings()
        return report
