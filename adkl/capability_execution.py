"""Internal native adapters. Public policy types live in capability_policy.

Runtime imports are deferred. No private ADK NodeTool/cache mutation or executor.
"""
import asyncio
from copy import deepcopy
import inspect
import logging
from dataclasses import asdict

from adkl.capability_policy import CapabilityDecision, CapabilityEvent, CapabilityOutcome, CapabilityRequest

logger = logging.getLogger(__name__)


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


async def eligible(binding, context):
    return await binding.discover(context)


async def policy_eligible(binding, context):
    names = {d.name for d in binding._descriptors}
    if binding._policy is None:
        return names
    result = await resolve(binding._policy.expose(context, tuple(binding._descriptors)))
    if not isinstance(result, (list, tuple, set, frozenset)) or any(
            not isinstance(name, str) for name in result):
        raise TypeError("expose must return a collection of capability names")
    if not set(result) <= names:
        raise ValueError("expose returned unregistered capabilities")
    return set(result)


async def notify(binding, context, request, status, reason):
    event = CapabilityEvent(1, context.invocation_id, context.agent_name,
                            str(getattr(context, "function_call_id", "") or "").split("__thought__", 1)[0][:128],
                            request.capability, request.kind, request.operation, status, reason)
    from adkl.console_trace import capability_event
    capability_event(event)
    # Diagnostic retention is bounded; execution never consults this history.
    key = f"temp:adkl_capability_events:{context.agent_name}"
    context.state[key] = [*(context.state.get(key) or [])[-63:], asdict(event)]
    if binding._on_event is not None:
        try:
            await resolve(binding._on_event(event))
        except Exception:
            logger.warning("Capability event observer failed")


async def allowed(binding, context, request):
    try:
        # Releasing a registered skill is cleanup, not relevance selection.
        # Host authorization still applies, including after eligibility revocation.
        exposed = ({d.name for d in binding._descriptors if d.kind == "skill"}
                   if request.operation == "unload_skill"
                   else await eligible(binding, context))
        if request.capability not in exposed:
            return CapabilityDecision(False, "not_eligible")
        if request.kind == "skill" and request.operation not in {
                "load_skill", "unload_skill", "list_skills", "load_skill_resource", "run_skill_script"}:
            from adkl.skill_lifecycle import active_skill_names
            active = active_skill_names(binding._skill_toolset, context)
            if request.capability not in active:
                return CapabilityDecision(False, "skill_not_active")
        if binding._policy is None:
            return CapabilityDecision(True, "eligible")
        result = await resolve(binding._policy.authorize(context, request))
        if not isinstance(result, CapabilityDecision):
            raise TypeError("authorize must return CapabilityDecision")
        return result
    except Exception:
        logger.warning("Capability policy failed closed")
        return CapabilityDecision(False, "policy_error")


async def execute(binding, context, request, operation):
    await notify(binding, context, request, "requested", "model_request")
    decision = await allowed(binding, context, request)
    if not decision.allowed:
        await notify(binding, context, request, "denied", decision.reason)
        return {"error": "Capability unavailable", "error_code": "CAPABILITY_DENIED",
                "reason": decision.reason}
    lifecycle = binding._lifecycle
    if lifecycle is not None and lifecycle.running(context, request.capability):
        await notify(binding, context, request, "denied", "capability_running")
        return {"error": "Capability already running", "error_code": "CAPABILITY_DENIED",
                "reason": "capability_running"}
    token = lifecycle.started(context, request) if lifecycle is not None else None
    try:
        await notify(binding, context, request, "started", "authorized")
        result = await operation()
        adapter = binding._outcomes.get(request.capability)
        outcome = await resolve(adapter(request, result)) if adapter else CapabilityOutcome()
        if not isinstance(outcome, CapabilityOutcome):
            raise TypeError("Outcome adapter must return CapabilityOutcome")
        if lifecycle is not None:
            lifecycle.finished(context, request, token, result=result,
                               continuation=outcome.continuation)
    except asyncio.CancelledError:
        if lifecycle is not None:
            lifecycle.finished(context, request, token)
        await notify(binding, context, request, "cancelled", "cancelled")
        raise
    except Exception:
        if lifecycle is not None:
            lifecycle.finished(context, request, token)
        await notify(binding, context, request, "failed", "execution_error")
        raise
    await notify(binding, context, request, outcome.status, outcome.reason)
    return result


