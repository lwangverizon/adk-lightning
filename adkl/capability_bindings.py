"""Unified Capability Selection over unchanged native ADK authoring objects.

No model router, graph DSL, private NodeTool import or replacement skill loader.
Runtime imports are deferred to keep authoring installations lightweight.
"""

from collections.abc import Mapping
import json
from typing import Any

from adkl.bindings import CALLBACK_KINDS, RuntimeBindings
from adkl.capability_contracts import CapabilityDescriptor


class CapabilityBindings(RuntimeBindings):
    """Bind Unified Capability Selection for native skills/workflows/subagents.

    Without retrieval the supplied catalog is the relevant, host-eligible set.
    Optional CapabilityRetrieval shares one shortlist across all capability kinds.
    Skill loading is explicit unless a host native_toolset owns preparation.
    A native owner with retrieval must consume the shared discovery contract;
    never layer an independently selecting skill toolset over this binding.
    Native workflow results return to the parent, unless the workflow explicitly
    owns completion through native actions. No answer ownership is inferred.
    """

    def __init__(self, *, skills=(), workflows=(), sub_agents=(),
                 skill_tools=(), tools=(), max_activated_skills=None,
                 feature_config=None, widget_specs=(), widget_sink=None,
                 companion_sink=None, request_id=None, on_warning=None,
                 on_finding=None, root=None, policy=None, on_event=None,
                 outcome_adapters=None, pre_emit=None, native_toolset=None,
                 retrieval=None, skill_lifecycle=None, retain_active_skills=False,
                 lifecycle=None, background_capabilities=(), tasks=None):
        from google.adk.agents import BaseAgent
        from google.adk.skills import Skill
        from google.adk.tools.base_tool import BaseTool
        from google.adk.tools.function_tool import FunctionTool
        from google.adk.tools.skill_toolset import SkillToolset
        from google.adk.workflow import Workflow

        for name, hook in (("on_event", on_event), ("pre_emit", pre_emit)):
            if hook is not None and not callable(hook):
                raise TypeError(f"{name} must be callable")
        if policy is not None and not all(callable(getattr(policy, name, None))
                                          for name in ("expose", "authorize")):
            raise TypeError("policy must implement expose and authorize")
        if native_toolset is not None and not isinstance(native_toolset, SkillToolset):
            raise TypeError("native_toolset must be a native SkillToolset")
        from adkl.capability_retrieval import CapabilityRetrieval
        if retrieval is not None and not isinstance(retrieval, CapabilityRetrieval):
            raise TypeError("retrieval must be CapabilityRetrieval")
        if (retrieval is not None or lifecycle is not None) and native_toolset is not None and not callable(
                getattr(native_toolset, "bind_capability_discovery", None)):
            raise TypeError("native_toolset must consume shared capability discovery")
        if type(retain_active_skills) is not bool:
            raise TypeError("retain_active_skills must be bool")
        if retain_active_skills and retrieval is None:
            raise ValueError("retain_active_skills requires retrieval")
        if skill_lifecycle is not None:
            if native_toolset is not None:
                raise ValueError("Configure skill_lifecycle on native_toolset")
            if max_activated_skills is not None:
                raise ValueError("Do not combine skill_lifecycle and max_activated_skills")
            from adkl.skill_lifecycle import validate_lifecycle_config
            validate_lifecycle_config(skill_lifecycle)
        self._skill_lifecycle = skill_lifecycle
        self._retain_active_skills = retain_active_skills
        self._capability_retrieval = retrieval
        from adkl.capability_lifecycle import CapabilityLifecycle
        if lifecycle is not None and not isinstance(lifecycle, CapabilityLifecycle):
            raise TypeError("lifecycle must be CapabilityLifecycle")
        self._lifecycle = lifecycle
        if lifecycle is not None and (skill_lifecycle is not None or retain_active_skills
                                      or max_activated_skills is not None):
            raise ValueError("Configure one explicit lifecycle owner")
        self._host_skill_toolset = native_toolset
        self._policy, self._on_event, self._pre_emit = policy, on_event, pre_emit
        self._outcomes = dict(outcome_adapters or {})
        if any(not callable(adapter) for adapter in self._outcomes.values()):
            raise TypeError("Outcome adapters must be callable")
        from adkl.background_tasks import BackgroundCapability, BackgroundTasks
        from adkl.background_adk import management_tools, submission_tool
        self._background_capabilities = tuple(background_capabilities)
        if tasks is not None and not isinstance(tasks, BackgroundTasks):
            raise TypeError("tasks must be BackgroundTasks")
        if self._background_capabilities and tasks is None:
            raise ValueError("background_capabilities require tasks")
        if any(not isinstance(cap, BackgroundCapability) for cap in self._background_capabilities):
            raise TypeError("background_capabilities require BackgroundCapability")
        self._task_tools = management_tools(tasks) if tasks is not None else []
        tools = (*tools, *(submission_tool(cap, tasks) for cap in self._background_capabilities))
        self._capability_skills = tuple(skills)
        self._workflows = tuple(workflows)
        self._agents = tuple(sub_agents)
        # Only explicit tools= entries are standalone. Native functions retain
        # their function-derived schema; tools owned by a skill stay scoped.
        self._tools = tuple(
            tool if isinstance(tool, BaseTool) else FunctionTool(tool)
            if callable(tool) else tool for tool in tools
        )
        self._issued = False
        self._descriptors = []
        names = set()
        reserved = {tool.name for tool in self._task_tools} | {"load_skill", "load_skill_resource", "list_skills",
                    "run_skill_script", "unload_skill", "search_skills", "transfer_to_agent"}
        for kind, objects, native_type in (
            ("skill", self._capability_skills, Skill),
            ("workflow", self._workflows, Workflow),
            ("agent", self._agents, BaseAgent),
            ("tool", self._tools, BaseTool),
        ):
            for obj in objects:
                if not isinstance(obj, native_type):
                    raise TypeError(f"{kind} requires native {native_type.__name__}")
                name = obj.name
                description = (obj.frontmatter.description if kind == "skill"
                               else obj.description)
                if name in names or name in reserved:
                    raise ValueError(f"Duplicate/reserved capability name: {name}")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError(f"Capability {name!r} requires a description")
                if kind == "workflow" and obj.input_schema is None:
                    raise ValueError(f"Workflow {name!r} requires input_schema")
                if kind == "agent" and obj.parent_agent is not None:
                    raise ValueError(f"Subagent {name!r} already has a parent")
                names.add(name)
                mode = getattr(obj, "mode", None)
                operation = ("load_skill" if kind == "skill" else
                             "transfer_to_agent" if kind == "agent" and mode in (None, "chat")
                             else name)
                self._descriptors.append(CapabilityDescriptor(name, kind, description, operation))
        if self._outcomes.keys() - names:
            raise ValueError("Outcome adapters must name registered capabilities")
        # Single-turn specialists are native callable operations and can use
        # the same request-local dispatch guard as workflows. Chat/task modes
        # can transfer/resume outside a tool call; do not claim those are scoped.
        if (policy is not None or retrieval is not None or lifecycle is not None) and any(getattr(agent, "mode", None) != "single_turn"
                                      for agent in self._agents):
            raise ValueError("Dynamic specialist policy requires mode='single_turn'; chat/task handoff is unsupported")
        # Exact native loader/resource/state behavior. No host-toolset subclass,
        # no rewriting SKILL.md and no duplicate selection mechanism.
        skill_tools = tuple(skill_tools)
        if not self._capability_skills and skill_tools:
            raise ValueError("skill_tools require at least one skill")
        scoped_names = set()
        for tool in skill_tools:
            name = tool.name if isinstance(tool, BaseTool) else getattr(tool, "__name__", None)
            if name is None:
                raise TypeError("skill_tools must be native BaseTool objects or functions")
            if name in names or name in reserved or name in scoped_names:
                raise ValueError(f"Duplicate/reserved scoped tool name: {name}")
            scoped_names.add(name)
        for skill in self._capability_skills:
            declared = skill.frontmatter.metadata.get("adk_additional_tools", [])
            if not isinstance(declared, list) or any(not isinstance(n, str) for n in declared):
                raise ValueError(f"Invalid adk_additional_tools for skill {skill.name!r}")
            if missing := set(declared) - scoped_names:
                raise ValueError(f"Missing tools for skill {skill.name!r}: {sorted(missing)}")
        options = {"additional_tools": list(skill_tools)} if skill_tools else {}
        if max_activated_skills is not None:
            options["max_activated_skills"] = max_activated_skills
        if skill_lifecycle is not None:
            options["lifecycle_config"] = skill_lifecycle
        # Share the existing lifecycle, not its selector. An empty native
        # toolset declares native ownership but is not installed when there are
        # no skills. There is exactly one binding and at most one skill loader.
        if native_toolset is not None:
            if max_activated_skills is not None:
                raise ValueError("Configure max_activated_skills on native_toolset")
            if {s.name for s in native_toolset.skills} != {s.name for s in self._capability_skills}:
                raise ValueError("native_toolset skills must match declared skills")
            native = native_toolset
        elif policy is not None or retrieval is not None or lifecycle is not None or on_event is not None or self._outcomes:
            from adkl.capability_execution import scoped_skill_toolset
            native = scoped_skill_toolset(self, self._capability_skills, list(skill_tools),
                                          {k: v for k, v in options.items() if k != "additional_tools"})
        else:
            native = SkillToolset(skills=list(self._capability_skills), **options)
        if (retrieval is not None or lifecycle is not None) and native_toolset is not None:
            from adkl.capability_execution import guarded_tool
            native.bind_capability_discovery(
                self.discover, lambda tool, owners: guarded_tool(self, tool, owners))
        self._skill_toolset = native if self._capability_skills else None
        if self._lifecycle is not None:
            self._lifecycle.bind(tuple(self._descriptors), skill_owner=self._skill_toolset)
        super().__init__(
            skills=self._capability_skills, tools=[*skill_tools, *self._tools],
            native_toolset=native,
            feature_config=feature_config if feature_config is not None else {"pipeline": []},
            widget_specs=widget_specs, widget_sink=widget_sink,
            companion_sink=companion_sink, request_id=request_id,
            on_warning=on_warning, on_finding=on_finding, root=root,
        )

    def _make_emitter(self):
        emitter = super()._make_emitter()

        async def emit(callback_context):
            if self._pre_emit is not None:
                from adkl.capability_execution import resolve
                from adkl.capability_policy import CapabilityDecision
                from adkl.widgets import clear_turn_flags
                try:
                    decision = await resolve(self._pre_emit(callback_context))
                    if not isinstance(decision, CapabilityDecision):
                        raise TypeError("pre_emit must return CapabilityDecision")
                except BaseException:
                    # Validation is before delivery. Also disarm on cancellation;
                    # ordinary after-agent hooks are not a finally boundary.
                    clear_turn_flags(callback_context.state, self._widget_specs)
                    callback_context.state["adkl:widgets:" + callback_context.agent_name] = {
                        "denied": True, "reason": "validation_error", "gates": []}
                    raise
                if not decision.allowed:
                    clear_turn_flags(callback_context.state, self._widget_specs)
                    callback_context.state["adkl:widgets:" + callback_context.agent_name] = {
                        "denied": True, "reason": decision.reason, "gates": []}
                    return None
            return await emitter(callback_context)
        return emit

    def _capability_snapshot(self) -> dict[str, Any]:
        """Declaration-only extension consumed by shared native inspection."""
        from adkl.inspection import workflow_graph
        return {
            "capabilities": [vars(d).copy() for d in self._descriptors],
            "workflowGraphs": [workflow_graph(workflow) for workflow in self._workflows],
            "selection": "parent_model",
            "retainActiveSkills": self._retain_active_skills,
            "capabilityLifecycle": (
                "skills_and_callable_continuation"
                if self._lifecycle is not None else "disabled"),
            "skillLifecycle": "native_configured" if self._skill_lifecycle is not None else "native_owner_default",
            "retrieval": "shared_capability_shortlist" if self._capability_retrieval else "host_supplied_catalog",
            "execution": "native_adk",
            "autoActivation": "host_owned" if self._host_skill_toolset else False,
            "skillExecutionOwnership": "host" if self._host_skill_toolset else "adkl",
            "skillPolicyOwnership": (
                "shared_dispatch_host_loader" if self._host_skill_toolset and self._capability_retrieval
                else "host" if self._host_skill_toolset else "adkl"),
            "eligibilityPolicy": self._policy is not None,
            "executionEvents": self._on_event is not None,
            "preEmissionValidation": self._pre_emit is not None,
            "completion": "native_target_contract",
            "standaloneTools": [tool.name for tool in self._tools],
            "backgroundCapabilities": [vars(cap).copy() for cap in self._background_capabilities],
            "taskManagementTools": [tool.name for tool in self._task_tools],
            "backgroundExecution": {
                "enabled": bool(self._task_tools), "dispatch": "task_backend",
                "worker": "host_managed", "automaticResume": False,
                "yaml": False, "console": False, "export": False,
            },
            "skillTools": {
                skill.name: list(skill.frontmatter.metadata.get("adk_additional_tools", []))
                for skill in self._capability_skills
            },
        }

    async def discover(self, context: Any) -> set[str]:
        """Return a shared relevance shortlist intersected with current eligibility."""
        from adkl.capability_execution import policy_eligible
        names = await policy_eligible(self, context)
        if self._capability_retrieval is None:
            return names
        catalog = tuple(d for d in self._descriptors if d.name in names)
        selected = await self._capability_retrieval.select(context, catalog)
        retained = set()
        if self._retain_active_skills:
            from adkl.skill_lifecycle import active_skill_names
            skills = {d.name for d in catalog if d.kind == "skill"}
            retained = set(active_skill_names(self._skill_toolset, context)) & skills
        if self._lifecycle is not None:
            retained.update(self._lifecycle.retained(context, catalog))
        from adkl.capability_contracts import CapabilitySelection
        from adkl.console_trace import discovery
        selected = CapabilitySelection(tuple(dict.fromkeys((*selected.names, *sorted(retained)))),
                                       selected.fallback)
        discovery(context, catalog, selected)
        return set(selected.names)

    async def prepare_request(self, callback_context, llm_request) -> None:
        """Expose eligible descriptors; dispatch guards recheck current policy."""
        from adkl.capability_execution import eligible, policy_eligible
        if self._lifecycle is not None:
            allowed = await policy_eligible(self, callback_context)
            self._lifecycle.reconcile(callback_context,
                tuple(d for d in self._descriptors if d.name in allowed))
        names = await eligible(self, callback_context)
        catalog = json.dumps([vars(d) for d in self._descriptors if d.name in names
                              and not (d.kind == "skill" and (
                                  (self._host_skill_toolset and not self._capability_retrieval)
                                  or getattr(self._skill_toolset, "emits_capability_skill_catalog", False)))], ensure_ascii=False)
        # The native toolset handles skill-owned declarations. Standalone tools
        # and workflow operations are narrowed here AND checked in their adapter.
        excluded = {d.operation for d in self._descriptors
                    if d.name not in names and d.kind in {"workflow", "tool", "agent"}}
        for name in excluded:
            llm_request.tools_dict.pop(name, None)
        for group in llm_request.config.tools or []:
            if getattr(group, "function_declarations", None):
                group.function_declarations = [d for d in group.function_declarations
                                               if d.name not in excluded]
        # ADK creates single-turn agent tools during LlmAgent construction.
        # Decorate only the outgoing request's canonical dispatch mapping, not
        # the shared agent tree or private native wrapper fields. Declarations
        # and native run_node/plugin behavior remain unchanged.
        if self._policy is not None or self._capability_retrieval is not None or self._lifecycle is not None or self._on_event is not None or self._outcomes:
            from adkl.capability_execution import guarded_tool
            targets = [*self._workflows, *(agent for agent in self._agents
                       if getattr(agent, "mode", None) == "single_turn")]
            for target in targets:
                if target.name in names:
                    tool = llm_request.tools_dict.get(target.name)
                    if tool is not None:
                        llm_request.tools_dict[target.name] = guarded_tool(self, tool, [target.name])
        llm_request.append_instructions([
            "Choose among the following peer capabilities by their descriptions. "
            "Do not prefer a skill just because it is a skill. You may answer "
            "directly or clarify when no capability is needed. For a skill, "
            "call load_skill(skill_name=...) before following its instructions. "
            "Host-prepared/auto-loaded skills need no load_skill call; follow "
            "their supplied procedure directly. The native skill catalog and "
            "this catalog are peers, not a preference order. "
            "For a workflow, invoke its named tool with the declared arguments "
            "directly; do not load it as a skill or invent its internal steps. "
            "For a chat specialist, use transfer_to_agent(agent_name=...). "
            "Task/single-turn specialists use their native named operation. "
            "Explicit standalone tools perform their described operation. "
            "A capability selection is not authorization. Native tool policies "
            "and completion contracts still apply.\n"
            "<capabilities>\n" + catalog + "\n</capabilities>"
        ])

    def agent_kwargs(self, *, callbacks=None, after_tool_observers=(),
                     before_model_observers=(), final_model_transforms=(),
                     after_model_transforms=()) -> dict[str, Any]:
        """Issue one native constructor with the shared callback/staging lifecycle.

        ADK converts workflows into callable tools. Catalog preparation follows
        shared request processing and precedes final host transforms/observers.
        No separate router model, private NodeTool adapter or second emitter is installed.
        """
        callbacks = dict(callbacks or {})
        if callbacks.keys() - CALLBACK_KINDS:
            raise ValueError("callbacks must contain supported native callback fields only")
        for value in callbacks.values():
            chain = value if isinstance(value, (list, tuple)) else [value]
            if any(isinstance(getattr(fn, "__self__", None), RuntimeBindings)
                   for fn in chain):
                raise ValueError("Remove existing ADKL lifecycle callbacks before binding")
        if not isinstance(final_model_transforms, (list, tuple)) or any(
                not callable(fn) for fn in final_model_transforms):
            raise TypeError("final_model_transforms must be a sequence of callables")
        kwargs = super().agent_kwargs(
            callbacks=callbacks, after_tool_observers=after_tool_observers,
            before_model_observers=before_model_observers,
            final_model_transforms=[self.prepare_request, *final_model_transforms],
            after_model_transforms=after_model_transforms,
        )
        from adkl.capability_execution import guarded_tool
        controlled = (self._policy is not None or self._capability_retrieval is not None
                      or self._lifecycle is not None or self._on_event is not None or bool(self._outcomes))
        kwargs["tools"] = ([self._skill_toolset] if self._skill_toolset else []) + list(
            self._workflows
        ) + ([guarded_tool(self, tool, [tool.name]) for tool in self._tools]
             if controlled else list(self._tools)) + self._task_tools
        kwargs["sub_agents"] = list(self._agents)
        return kwargs

    def merge_into(self, existing_kwargs, *, after_tool_observers=(),
                   before_model_observers=(), final_model_transforms=(),
                   after_model_transforms=()) -> dict[str, Any]:
        """Preserve host configuration; require explicit capability/tool ownership.

        Register tools and specialists on this binding rather than retaining a
        second catalog from host kwargs. Validation does not consume the binding
        or mutate the host dictionary. Native construction parents subagents.
        """
        if not isinstance(existing_kwargs, Mapping):
            raise TypeError("existing_kwargs must be a mapping")
        result = dict(existing_kwargs)
        for field in ("tools", "sub_agents"):
            value = result.pop(field, None)
            if value is not None and (not isinstance(value, (list, tuple)) or value):
                raise ValueError(f"Register {field} on CapabilityBindings, not host kwargs")
        callbacks = {kind: result.pop(kind) for kind in CALLBACK_KINDS if kind in result}
        result.update(self.agent_kwargs(
            callbacks=callbacks, after_tool_observers=after_tool_observers,
            before_model_observers=before_model_observers,
            final_model_transforms=final_model_transforms,
            after_model_transforms=after_model_transforms,
        ))
        return result
