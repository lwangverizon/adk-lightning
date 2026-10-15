"""Shared lifecycle for host-owned native ADK agents.

Importing this module needs no ADK installation. Native objects are accepted at
construction; host credentials, models, runners and transport are never created.
"""
from __future__ import annotations

import inspect
import pathlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from adkl import capabilities, features, runtime as runtime_mod
from adkl import widgets as widgets_mod
from adkl.callbacks import _lifecycle

CALLBACK_KINDS = frozenset({"before_agent_callback", "after_agent_callback", "before_model_callback",
    "after_model_callback", "before_tool_callback", "after_tool_callback",
    "on_model_error_callback", "on_tool_error_callback"})


class NativeLifecycle:
    """Internal lifecycle shared by project factories and native bindings."""

    def resolution_warnings(self) -> list[str]:
        """Everything `features.resolve` and the sdk manifest flagged but allowed.

        These are the four classes a host most needs and could not previously
        reach: an `enabled: false` kill switch that beat pipeline membership, a
        non-stable feature live in production, an option made inert by a gate
        that skips the block reading it, and a feature shadowed by an earlier one
        so it never runs. `features.resolve` computes them on every host path and
        nothing read them -- only the console's own endpoint did -- so a guard
        written for a host was invisible to that host. `partial` support detail
        joins them because activating a partial feature is equally worth stating.
        """
        resolved = features.resolve(self.doc.get("features"))
        return list(resolved.warnings) + list(
            capabilities.manifest(resolved, "sdk")["warnings"])

    def _report_resolution(self, resolved) -> None:
        """Hand resolution warnings to a host's callback, if it supplied one."""
        report = getattr(self, "_on_warning", None)
        if report is None:
            return
        notes = list(resolved.warnings) + list(
            capabilities.manifest(resolved, "sdk")["warnings"])
        if notes:
            report(notes)

    def _compose_callbacks(self, callbacks, observers=(), model_observers=(), model_transforms=(),
                           output_transforms=()):
        from adkl.skill_runtime import SelectedSkillToolset
        hooks = _lifecycle.normalize_callbacks(callbacks, CALLBACK_KINDS)
        _lifecycle.require_callbacks(observers, "after_tool_observers")
        resolved = features.resolve(self.doc.get("features"))
        # AgentRuntime supplies `_record_output_findings`; a host binding supplies
        # `on_finding`. Resolve both explicitly rather than sniffing one name that
        # only exists on one of the two classes.
        report = (getattr(self, "_record_output_findings", None)
                  or getattr(self, "_on_finding", None))
        shared, unsupported = runtime_mod.build_callbacks(
            resolved.active, resolved.config, report,
            widget_specs=self._widget_specs, root=getattr(self, "root", None))
        if unsupported:
            raise ValueError("; ".join(unsupported))
        self._report_resolution(resolved)
        for kind, fns in shared.items():
            hooks.setdefault(kind, []).extend(fns)
        _lifecycle.require_callbacks(output_transforms, "after_model_transforms")
        if output_transforms or shared.get("after_model_callback"):
            chain = [*hooks.get("after_model_callback", []), *output_transforms]
            hooks["after_model_callback"] = [_lifecycle.compose_output_transforms(chain)]
        if observers or hooks.get("after_tool_callback"):
            hooks["after_tool_callback"] = [_lifecycle.compose_tool_callbacks(
                observers, hooks.get("after_tool_callback", []))]
        if self._widgets_active and self._widget_specs:
            # Resolve the reporter at failure time, as on the factory/host paths.
            def get_reporter():
                return (getattr(self, "_record_output_findings", None)
                        or getattr(self, "_on_finding", None))
            hooks["after_agent_callback"] = [_lifecycle.compose_completion(
                self._make_emitter(), hooks.get("after_agent_callback", []), get_reporter)]
            hooks.setdefault("before_model_callback", []).append(self._make_contract_injector())
        native = getattr(self, "_native_toolset", None)
        self._selection = None if native is not None else SelectedSkillToolset(self)
        if native is not None:
            hooks.setdefault("before_agent_callback", []).insert(0, self._begin_native)
        else:
            hooks.setdefault("before_agent_callback", []).insert(0, self._selection.begin)
            hooks["before_agent_callback"].append(self._selection.prepare)
            if getattr(self, "_prepare_skill", None) is not None:
                hooks.setdefault("before_tool_callback", []).insert(0, self._selection.before_tool)
        # Position among the before-model callbacks is load-bearing and there is
        # no seam outside this function for it: `final_model_transforms` are
        # appended AFTER this hook, so a host that needs its own injection ahead
        # of our skill blocks (or needs ours ahead of its own, which is what
        # native ADK does -- the toolset contributes instructions from
        # process_llm_request, before any agent callback runs) has to reorder the
        # composed list from outside and reach into a private attribute to do it.
        # Read from `resolved` rather than a constructor kwarg because this one
        # function serves both the project factory and RuntimeBindings.
        #
        if native is not None:
            hooks.setdefault("before_model_callback", []).insert(0, self._observe_native)
        elif resolved.config["skill_retrieval"]["skill_hook_position"] == "first":
            hooks.setdefault("before_model_callback", []).insert(0, self._selection.before_model)
        else:
            hooks.setdefault("before_model_callback", []).append(self._selection.before_model)
        _lifecycle.require_callbacks(model_observers, "before_model_observers")
        _lifecycle.require_callbacks(model_transforms, "final_model_transforms")
        model_transforms = tuple(model_transforms)
        model_observers = tuple(model_observers)
        async def finalize_model(callback_context, llm_request):
            for transform in model_transforms:
                result = transform(callback_context=callback_context, llm_request=llm_request)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    raise TypeError("final-model transforms must mutate the request and return None")
        if model_transforms:
            hooks["before_model_callback"].append(finalize_model)
        self._final_model_transform_count = len(model_transforms)
        async def observe_model(callback_context, llm_request):
            for observer in model_observers:
                result = observer(callback_context=callback_context, llm_request=llm_request)
                if inspect.isawaitable(result):
                    result = await result
                if result is not None:
                    raise TypeError("before-model observers must return None")
            from adkl.console_trace import model_request
            model_request(callback_context, llm_request)
            if getattr(self, "_capture_requests", False):
                value = getattr(llm_request.config, "system_instruction", None)
                callback_context.state["adkl:request:" + callback_context.agent_name] = {
                    "instruction": value if isinstance(value, str) else str(value),
                    "contentCount": len(llm_request.contents),
                    "evidence": "shared_before_model"}
        hooks["before_model_callback"].append(observe_model)
        return {kind: fns for kind, fns in hooks.items() if fns}

    def _make_skill_toolset(self, skills: dict[str, Any] | list[str]):
        """Build one native toolset for the skills reachable on this turn."""
        from google.adk.tools.skill_toolset import SkillToolset

        options = self._skill_catalog_options()
        if self._presentation_config or (self.doc.get("tools") or {}).get("core"):
            from adkl.presentation_runtime import PresentationSkillToolset
            return self._apply_skill_catalog(PresentationSkillToolset(
                skills=[self._skills[name] for name in skills if name in self._skills],
                additional_tools=list(self._tool_fns.values()),
                presentation_names={t.name for t in self._global_presentation_tools()},
                **options))

        names = skills if isinstance(skills, list) else list(skills)
        selected = [self._skills[name] for name in names if name in self._skills]
        return self._apply_skill_catalog(SkillToolset(
            skills=selected, additional_tools=list(self._tool_fns.values()), **options))

    def _skill_catalog_options(self) -> dict[str, Any]:
        """Native constructor options for skill_catalog: prompt.

        ADK 2.11 added `discovery_mode=SkillDiscoveryMode.EAGER`, which drops
        `list_skills` and injects the catalogue itself. It also deprecated the
        older route below (removing the tool to imply eager disclosure) and warns
        that a future release will stop injecting the catalogue for it. Use the
        native option wherever it exists.
        """
        if self._skill_catalog_mode() != "prompt":
            return {}
        from google.adk.tools import skill_toolset
        mode = getattr(skill_toolset, "SkillDiscoveryMode", None)
        return {} if mode is None else {"discovery_mode": mode.EAGER}

    def _apply_skill_catalog(self, toolset):
        """Honour skill_retrieval.skill_catalog on ADK without native EAGER mode.

        ADK decides where the catalogue goes from the toolset's OWN tool list:
        `SkillToolset.process_llm_request` appends the skills XML to the
        instruction only when no `ListSkillsTool` is present. So removing that
        tool is what moves the catalogue into the prompt, and it is a latency
        change rather than a cosmetic one -- with the tool present the model must
        spend a whole round trip calling `list_skills` before it can pick a skill.

        `tool_filter` is the trap. It is applied in `get_tools`, so the tool stays
        in `_tools`: the catalogue remains out of the prompt AND the tool is
        hidden from the model, leaving no way to discover a skill at all. This has
        to reach into `_tools`, and both branches above are covered because
        PresentationSkillToolset overrides only `get_tools`.
        """
        if self._skill_catalog_mode() != "prompt" or self._skill_catalog_options():
            # Native EAGER disclosure (ADK >= 2.11) was set at construction.
            return toolset
        from google.adk.tools.skill_toolset import ListSkillsTool
        tools = getattr(toolset, "_tools", None)
        if tools is None:
            # Loud, because the silent alternative is a catalogue that reaches the
            # model through neither route.
            raise ValueError(
                "skill_catalog: prompt needs SkillToolset._tools, which this ADK "
                "version does not expose. Pin ADK or set skill_catalog: tool.")
        toolset._tools = [t for t in tools if not isinstance(t, ListSkillsTool)]
        return toolset

    def _skill_catalog_mode(self) -> str:
        mode = getattr(self, "_skill_catalog", None)
        if mode is None:
            # Cached: this runs once per invocation via _delegate, and it is
            # reachable from both AgentRuntime and RuntimeBindings without either
            # having necessarily gone through _compose_callbacks.
            resolved = features.resolve(self.doc.get("features"))
            mode = resolved.config["skill_retrieval"]["skill_catalog"]
            cfg = resolved.config["skill_retrieval"]
            # Count the full eligible pool, not this invocation's selected slice.
            # Otherwise a large catalog ranked down to top-3 would bypass search.
            if ("skill_retrieval" in resolved.active
                    and len(set(self._skills) - set(cfg["excluded_skills"]))
                    < cfg["min_skills_for_retrieval"]):
                mode = "prompt"
            self._skill_catalog = mode
        return mode

    def _global_presentation_tools(self):
        # Pure display tools are agent-level. Bound domain tools retain native
        # skill activation; selecting a pack must not broaden domain access.
        bindings = self._presentation_config.get("bindings", {})
        names = list(dict.fromkeys([
            *[name for name in self._presentation_tools if name not in bindings],
            *((self.doc.get("tools") or {}).get("core") or []),
        ]))
        from google.adk.tools import FunctionTool
        return [self._tool_fns[name] if hasattr(self._tool_fns[name], "name")
                else FunctionTool(self._tool_fns[name]) for name in names]

    # --------------------------------------------------------------- widgets

    def _make_emitter(self):
        """After-agent callback: drain live registers, record why for the trace."""
        specs = self._widget_specs
        request_id = self._widget_cfg.get("request_id", "adkl")

        async def after_agent(callback_context):
            result = await widgets_mod.emit_staged(
                callback_context, specs,
                self._request_id(callback_context) if getattr(self, "_request_id", None) else request_id,
                sink=getattr(self, "_widget_sink", None),
                isolate_render_failures=self._widget_cfg.get("isolate_render_failures", False))
            # Stashed for the trace rather than logged: the console's whole job
            # is showing why a widget did or did not appear, and a log line in a
            # dev server is not where a reviewer will look.
            self._last_emit = result
            key = "adkl:widgets:" + callback_context.agent_name
            diagnostics = dict(callback_context.state.get(key, {}))
            diagnostics["gates"] = [
                {"key": gate.key, "passed": gate.passed, "gate": gate.gate,
                 "detail": gate.detail, "revived": gate.revived} for gate in result.gates]
            callback_context.state[key] = diagnostics
            # Native SDK consumers also receive a companion when the model
            # finishes without text. Only inspect this invocation's final text.
            has_text = any(
                event.invocation_id == callback_context.invocation_id
                and event.author == callback_context.agent_name
                and not getattr(event, "partial", False)
                and any(getattr(part, "text", None) and part.text.strip()
                        and not getattr(part, "thought", False)
                        for part in (getattr(event.content, "parts", None) or []))
                for event in callback_context.session.events)
            if result.emitted and result.companion and not has_text:
                # A host whose transport builds the companion turn from the state
                # delta must be able to say so. Returned content is a real model
                # event: it persists into conversation history, where the next
                # turn's model reads it as its own prior wording and imitates it.
                # For such a host the generic route is not a different delivery of
                # the same sentence, it is a behaviour change, so the choice
                # belongs to whoever owns the transport.
                sink = getattr(self, "_companion_sink", None)
                if sink is not None:
                    accepted = sink(callback_context, result.companion)
                    if inspect.isawaitable(accepted):
                        accepted = await accepted
                    if accepted is not None:
                        raise widgets_mod.WidgetError(
                            "companion sink must return None or raise")
                    return None
                from google.genai import types
                return types.Content(role="model", parts=[types.Part(text=result.companion)])
            return None

        return after_agent

    def _make_contract_injector(self):
        """Before-model callback: resolve the contract and append its block.

        This shared block describes the staged output. Conflicting authored
        instructions must be audited; callback ordering is not policy authority.
        """
        specs = self._widget_specs

        async def before_model(callback_context, llm_request):
            res = widgets_mod.resolve_contract(callback_context.state, specs)
            self._last_resolution = res
            callback_context.state["adkl:widgets:" + callback_context.agent_name] = {
                "contract": res.contract.value if res.contract else None,
                "sources": list(res.sources), "gates": []}
            block = widgets_mod.instruction_for(res.contract)
            if block and self._widget_cfg.get("inject_contract", True):
                try:
                    llm_request.append_instructions([block])
                except Exception:
                    # Never fail a turn over reply shaping: an unshaped reply is
                    # worse-looking, a failed turn is broken.
                    pass
            return None

        return before_model



