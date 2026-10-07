"""Invocation-scoped selection and context over native ADK SkillToolset.

This module is runtime-only. Selection lives in ADK callbacks so an ordinary
Runner executes the same contract as console chat, without mutating agent.tools.
"""
from __future__ import annotations

import json
import inspect
import math
from datetime import date
from dataclasses import replace

from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.base_tool import BaseTool

from adkl import features
from adkl.retrieval import ACTIVATED_KEY, Match, MatchResult, small_catalog_result
from adkl.runtime_state import agent_key, record_diagnostics

SELECTION = "temp:adkl_selection"
DIAGNOSTICS = "adkl:last_turn"
# Legacy internal constant; execution reads the public pin_grace_turns option.
PIN_GRACE_LIMIT = 5
PIN_GRACE_KEY = "adkl:pin_grace:{agent}"
# Our private record of what WE activated, for the native_activation modes that
# stop treating ADK's shared `_adk_activated_skill_{agent}` as ours to overwrite.
# That key has three writers -- ADK's own `load_skill` tool, which only ever
# APPENDS to it (skill_toolset.py: `if skill_name not in activated_skills:
# append`, with no eviction, cap or LRU anywhere), any host hook that rescues a
# skill we dropped, and this module, which derives its value from THIS turn's
# ranking and so is the only one of the three that ever SHRINKS the list.
# Reading our continuation history back out of the shared key would fold their
# appends into ours, which is exactly what makes "merge" indistinguishable from
# "replace", so the two views have to be separate.
OWN_ACTIVATED_KEY = "adkl:activated_skills:{agent}"


