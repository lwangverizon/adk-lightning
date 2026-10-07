"""Capability active context, separate from retrieval and native execution.

Adapters own type-specific behavior. Skills reuse native activation state;
callable capabilities retain only bounded continuation metadata, never a graph,
agent instance, arguments, results or business transaction state.
"""
from dataclasses import dataclass
from hashlib import sha256
import math
import time
from typing import Any, Callable


@dataclass(frozen=True)
class CapabilityLifecycleConfig:
    max_active_skills: int = 3
    max_waiting_capabilities: int = 3
    continuation_ttl_seconds: float = 900

    def __post_init__(self):
        for value in (self.max_active_skills, self.max_waiting_capabilities):
            if type(value) is not int or value < 1:
                raise ValueError("Lifecycle limits must be positive integers")
        ttl = self.continuation_ttl_seconds
        if (isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                or not math.isfinite(ttl) or ttl <= 0):
            raise ValueError("continuation_ttl_seconds must be positive and finite")


@dataclass(frozen=True)
class SkillLifecycleAdapter:
    """Explicit native ownership contract, not a second activation store.

    The host guarantees that release updates the same store as read_active, that
    activity is in oldest-load-first order, and that no competing cap/rescue writer
    is enabled. Construction belongs next to that native owner's configuration.
    """
    owner: Any
    read_active: Callable
    release: Callable

    def __post_init__(self):
        if self.owner is None or not callable(self.read_active) or not callable(self.release):
            raise TypeError("Skill adapter requires owner, read_active and release")

    def active(self, context):
        names = self.read_active(context)
        if not isinstance(names, (list, tuple)) or any(not isinstance(n, str) for n in names):
            raise TypeError("Skill activity must be an ordered sequence of names")
        return tuple(dict.fromkeys(names))

    def loaded(self, context, name, limit):
        active = self.active(context)
        if name in active:
            for old in [n for n in active if n != name][:max(0, len(active) - limit)]:
                self.release(context, old)


class _CallableLifecycleAdapter:
    """Identical continuation semantics for workflows and single-turn agents.

    Standalone callable tools may also explicitly return continuation. This is
    retention after a native call returns, not suspended execution or resume.
    """
    def __init__(self, config, clock, scope):
        self.config, self.clock, self.scope = config, clock, scope
        self.names = set()

    @staticmethod
    def key(context):
        return f"adkl:capability_lifecycle:{context.agent_name}"

    def scope_id(self, context, name):
        scope = self.scope(context, name) if self.scope else "session"
        if scope is None:
            return None
        if not isinstance(scope, str) or not scope:
            raise ValueError("Capability scope must be a nonempty string or None")
        return sha256(scope.encode()).hexdigest()

    def records(self, context):
        raw = context.state.get(self.key(context), {})
        if not isinstance(raw, dict):
            return {}
        now, result = self.clock(), {}
        for name, record in raw.items():
            if name not in self.names or not isinstance(record, dict):
                continue
            if record.get("status") == "running":
                if record.get("invocation") == context.invocation_id:
                    result[name] = dict(record)
            elif record.get("status") == "waiting":
                stamp, scope = record.get("updated_at"), self.scope_id(context, name)
                if (scope is not None and record.get("scope") == scope
                        and type(stamp) in (int, float)
                        and 0 <= now - stamp < self.config.continuation_ttl_seconds):
                    result[name] = dict(record)
        return result

    def reconcile(self, context, eligible):
        records = {n: r for n, r in self.records(context).items()
                   if n in eligible or r["status"] == "running"}
        if context.state.get(self.key(context), {}) != records:
            context.state[self.key(context)] = records

    def start(self, context, name):
        records = self.records(context)
        if records.get(name, {}).get("status") == "running":
            raise ValueError("Capability already running")
        token = (context.invocation_id, str(getattr(context, "function_call_id", "")))
        records[name] = {"status": "running", "invocation": token[0],
                         "call": token[1], "scope": self.scope_id(context, name)}
        context.state[self.key(context)] = records
        return token

    def finish(self, context, name, token, continuation):
        records = self.records(context)
        previous = records.get(name)
        if not previous or (previous.get("invocation"), previous.get("call")) != token:
            return
        records.pop(name)
        scope = self.scope_id(context, name) if continuation == "waiting" else None
        if scope is not None and previous.get("scope") == scope:
            records[name] = {"status": "waiting", "updated_at": self.clock(), "scope": scope}
        waiting = [n for n, r in records.items() if r["status"] == "waiting"]
        for old in waiting[:-self.config.max_waiting_capabilities]:
            records.pop(old)
        context.state[self.key(context)] = records

    def release(self, context, name):
        records = self.records(context)
        if records.get(name, {}).get("status") == "running":
            raise ValueError("Cannot release a running capability")
        records.pop(name, None)
        context.state[self.key(context)] = records