class RuntimeBindings(NativeLifecycle):
    """Bind shared patterns to native skills/tools without a project scaffold.

    ``selector.match(text, state)`` must asynchronously return a MatchResult.
    A SkillRetrieval instance is the built-in implementation. Custom selectors
    own ranking only; the binding validates candidates and activation policy.
    ``prepare_skill(skill_name=..., context=..., reason=...)`` runs before native
    explicit/automatic loading. Return None or raise to prevent activation.
    ``widget_sink(context, widget_id, payload)`` replaces generic widget delivery;
    it must return None only after accepting the emission, or raise on failure.
    ``companion_sink(context, text)`` likewise replaces generic companion delivery:
    without it a companion the model did not write is returned as model content,
    which persists into conversation history. Same contract: None, or raise.
    """

    def __init__(self, *, skills, tools=(), feature_config=None, selector=None,
                 prepare_skill=None, widget_specs=(), widget_sink=None,
                 companion_sink=None,
                 request_id=None, core_tools=(), vectors=None, embedder=None, descriptions=None,
                 presentation=None, route_skill=None, seed_today=True,
                 on_finding=None, on_warning=None, root=None,
                 native_toolset=None, native_observation=None):
        from google.adk.tools import FunctionTool
        from google.adk.tools.base_tool import BaseTool

        from google.adk.tools.base_toolset import BaseToolset
        if native_toolset is not None and not isinstance(native_toolset, BaseToolset):
            raise TypeError("native_toolset must be a native ADK BaseToolset")
        if native_observation is not None and (native_toolset is None or not callable(native_observation)):
            raise TypeError("native_observation requires a native_toolset and must be callable")
        self._native_toolset = native_toolset
        self._native_observation = native_observation
        if not isinstance(seed_today, bool):
            raise TypeError("seed_today must be a boolean")
        self._seed_today = seed_today
        # `on_finding` and `root` were previously read off `self` with getattr in
        # _compose_callbacks, which only ever resolved for AgentRuntime -- so on
        # every host path they were None. That silently disabled the reporting of
        # output_cleanup/pii_redaction removals and prompt_state_injection's
        # missing-context notice, and made `from_file` slots a TypeError rather
        # than a refusal. They are constructor arguments here so a host can
        # supply them, and so their absence is a stated choice, not an accident.
        for name, fn in (("on_finding", on_finding), ("on_warning", on_warning)):
            if fn is not None and not callable(fn):
                raise TypeError(f"{name} must be callable")
        self._on_finding, self._on_warning = on_finding, on_warning
        self.root = None if root is None else pathlib.Path(root)
        self._skills = {}
        for skill in skills:
            if skill.name in self._skills:
                raise ValueError(f"Duplicate skill: {skill.name}")
            self._skills[skill.name] = skill
        self._tool_fns = {}
        domain_functions = {}
        for tool in tools:
            native = tool if isinstance(tool, BaseTool) else FunctionTool(tool)
            if native.name in self._tool_fns:
                raise ValueError(f"Duplicate tool: {native.name}")
            self._tool_fns[native.name] = native
            domain_functions[native.name] = tool
        self.doc = {"features": feature_config, "tools": {"core": list(core_tools)}}
        if presentation is not None:
            self.doc["presentation"] = presentation
        from adkl import presentation as presentation_mod
        self._presentation_config = presentation_mod.configuration(self.doc)
        self._presentation_tools = {}
        if self._presentation_config:
            from adkl.presentation_runtime import tools as pack_tools
            self._presentation_tools = pack_tools(self._presentation_config, domain_functions)
            conflicts = (self._presentation_tools.keys() & self._tool_fns.keys()) - self._presentation_config["bindings"].keys()
            if conflicts:
                raise ValueError(f"Domain tools conflict with presentation tools: {sorted(conflicts)}")
            self._tool_fns.update(self._presentation_tools)
        resolved = features.resolve(feature_config)
        capabilities.require_supported(resolved, "sdk")
        features.validate_skill_scope(resolved, self._skills)
        if native_toolset is not None:
            forbidden = set(resolved.active) & {"skill_retrieval", "skill_state_injection"}
            if forbidden or any(value is not None for value in (selector, vectors, embedder, prepare_skill, route_skill)):
                raise ValueError("native_toolset owns selection, activation and skill context; remove ADKL skill features/hooks")
            if self._presentation_config or core_tools:
                raise ValueError("native_toolset owns tool exposure; use host widget_specs instead of packs/core_tools")
            self._seed_today = False
        if vectors is not None or embedder is not None:
            if selector is not None or vectors is None or embedder is None:
                raise ValueError("Pass vectors and embedder together, or a selector")
            from adkl.retrieval import SkillRetrieval, config_from_dict
            selector = SkillRetrieval(config_from_dict(resolved.config["skill_retrieval"]),
                                      vectors, embedder, descriptions)
        if ("skill_retrieval" in resolved.active) != (selector is not None):
            raise ValueError("skill_retrieval requires a selector and vice versa")
        if selector is not None and not callable(getattr(selector, "match", None)):
            raise TypeError("selector must provide async match(text, state)")
        for name, hook in (("prepare_skill", prepare_skill), ("widget_sink", widget_sink),
                           ("companion_sink", companion_sink), ("route_skill", route_skill)):
            if hook is not None and not callable(hook):
                raise TypeError(f"{name} must be callable")
        if request_id is not None and not callable(request_id):
            raise TypeError("request_id must be a callable of callback context")
        declared = set(core_tools)
        for skill in self._skills.values():
            declared.update(skill.frontmatter.metadata.get("adk_additional_tools", []))
        missing = declared - self._tool_fns.keys()
        if missing:
            raise ValueError(f"Declared tools are not executable: {sorted(missing)}")
        if selector is not None:
            from adkl.retrieval import SkillRetrieval
            if isinstance(selector, SkillRetrieval):
                eligible = set(self._skills) - set(resolved.config["skill_retrieval"]["excluded_skills"])
                if eligible - selector.vectors.keys():
                    raise ValueError("Selector vectors must cover every eligible skill")
        self._retrieval = selector
        self._prepare_skill = prepare_skill
        self._widget_sink = widget_sink
        self._companion_sink = companion_sink
        self._request_id = request_id
        self._route_skill = route_skill
        self._widget_specs = list(widget_specs)
        if self._presentation_config:
            self._widget_specs.append(presentation_mod.spec())
        keys = [spec.key for spec in self._widget_specs]
        if len(keys) != len(set(keys)):
            raise ValueError("Widget spec keys must be unique")
        from adkl.staged_tool import StagedResultTool
        for tool in self._tool_fns.values():
            if isinstance(tool, StagedResultTool) and tool.spec not in self._widget_specs:
                raise ValueError(f"Staged tool {tool.name!r} requires its widget spec on the binding")
        self._widgets_active = "staged_widgets" in resolved.active
        self._widget_cfg = resolved.config.get("staged_widgets", {})
        if self._widget_specs and not self._widgets_active:
            raise ValueError("widget_specs require staged_widgets")
        if self._widgets_active and not self._widget_specs:
            raise ValueError("staged_widgets requires widget_specs")
        self._last_emit = self._last_resolution = None
        self._issued = False

    def _begin_native(self, callback_context):
        from adkl.runtime_state import begin_invocation
        begin_invocation(self, callback_context)

    async def _observe_native(self, callback_context, llm_request):
        """Observe after native preprocessing, including host cache short circuits.

        The provider must not rerank or mutate model inputs. Exceptions propagate;
        hosts wanting best-effort observation should handle their provider errors.
        """
        from adkl.runtime_state import agent_key, record_diagnostics
        value = {}
        if self._native_observation is not None:
            value = self._native_observation(callback_context)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, dict):
                raise TypeError("native_observation must return a diagnostic dict")
        current = dict(callback_context.state.get(agent_key("adkl:last_turn", callback_context), {}))
        current.update(value)
        current["execution_owner"] = "host"
        record_diagnostics(callback_context, current)

    def inspect(self):
        """Inspect declarations repeatedly without issuing constructor kwargs."""
        from adkl.inspection import snapshot
        return deepcopy(snapshot(self))

    def merge_into(self, existing_kwargs, *, after_tool_observers=(), before_model_observers=(),
                   final_model_transforms=(), after_model_transforms=()):
        """Return a new constructor dict, composing callbacks and retaining host tools.

        Like agent_kwargs, consumes this binding once. Inspect before composing;
        use a fresh binding for each agent. Host tools stay globally visible, so
        tools also owned by this binding are rejected instead of promoted.
        Dynamic host toolsets remain host-owned and are not enumerated here.
        """
        from google.adk.tools.skill_toolset import SkillToolset
        from adkl.skill_runtime import SelectedSkillToolset
        if not isinstance(existing_kwargs, Mapping):
            raise TypeError("existing_kwargs must be a mapping")
        result = dict(existing_kwargs)
        host_tools = result.pop("tools", []) or []
        if not isinstance(host_tools, (list, tuple)):
            raise TypeError("tools must be a sequence")
        names = set()
        skill_tools = {"list_skills", "search_skills", "load_skill", "load_skill_resource", "run_skill_script"}
        native_count = sum(tool is self._native_toolset for tool in host_tools)
        if self._native_toolset is not None and native_count > 1:
            raise ValueError("native_toolset must occur at most once")
        host_tools = [tool for tool in host_tools if tool is not self._native_toolset]
        for tool in host_tools:
            if isinstance(tool, (SkillToolset, SelectedSkillToolset)):
                raise ValueError("Remove existing skill toolsets before installing RuntimeBindings")
            name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
            if name and (name in self._tool_fns or name in names or name in skill_tools):
                raise ValueError(f"Host tool conflicts with another tool or binding-owned tool: {name}")
            if name:
                names.add(name)
        callbacks = {kind: result.pop(kind) for kind in CALLBACK_KINDS if kind in result}
        shared = self.agent_kwargs(callbacks=callbacks, after_tool_observers=after_tool_observers,
                                   before_model_observers=before_model_observers,
                                   final_model_transforms=final_model_transforms,
                                   after_model_transforms=after_model_transforms)
        result.update(shared)
        result["tools"] = [*host_tools, *shared["tools"]]
        return result

    def agent_kwargs(self, *, callbacks=None, after_tool_observers=(), before_model_observers=(),
                     final_model_transforms=(), after_model_transforms=()):
        """Return tools and lifecycle kwargs once, for one native constructor.

        Before hooks preserve native short circuits. After-tool observers see raw
        data; callbacks['after_tool_callback'] are sequential replacement
        transforms. Host after-agent callbacks run after shared emission.
        Do not install another shared adapter or pass duplicate callback kwargs.
        """
        if self._issued:
            raise ValueError("RuntimeBindings already issued constructor kwargs; create a new binding")
        hooks = self._compose_callbacks(callbacks or {}, after_tool_observers, before_model_observers,
                                        final_model_transforms, after_model_transforms)
        self._issued = True
        if self._native_toolset is not None:
            return {"tools": [self._native_toolset], **hooks}
        return {"tools": [self._selection, *self._global_presentation_tools()], **hooks}