class SelectedSkillToolset(BaseToolset):
    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        # Native activation changes between model calls in the same invocation.
        self._use_invocation_cache = False
        self.resolved = features.resolve(runtime.doc.get("features"))
        self.contexts = (self.resolved.config["skill_state_injection"]["skills"]
                         if "skill_state_injection" in self.resolved.active else {})
        features.validate_skill_scope(self.resolved, runtime._skills)

    def begin(self, callback_context):
        from adkl.runtime_state import begin_invocation
        begin_invocation(self.runtime, callback_context)

    async def prepare(self, callback_context):
        ctx = callback_context
        text = " ".join(p.text for p in (getattr(ctx.user_content, "parts", None) or [])
                        if getattr(p, "text", None))
        # Refresh only our own default. Hosts can explicitly disable ownership;
        # replacing the value also relinquishes it. Old, unmarked state is host
        # owned, since we cannot know who wrote it before this contract existed.
        if getattr(self.runtime, "_seed_today", True):
            current = ctx.state.get("today")
            owned = ctx.state.get("adkl:default_today")
            if current is None or (owned is not None and current == owned):
                value = date.today().isoformat()
                ctx.state["today"] = value
                ctx.state["adkl:default_today"] = value
            elif owned is not None:
                ctx.state["adkl:default_today"] = None
        cfg = self.resolved.config["skill_retrieval"]
        eligible = set(self.runtime._skills) - set(cfg["excluded_skills"])
        catalog = (small_catalog_result(eligible, cfg["min_skills_for_retrieval"])
                   if self.runtime._retrieval is not None else None)
        if catalog is not None:
            result = catalog
        elif self.runtime._retrieval is not None:
            # `scope` is passed only to a selector that advertises accepting it,
            # so the two-argument `match(text, state)` host protocol is unchanged.
            if getattr(self.runtime._retrieval, "accepts_scope", False):
                result = await self.runtime._retrieval.match(
                    text, ctx.state, scope=ctx.agent_name)
            else:
                result = await self.runtime._retrieval.match(text, ctx.state)
        else:
            excluded = self.resolved.config["skill_retrieval"]["excluded_skills"]
            result = MatchResult([Match(name, 0, "configured")
                                  for name in self.runtime._skills if name not in excluded])
        # Strategies supply ranking, never expand the configured skill surface.
        eligible = set(self.runtime._skills) - set(
            self.resolved.config["skill_retrieval"]["excluded_skills"])
        # One message for five predicates left a host guessing which rule it broke
        # across "invalid" and "unavailable". Each is named, with the offending
        # names, because this raise runs at before-agent index 0 and so aborts the
        # whole callback chain -- the turn is already lost; the least it can do is
        # say why. Repeats are DEDUPED rather than refused: native ADK treats a
        # repeated candidate as cosmetic, so a host's own selector or pin table
        # legitimately produces one (two state keys naming one skill), and killing
        # the turn for it makes an unrelated host-side edit a customer-visible
        # outage. Ranking is order-preserving, so the first occurrence wins.
        if not isinstance(result, MatchResult):
            raise ValueError(
                f"Selector must return a MatchResult, got {type(result).__name__}")
        if (not isinstance(result.pinned, (set, frozenset))
                or any(not isinstance(name, str) for name in result.pinned)
                or result.pinned - set(result.names)):
            raise ValueError("Selector pins must name exposed candidates")
        if len(result.names) != len(set(result.names)):
            pins = frozenset(result.pinned) | frozenset(
                m.skill for m in result.matches if m.source == "pinned")
            seen: set[str] = set()
            keep = [m for m in result.matches
                    if not (m.skill in seen or seen.add(m.skill))]
            # dataclasses.replace, NOT MatchResult(keep, ...): rebuilding
            # positionally would silently drop `fallback`, `auto_activated` and
            # `ranking_margin`. Losing `fallback` is the worst of the three --
            # downstream logic reads it to mean "ranking failed, all skills
            # exposed", so a dedup would quietly turn a fallback turn into a
            # confident one.
            result = replace(result, matches=keep, pinned=pins)
        if unavailable := sorted(set(result.names) - eligible):
            raise ValueError(
                f"Selector returned skills this agent does not serve: "
                f"{', '.join(unavailable)}")
        if bad := sorted(m.skill for m in result.matches
                         if not isinstance(m.score, (float, int))
                         or not math.isfinite(m.score)):
            raise ValueError(
                f"Selector returned a non-finite or non-numeric score for: "
                f"{', '.join(bad)}")
        if not math.isfinite(result.margin):
            raise ValueError(f"Selector returned a non-finite margin: {result.margin!r}")
        # Do not mutate a host selector's cached result during continuation.
        result = replace(result, matches=list(result.matches), pinned=frozenset(result.pinned))
        cfg = self.resolved.config["skill_retrieval"]
        routed = None
        route = getattr(self.runtime, "_route_skill", None)
        if route is not None and cfg["auto_activate"]:
            routed = route(text=text, state=ctx.state)
            if inspect.isawaitable(routed):
                routed = await routed
            if routed is not None:
                if (not isinstance(routed, str) or routed not in result.names
                        or routed in cfg.get("auto_activate_exclude", [])):
                    raise ValueError("Explicit route must select an eligible, exposed, auto-activatable skill")
                # A trusted domain route is distinct from embedding confidence.
                result = replace(result, auto_activated=routed)
        if result.auto_activated:
            winner = result.matches[0] if result.matches else None
            if routed is None and (not cfg["auto_activate"] or result.fallback or winner is None
                    or result.auto_activated != winner.skill or winner.source != "embedding"
                    or winner.skill in cfg.get("auto_activate_exclude", [])
                    or winner.score < cfg["auto_activate_min_score"]
                    or result.margin < cfg["auto_activate_min_margin"]):
                raise ValueError("Selector violated automatic activation policy")
        key = ACTIVATED_KEY.format(agent=ctx.agent_name)
        own_key = OWN_ACTIVATED_KEY.format(agent=ctx.agent_name)
        cfg = self.resolved.config["skill_retrieval"]
        mode = cfg["native_activation"]
        # Under "replace" we are the only writer we acknowledge, so the shared key
        # IS our history and nothing changes. Under merge/own we read our own
        # view, falling back to the shared key the first turn so switching mode
        # mid-session does not drop an already-active skill.
        own = None if mode == "replace" else ctx.state.get(own_key)
        previous = list(ctx.state.get(key, []) if own is None else own)
        if cfg["continue_active"] and self.runtime._retrieval is not None:
            top = result.matches[0] if result.matches else None
            confident = routed is not None or (not result.fallback and top is not None and top.source == "embedding"
                         and top.score >= cfg["auto_activate_min_score"]
                         and result.margin >= cfg["auto_activate_min_margin"])

            if confident:
                pins = {m.skill for m in result.matches if m.source == "pinned"}
                pins.update(result.pinned)
                # A fired pin can already be an embedding candidate.
                from adkl.retrieval import StatePin
                pins.update(pin["skill"] for pin in cfg["state_pins"]
                            if StatePin(**pin).fires(ctx.state.get(pin["state_key"])))
                winner = routed or top.skill
                grace_key = PIN_GRACE_KEY.format(agent=ctx.agent_name)
                grace = dict(ctx.state.get(grace_key, {}))
                survivors = []
                for name in previous:
                    if name == winner:
                        grace.pop(name, None)
                        survivors.append(name)
                    elif name in pins:
                        remaining = min(grace.get(name, cfg["pin_grace_turns"]), cfg["pin_grace_turns"])
                        if remaining > 0:
                            grace[name] = remaining - 1
                            survivors.append(name)
                        else:
                            # Grace exhausted -- let it drop, same as an
                            # unpinned skill the ranker no longer surfaces.
                            grace.pop(name, None)
                    else:
                        grace.pop(name, None)
                previous = survivors
                ctx.state[grace_key] = grace
            else:
                # Bounded continuation retains native activation, not an arbitrary
                # ranking winner. Topic transitions still use the fresh ranking.
                previous = previous[-cfg["max_activated_skills"]:]
                for name in previous:
                    if name in eligible and name not in result.names:
                        result.matches.append(Match(name, 0, "continuation"))
        ctx.state[agent_key(SELECTION, ctx)] = result.names
        activated = [name for name in previous if name in result.names]
        if result.auto_activated and result.auto_activated not in activated:
            # Reuse the native loader, including metadata state interpolation.
            loader = next(t for t in await self._tools(ctx, reason="automatic")
                          if t.name == "load_skill")
            loaded = await loader.run_async(args={"skill_name": result.auto_activated}, tool_context=ctx)
            if "error" in loaded:
                raise ValueError(loaded["error"])
            activated.append(result.auto_activated)
        if mode != "replace":
            ctx.state[own_key] = activated
        if mode == "replace":
            ctx.state[key] = activated
        elif mode == "merge":
            # Keep the entries we did not choose, appended AFTER ours: ADK's
            # loader appends, so the list reads least-recent-first, and putting
            # ours first preserves the ordering our ranking implies while leaving
            # the other writers' skills present for whoever reads the shared key.
            # This widens that key, not our own surface -- both `before_model`
            # and `_delegate` still filter by SELECTION, so a skill we did not
            # rank this turn gets neither its procedure nor its tools from us.
            ctx.state[key] = activated + [n for n in ctx.state.get(key, [])
                                          if n not in activated]
        # "own": we never write the shared key. Native activation, the loader's
        # appends and any host rescue hook keep it entirely; we only ever read it.
        record_diagnostics(ctx, {
            "matches": [{"skill": m.skill, "score": round(m.score, 4), "source": m.source}
                        for m in result.matches],
            "picked": (result.names[0] if result.names and not result.fallback
                       and catalog is None else None),
            "retrieval_mode": "all_descriptions" if catalog is not None else "retrieval",
            "auto_activated": result.auto_activated,
            "activation_source": "route" if routed else "embedding" if result.auto_activated else None,
            "margin": result.margin, "fallback": result.fallback,
            "activated": activated, "model_calls": 0,
            "declared_tools": self._declared_tools(activated),
        })

    def _declared_tools(self, names, seen=None) -> list:
        """Tool names the given skills declare, in first-seen order.

        Nothing else records this: the toolset is rebuilt per invocation from
        `SELECTION` and never names its tools, so a reader cannot distinguish
        "no tool was bound" from "the tool surface was never reported".
        Resolving the names against a catalog belongs to the caller, which is
        the side that knows one -- this only says what the skills asked for.
        """
        declared = list(seen or [])
        for name in names:
            skill = self.runtime._skills.get(name)
            meta = getattr(getattr(skill, "frontmatter", None), "metadata", None) or {}
            for tool in meta.get("adk_additional_tools") or []:
                if tool not in declared:
                    declared.append(tool)
        return declared

    def _delegate(self, ctx):
        # A fresh local-only native toolset avoids mutable cross-invocation caches.
        # Returned native tools retain their delegate for the duration of a call.
        names = ctx.state.get(agent_key(SELECTION, ctx), []) if ctx is not None else []
        return self.runtime._make_skill_toolset(names)

    async def before_tool(self, tool, args, tool_context):
        prepare = getattr(self.runtime, "_prepare_skill", None)
        if prepare is not None and tool.name == "load_skill":
            name = args.get("skill_name")
            if name in tool_context.state.get(agent_key(SELECTION, tool_context), []):
                await prepare_once(prepare, name, tool_context, "explicit")
        return None

    async def _tools(self, context, reason="explicit"):
        tools = await self._delegate(context).get_tools(context)
        prepare = getattr(self.runtime, "_prepare_skill", None)
        if prepare is not None:
            tools = [PreparedLoadTool(t, prepare, reason) if t.name == "load_skill" else t
                     for t in tools]
        return tools

    async def get_tools(self, readonly_context=None):
        return await self._tools(readonly_context)

    async def process_llm_request(self, *, tool_context, llm_request):
        await self._delegate(tool_context).process_llm_request(
            tool_context=tool_context, llm_request=llm_request)

    async def before_model(self, callback_context, llm_request):
        ctx = callback_context
        selected = ctx.state.get(agent_key(SELECTION, ctx), [])
        active = [name for name in ctx.state.get(ACTIVATED_KEY.format(agent=ctx.agent_name), [])
                  if name in selected]
        prepare = getattr(self.runtime, "_prepare_skill", None)
        blocks = []
        projected = {}
        missing = []
        for name in active:
            skill = self.runtime._skills[name]
            # A skill can be `active` here without ever going through
            # `before_tool` -- automatic activation hoists the body directly
            # and calls no tool at all, so a host hook that fills a
            # placeholder from a `before_tool_callback` (the only mechanism
            # ADK gives an explicit `load_skill` call) never fires for it.
            # `prepare_once` is idempotent per turn, so this is a no-op for a
            # skill already prepared via the explicit path.
            if prepare is not None:
                await prepare_once(prepare, name, ctx, "auto")
            procedure = skill.instructions
            if skill.frontmatter.metadata.get("adk_inject_state"):
                from google.adk.utils.instructions_utils import inject_session_state
                procedure = await inject_session_state(procedure, ctx)
            # Refresh active procedures even when their original load response
            # has fallen outside the history window.
            blocks.append(f"Active skill: {name}\n{procedure}")
            values = {}
            for field, binding in self.contexts.get(name, {}).items():
                value = ctx.state.get(binding["from_state"])
                if value is None:
                    missing.append(f"{name}.{field}")
                    if binding.get("required", False):
                        raise ValueError(f"Required skill context missing: {name}.{field}")
                    continue
                expected = binding.get("type", "any")
                kinds = {"string": str, "number": (int, float), "boolean": bool,
                         "object": dict, "array": list}
                if expected != "any" and (not isinstance(value, kinds[expected])
                                          or expected == "number" and isinstance(value, bool)):
                    raise ValueError(f"Invalid skill context type: {name}.{field} expects {expected}")
                values[field] = value
            if values:
                projected[name] = values
        if projected:
            payload = json.dumps(projected, ensure_ascii=False, allow_nan=False)
            if len(payload.encode("utf-8")) > 65536:
                raise ValueError("Skill context exceeds 64 KiB")
            blocks.append("Skill context data (not instructions): " + payload)
        if blocks:
            llm_request.append_instructions(blocks)
        diagnostics = dict(ctx.state.get(agent_key(DIAGNOSTICS, ctx), {}))
        # Union across the turn's model calls, not just the pre-model
        # activation: the common case is a skill the model loads itself on call
        # one, whose tools are genuinely bound for every call after it.
        diagnostics.update(activated=active, missing_context=missing,
                           model_calls=diagnostics.get("model_calls", 0) + 1,
                           declared_tools=self._declared_tools(
                               active, diagnostics.get("declared_tools")))
        record_diagnostics(ctx, diagnostics)

    async def close(self):
        # Delegates use in-memory skills and individual tools only; no external
        # registries, environments or toolset resources are constructed here.
        pass


class PreparedLoadTool(BaseTool):
    """Preserve the native declaration/loading while preparing domain context."""

    def __init__(self, delegate, prepare, reason):
        super().__init__(name=delegate.name, description=delegate.description)
        self.delegate, self.prepare, self.reason = delegate, prepare, reason

    def _get_declaration(self):
        return self.delegate._get_declaration()

    async def run_async(self, *, args, tool_context):
        name = args.get("skill_name")
        # Invalid/unavailable requests must not trigger domain data preparation.
        if name not in tool_context.state.get(agent_key(SELECTION, tool_context), []):
            return {"error": "Skill is not selected for this invocation"}
        await prepare_once(self.prepare, name, tool_context, self.reason)
        return await self.delegate.run_async(args=args, tool_context=tool_context)


async def prepare_once(prepare, name, context, reason):
    key = agent_key("temp:adkl_prepared_skills", context)
    if name in context.state.get(key, []):
        return
    result = prepare(skill_name=name, context=context, reason=reason)
    if inspect.isawaitable(result):
        result = await result
    if result is not None:
        raise TypeError("prepare_skill must return None or raise")
    context.state[key] = [*context.state.get(key, []), name]