class CapabilityLifecycle:
    """Binding-owned coordinator; retrieval only consumes its read-only snapshot.

    prepare_request reconciles expiry/scope at a writable boundary. Dispatch calls
    started/finished for every kind. Releasing active context is never cancelling
    native work. Concurrent session writes retain native ADK limitations; this is
    not distributed locking, transaction idempotency or durable resume.
    """
    def __init__(self, config=None, *, skill_adapter=None, scope=None,
                 clock: Callable[[], float] = time.time):
        self.config = config if config is not None else CapabilityLifecycleConfig()
        if not isinstance(self.config, CapabilityLifecycleConfig):
            raise TypeError("config must be CapabilityLifecycleConfig")
        if skill_adapter is not None and not isinstance(skill_adapter, SkillLifecycleAdapter):
            raise TypeError("skill_adapter must be SkillLifecycleAdapter")
        if scope is not None and not callable(scope):
            raise TypeError("scope must be callable")
        self._skills = skill_adapter
        self._calls = _CallableLifecycleAdapter(self.config, clock, scope)
        self._catalog = None

    def bind(self, catalog, *, skill_owner=None):
        if self._catalog is not None:
            raise ValueError("Lifecycle already bound; create one per binding")
        catalog = {d.name: d.kind for d in catalog}
        if "skill" in catalog.values():
            if self._skills is None or self._skills.owner is not skill_owner:
                raise ValueError("Lifecycle requires an explicit adapter for this skill owner")
        elif self._skills is not None:
            raise ValueError("Skill adapter requires registered skills")
        self._catalog = catalog
        self._calls.names = {n for n, k in catalog.items() if k in {"workflow", "agent", "tool"}}

    def active_skills(self, context):
        if self._catalog is None:
            raise RuntimeError("Bind lifecycle before discovery")
        return self._skills.active(context) if self._skills else ()

    def retained(self, context, catalog):
        eligible = {d.name for d in catalog}
        return tuple(n for n in dict.fromkeys((*self.active_skills(context),
                                               *self._calls.records(context))) if n in eligible)

    def reconcile(self, context, catalog):
        """Permanently drop invalid continuation at a writable model boundary."""
        self._calls.reconcile(context, {d.name for d in catalog})

    def running(self, context, name):
        return self._calls.records(context).get(name, {}).get("status") == "running"

    def started(self, context, request):
        if request.capability in self._calls.names:
            return self._calls.start(context, request.capability)
        return None

    def finished(self, context, request, token, *, result=None, continuation="none"):
        if request.capability in self._calls.names:
            self._calls.finish(context, request.capability, token, continuation)
        elif (self._skills and request.kind == "skill" and request.operation == "load_skill"
              and isinstance(result, dict) and isinstance(result.get("instructions"), str)):
            self._skills.loaded(context, result.get("skill_name", request.capability),
                                self.config.max_active_skills)

    def release(self, context, name):
        """Trusted host cleanup, not a model tool or business cancellation API."""
        if self._catalog is None or name not in self._catalog:
            raise ValueError("Unknown capability")
        if self._catalog[name] == "skill":
            self._skills.release(context, name)
        else:
            self._calls.release(context, name)