def guarded_tool(binding, tool, owners):
    from google.adk.tools.base_tool import BaseTool

    owners = tuple(owners)

    class GuardedTool(BaseTool):
        def __init__(self):
            super().__init__(name=tool.name, description=tool.description,
                             is_long_running=tool.is_long_running,
                             custom_metadata=tool.custom_metadata,
                             response_scheduling=tool.response_scheduling)

        def _get_declaration(self):
            return tool._get_declaration()

        async def process_llm_request(self, *, tool_context, llm_request):
            await tool.process_llm_request(tool_context=tool_context, llm_request=llm_request)
            # Native preparation can register the original. Keep preparation,
            # but never let it replace the authorized dispatch target.
            if self.name in llm_request.tools_dict:
                llm_request.tools_dict[self.name] = self

        async def check_require_confirmation(self, args, tool_context):
            return await tool.check_require_confirmation(args, tool_context)

        async def run_async(self, *, args, tool_context):
            candidates = owners
            if tool.name in {"load_skill", "unload_skill", "load_skill_resource", "run_skill_script"}:
                candidates = [args.get("skill_name", "")]
            # Select an eligible active owner for shared tools, never trust an
            # arbitrary model-supplied owner. Do not execute a shared tool twice.
            from adkl.skill_lifecycle import active_skill_names
            active = active_skill_names(binding._skill_toolset, tool_context)
            try:
                names = (set(candidates) if tool.name == "unload_skill"
                         else await eligible(binding, tool_context))
            except Exception:
                names = set()
            owner = next((n for n in reversed(active) if n in candidates and n in names),
                         next((n for n in candidates if n in names), candidates[0] if candidates else ""))
            descriptor = next((d for d in binding._descriptors if d.name == owner), None)
            request = CapabilityRequest(owner, descriptor.kind if descriptor else "skill",
                                        tool.name, deepcopy(args))
            return await execute(binding, tool_context, request,
                                 lambda: tool.run_async(args=args, tool_context=tool_context))

    return GuardedTool()


def scoped_skill_toolset(binding, skills, tools, options):
    from google.adk.tools.skill_toolset import SkillToolset

    class ScopedSkills(SkillToolset):
        """Stateless delegates scoped before native enumeration and dispatch.

        All declarations are local and no executor/registry resources are owned.
        No invocation context is retained by the singleton toolset.
        """
        def __init__(self):
            # Inherit the native skill toolset's dynamic-enumeration contract.
            # A plain BaseToolset caches the first tool list for an invocation
            # in the host distribution, hiding newly activated tools from host
            # before-model callbacks. Never mutate that private cache flag.
            if "lifecycle_config" in options:
                from google.adk.tools.skill_toolset import SkillDiscoveryMode
                options["discovery_mode"] = SkillDiscoveryMode.EAGER
            self.emits_capability_skill_catalog = "lifecycle_config" in options
            super().__init__(skills=list(skills), additional_tools=tools, **options)

        async def delegate(self, context):
            names = await eligible(binding, context)
            if "lifecycle_config" in options:
                # Native cloning preserves lifecycle/discovery/resource settings.
                return self.clone_with_updated_skills([s for s in skills if s.name in names])
            return SkillToolset(skills=[s for s in skills if s.name in names],
                                additional_tools=tools, **options)

        async def get_tools(self, readonly_context=None):
            if readonly_context is None:
                return []
            native = await self.delegate(readonly_context)
            result = []
            for tool in await native.get_tools(readonly_context):
                if tool.name == "list_skills":
                    # Catalog already contains all eligible descriptions; no
                    # redundant model hop, nor stale list closure at dispatch.
                    continue
                owners = [s.name for s in skills if tool.name in
                          s.frontmatter.metadata.get("adk_additional_tools", [])]
                result.append(guarded_tool(binding, tool, owners))
            return result

        async def process_llm_request(self, *, tool_context, llm_request):
            native = await self.delegate(tool_context)
            await native.process_llm_request(tool_context=tool_context, llm_request=llm_request)

        async def close(self):
            await super().close()

    return ScopedSkills()
