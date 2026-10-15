#!/usr/bin/env python3
"""Staged A2UI widgets: declare the pipeline, let the library run it.

## The pattern this models

Most widget tools do not render. They **stage** a payload into a session-state
register and a turn-boundary callback emits it. Production runs four phases:

    stage  ->  rerank (optional)  ->  contract  ->  emit

1. **stage**   a tool writes candidates into `register` and flags it dirty
2. **rerank**  optionally, the model reorders or filters what was staged
3. **contract** BEFORE the next model call, the pending widget's communicative
               role is resolved into a reply-shaping instruction block
4. **emit**    at the turn boundary, a callback drains the register and calls
               `render_ui_widget`

Skipping any phase fails quietly, which is what makes this worth regulating. A
tool that stages with no emitter returns `{"status": "staged"}` and the turn
completes clean with no widget and no error -- observed here before this module
existed. A contract resolved from different gates than the emitter uses renders a
widget on a turn whose reply was never shaped for it (the `#1898` run-on-reply
shape in the source repo).

## What adk-lightning owns, and why that is the point

The reference implementation is ~7,400 lines across three modules, with 18 specs
for one agent spread over 1,900 more. The *logic* in it is real and mostly
irreducible -- payload builders, suppression rules, revive predicates. But the
**pipeline** around that logic is uniform, and every agent re-deriving it is how
gate order drifts and telemetry goes missing.

So: specs are declared in `agent.yaml`, this module runs them, and
`adkl.widget_lint` checks them. Concretely that buys

* one gate order, in one place, with the documented fail-open/fail-closed
  behaviour per gate rather than per author
* the resolver/emit agreement enforced **by construction** -- both read the same
  declared spec through `spec_is_live`, so they cannot disagree
* a named, counted decision for every declined gate, so "no widget appeared"
  always has an answer
* those decisions surfaced in the console's turn trace

Hooks stay hooks. A payload builder is code; pretending otherwise would produce a
bad DSL. What is declarative is the *shape*: which register, which role, which
gates, in which order.

## Gate order

Mirrors the reference implementation exactly, because the order encodes real
decisions and reordering it changes behaviour:

    1  live?        dirty this turn, or revive_when says the register is still
                    the live UI for an in-progress flow
    2  emitted?     an explicit render tool already emitted this type
    3  suppressed?  the spec's own rule declines
    4  non-empty?   the register actually holds items
    5  build        -> [] means expected_empty decides info vs warning
    6  render       emit each payload, then MUST write state (see below)

**Fail direction per gate is deliberate.** `revive_when` fails CLOSED: a wrong
`True` renders UI no other gate expected, so silence beats optimism.
`expected_empty` and `resolve_role` fail OPEN in contract resolution: a raising
predicate keeps the contract, because a false "nothing will render" is worse than
a redundant instruction block.

## One ADK constraint that is not obvious

ADK only emits the after-agent event when the callback returns content or mutates
state. `render_ui_widget` mutates event actions, *not* state -- so the emitted
flag must be written on every emit or ADK drops the event and the widget is lost.
Enforced here rather than left to each spec.
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class WidgetError(RuntimeError):
    """A spec that cannot be run as declared."""


# --------------------------------------------------------------- roles


# Compatibility imports: existing widget users keep the same public names.
from adkl.presentation_contracts import Contract, Role, navigation_handoff_guidance
from adkl.presentation_contracts import contract_for_roles as _contract_for_roles


def contract_for_roles(roles: list[Role]) -> Contract | None:
    """Legacy widget resolver ignores unknown roles; the new host API is strict."""
    return _contract_for_roles(role for role in Role if role in roles)


# --------------------------------------------------------------- predicates

# A declared predicate is either a small declarative form or a hook. The
# declarative forms cover what specs actually do most of the time -- "is this
# state key set" -- so the common case needs no Python and can be linted. Real
# logic uses `hook`, which is honest about being code.
#
#   state_truthy: some:key      -> bool(state.get("some:key"))
#   state_falsy:  some:key      -> not state.get(...)
#   hook: module:function       -> called with the documented signature
#   always / never              -> constants, useful to disable a gate in place


def _resolve_attr(ref: str, *, what: str = "reference") -> Any:
    """`module:ATTRIBUTE` -> the attribute. Used for hooks and spec registries."""
    if ":" not in ref:
        raise WidgetError(
            f"{what} must be 'module:attribute', got {ref!r}")
    mod_name, attr = ref.split(":", 1)
    try:
        return getattr(importlib.import_module(mod_name), attr)
    except Exception as exc:
        raise WidgetError(f"cannot load {what} {ref}: "
                          f"{type(exc).__name__}: {exc}") from exc


def _resolve_hook(ref: str) -> Callable:
    """A hook must be callable; a spec registry need not be (see _resolve_attr)."""
    fn = _resolve_attr(ref, what="hook")
    if not callable(fn):
        raise WidgetError(f"hook {ref} is not callable")
    return fn


def call_gate(fn: Callable, ctx: Any, items: list) -> bool:
    """Call an `expected_empty` predicate, tolerating either signature.

    The reference implementation's predicates take `(callback_context, items)`,
    while the declarative forms compiled here take `(state)`. Both are legitimate
    -- one is a ported hook, the other is `state_truthy: some:key` -- so the
    caller adapts instead of forcing every author to match one shape.

    Inspecting the signature rather than catching TypeError, because a TypeError
    raised *inside* a two-arg predicate would otherwise look like an arity
    mismatch and be silently retried with one arg. That is how a real bug becomes
    a wrong gate decision.
    """
    try:
        import inspect
        params = inspect.signature(fn).parameters
        wants_two = len([p for p in params.values()
                         if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                         and p.default is p.empty]) >= 2
    except (TypeError, ValueError):
        wants_two = False
    return bool(fn(ctx, items) if wants_two else fn(getattr(ctx, "state", ctx)))


def compile_predicate(decl: Any, *, name: str, hook_resolver=_resolve_hook) -> Callable[[Mapping], bool] | None:
    """Compile a declared predicate to `(state) -> bool`. None when absent.

    Kept separate from the spec so `adkl.widget_lint` can compile without
    running, and so a malformed predicate is a load-time error rather than a
    surprise at the turn boundary.
    """
    if decl is None:
        return None
    if decl is True or decl == "always":
        return lambda state: True
    if decl is False or decl == "never":
        return lambda state: False
    if not isinstance(decl, dict):
        raise WidgetError(
            f"{name}: expected a mapping, 'always' or 'never', got {decl!r}")

    if "state_truthy" in decl:
        key = decl["state_truthy"]
        return lambda state: bool(state.get(key))
    if "state_falsy" in decl:
        key = decl["state_falsy"]
        return lambda state: not state.get(key)
    if "hook" in decl:
        return hook_resolver(decl["hook"])
    raise WidgetError(
        f"{name}: needs one of state_truthy, state_falsy, hook, always, never")


# --------------------------------------------------------------- spec


@dataclass
class WidgetSpec:
    """One staged widget type, declared rather than coded.

    State keys are DERIVED from `key` rather than declared separately. The
    reference implementation names `state_items`, `state_dirty` and
    `state_emitted` per spec, which is three chances to typo a key that fails
    silently -- a register flagged under one name and read under another is
    simply never emitted. Deriving them makes that class of bug unreachable.
    """

    key: str                    # short name, used in logs and telemetry
    widget_id: str              # UiWidget id; "-<n>" appended when several emit
    payload_type: str           # payload["type"]; the client dispatches on it
    build: Callable             # (ctx, items, request_id) -> [(payload, caption)]

    register: str = ""          # persistent state key holding staged items
    stage_tools: list = field(default_factory=list)   # tools that stage here
    rerank_tool: str | None = None                    # optional curation step

    # Explicit key overrides, for adopting stage tools that already exist.
    #
    # Derivation is the default because three hand-written key names is three
    # chances to typo one that fails silently -- a register flagged under one name
    # and read under another is simply never emitted. But an existing tool writes
    # the keys it already writes, and the reference implementation's nav cards use
    # `temp:nav_cards_stage_dirty`, which no derivation rule would guess. Adopting
    # a real tool without editing it requires saying so.
    dirty_key: str = ""
    emitted_key: str = ""
    revive_count_key: str = ""

    role: Role | None = None                          # static contract role
    resolve_role: Callable | None = None              # (state) -> Role | None

    suppress: Callable | None = None                  # (state) -> bool
    expected_empty: Callable | None = None            # (ctx, items) -> bool
    revive_when: Callable | None = None               # (state) -> bool
    revive_cap: int = 0        # 0 = uncapped; -1 = delegated; >0 = local cap

    default_companion: str = ""
    companion_note: Callable | None = None            # (state) -> str
    adoption_warnings: list[str] = field(default_factory=list)
    preserve_register_shape: bool = False  # adopted host builder contract

    # ---- derived state keys. `temp:` marks turn-scoped in ADK.

    @property
    def state_items(self) -> str:
        return self.register or f"staged_{self.key}_items"

    @property
    def state_dirty(self) -> str:
        return self.dirty_key or f"temp:staged_{self.key}_dirty"

    @property
    def state_emitted(self) -> str:
        # Writing this is what makes ADK emit the event at all (see the module
        # docstring), and it also stops a widget an explicit render tool already
        # emitted from being emitted twice.
        #
        # `temp:` when derived, because within a turn is the only scope that
        # matters and a persistent flag would suppress the widget on every later
        # turn. The reference implementation agrees -- its nav cards use
        # `temp:nav_cards_emitted`.
        return self.emitted_key or f"temp:staged_{self.key}_emitted"

    @property
    def state_revive_count(self) -> str:
        return self.revive_count_key or f"staged_{self.key}_revive_count"


def spec_from_dict(key: str, d: dict, *, hook_resolver=_resolve_hook) -> WidgetSpec:
    """Build a spec from its `agent.yaml` block."""
    if not isinstance(d, dict):
        raise WidgetError(f"widget spec {key}: expected a mapping")

    for required in ("widget_id", "type", "build"):
        if not d.get(required):
            raise WidgetError(f"widget spec {key}: missing {required}")

    role = d.get("role")
    if role is not None:
        try:
            role = Role(role)
        except ValueError as exc:
            raise WidgetError(
                f"widget spec {key}: unknown role {role!r}. Valid: "
                + ", ".join(r.value for r in Role)) from exc

    stage = d.get("stage") or {}
    rerank = d.get("rerank") or {}
    emit = d.get("emit") or {}
    companion = d.get("companion") or {}

    return WidgetSpec(
        key=key,
        widget_id=d["widget_id"],
        payload_type=d["type"],
        build=hook_resolver(d["build"]),
        register=d.get("register", ""),
        dirty_key=(d.get("stage") or {}).get("dirty_key", ""),
        emitted_key=(d.get("emit") or {}).get("emitted_key", ""),
        stage_tools=list(stage.get("tools") or []),
        rerank_tool=rerank.get("tool"),
        role=role,
        resolve_role=(hook_resolver(d["resolve_role"])
                      if d.get("resolve_role") else None),
        suppress=compile_predicate(emit.get("suppress"),
                                   name=f"{key}.emit.suppress", hook_resolver=hook_resolver),
        expected_empty=compile_predicate(emit.get("expected_empty"),
                                         name=f"{key}.emit.expected_empty", hook_resolver=hook_resolver),
        revive_when=compile_predicate(emit.get("revive_when"),
                                      name=f"{key}.emit.revive_when", hook_resolver=hook_resolver),
        revive_cap=int(emit.get("revive_cap") or 0),
        default_companion=companion.get("default", ""),
        companion_note=(hook_resolver(companion["note"])
                        if companion.get("note") else None),
    )


def adopt(ref: str) -> list[WidgetSpec]:
    """Adapt an existing agent's spec registry into adk-lightning specs.

    `ref` is `module:ATTRIBUTE` naming a sequence of specs in the agent repo.

    ## Why this exists rather than only YAML declaration

    Hand-declaring specs is right for a new agent. For an existing one it is both
    impractical and, in places, impossible:

    * the prospect agent has **18** specs, and transcribing them is 18 chances to
      mistype a state key -- a class of bug that fails silently, since a register
      flagged under one name and read under another simply never emits (already
      hit once here: declared keys did not match, gate reported `not_live`)
    * several `suppress` predicates are **lambdas**. A `module:function` reference
      cannot address an anonymous function, so those specs cannot be expressed in
      the declarative form at all

    Adopting keeps everything that makes the native pipeline worth having -- one
    gate order, named declines, the resolver/emitter agreement, trace visibility
    -- while the specs stay where their authors maintain them. What is dropped is
    only the ability to lint field-by-field, which is the lesser loss: a spec that
    already runs in production has been validated by production.

    Field names are read defensively. The source dataclass uses `state_items` /
    `state_dirty` / `state_emitted` and `presentation_role`; a rename upstream
    should degrade to a reported problem rather than a wrong key.
    """
    registry = _resolve_attr(ref, what="widgets.adopt")
    try:
        iter(registry)
    except TypeError as exc:
        raise WidgetError(
            f"widgets.adopt {ref} is not a sequence of specs") from exc

    out: list[WidgetSpec] = []
    for src in registry:
        key = getattr(src, "key", None)
        if not isinstance(key, str) or not key:
            raise WidgetError(f"widgets.adopt {ref} contains a spec without a valid key")
        role = getattr(src, "presentation_role", None)
        role_value = getattr(role, "value", role)
        findings = []
        try:
            mapped = Role(role_value) if role_value is not None else None
        except (ValueError, TypeError):
            message = f"Widget {key!r}: unmapped presentation role {role_value!r}; no reply contract"
            findings.append(message)
            warnings.warn(message, UserWarning, stacklevel=2)
            mapped = None

        out.append(WidgetSpec(
            key=key,
            widget_id=getattr(src, "widget_id", key),
            payload_type=getattr(src, "payload_type", "") or "",
            build=getattr(src, "build", None),
            register=getattr(src, "state_items", "") or "",
            dirty_key=getattr(src, "state_dirty", "") or "",
            emitted_key=getattr(src, "state_emitted", "") or "",
            role=mapped,
            resolve_role=getattr(src, "resolve_role", None),
            suppress=getattr(src, "suppress", None),
            expected_empty=getattr(src, "expected_empty", None),
            revive_when=getattr(src, "revive_when", None),
            # Upstream stores the counter KEY rather than a cap, and enforces the
            # limit INSIDE its own revive_when. So adoption enables the
            # bookkeeping and delegates the ceiling.
            #
            # -1 rather than a huge sentinel: 1_000_000 was read by the linter as
            # "an uncapped sticky register" and reported a warning on a spec that
            # is correctly capped upstream. A sentinel that another layer
            # misreads as data is worse than an explicit one.
            revive_cap=(-1 if getattr(src, "revive_count_key", None) else 0),
            revive_count_key=getattr(src, "revive_count_key", "") or "",
            default_companion=getattr(src, "default_companion", "") or "",
            companion_note=getattr(src, "companion_note", None),
            adoption_warnings=findings,
            preserve_register_shape=True,
        ))
    return out


def specs_from_config(doc: dict) -> list[WidgetSpec]:
    """Read every spec for an agent: adopted, declared, or both.

    Declared specs win on a key collision, so a workspace can adopt a registry
    wholesale and override one spec locally without forking the registry.
    """
    block = (doc.get("widgets") or {})
    specs: list[WidgetSpec] = []

    if block.get("adopt"):
        specs.extend(adopt(block["adopt"]))

    declared = {k: spec_from_dict(k, v)
                for k, v in (block.get("specs") or {}).items()}
    if declared:
        specs = [s for s in specs if s.key not in declared]
        specs.extend(declared.values())
    from adkl import presentation
    if presentation.configuration(doc):
        shared = presentation.spec()
        if any(s.key == shared.key or s.state_items == shared.state_items
               or s.widget_id == shared.widget_id for s in specs):
            raise WidgetError("widget spec collides with the ADKL presentation queue")
        specs.append(shared)
    return specs


# --------------------------------------------------------------- shared gate


@dataclass
class GateResult:
    """Why a spec did or did not proceed. The unit of explanation."""

    key: str
    passed: bool
    gate: str = ""          # which gate declined
    detail: str = ""
    revived: bool = False


def spec_is_live(state: Mapping, spec: WidgetSpec) -> tuple[bool, bool]:
    """(consider, revived) -- gate 1, shared by the resolver and the emit loop.

    **This function existing once is the point.** When contract resolution and
    emission each decide liveness their own way they eventually disagree, and a
    revived widget renders on a turn whose contract declined. Both callers go
    through here, so drift is not possible rather than merely discouraged.

    Fails CLOSED. A raising `revive_when` declines: a wrong `True` renders UI no
    other gate expected, and silence beats optimism.
    """
    if state.get(spec.state_dirty):
        return True, False
    if spec.revive_when is None:
        return False, False
    try:
        if not spec.revive_when(state):
            return False, False
    except Exception:
        return False, False

    # Cap consecutive revived emits. Each one costs a real enrichment round
    # trip, so an uncapped sticky register re-enriches on every later turn.
    #
    # -1 means an adopted spec whose own revive_when enforces the ceiling; we
    # count but do not second-guess it.
    if spec.revive_cap > 0:
        try:
            if int(state.get(spec.state_revive_count) or 0) >= spec.revive_cap:
                return False, False
        except (TypeError, ValueError):
            return False, False
    return True, True


# --------------------------------------------------------------- contract


@dataclass
class Resolution:
    """Outcome of contract resolution, and the gate decisions behind it."""

    contract: Contract | None = None
    sources: list = field(default_factory=list)   # spec keys that contributed
    gates: list = field(default_factory=list)     # [GateResult]


def resolve_contract(state: Mapping, specs: list[WidgetSpec]) -> Resolution:
    """Resolve the reply-shaping contract BEFORE the model call.

    Runs gates 1-4 plus `expected_empty`, deliberately the same gates in the same
    order the emit loop will use. A spec whose widget will not render must not
    shape the reply, and a spec whose widget WILL render must.

    `expected_empty` and `resolve_role` fail OPEN here: a raising predicate keeps
    the contract. A false "nothing will render" costs a correctly-shaped reply,
    which is worse than a redundant instruction block.
    """
    res = Resolution()
    roles: list[Role] = []

    for spec in specs:
        if spec.role is None:
            continue   # does not participate in contract resolution

        consider, revived = spec_is_live(state, spec)
        if not consider:
            res.gates.append(GateResult(spec.key, False, "not_live"))
            continue
        if state.get(spec.state_emitted):
            res.gates.append(GateResult(spec.key, False, "already_emitted"))
            continue
        if spec.suppress is not None:
            try:
                if spec.suppress(state):
                    res.gates.append(GateResult(spec.key, False, "suppressed"))
                    continue
            except Exception:
                pass   # fail open, same as the emit loop's best-effort gates
        items = state.get(spec.state_items)
        if not items:
            res.gates.append(GateResult(spec.key, False, "no_items"))
            continue
        if spec.expected_empty is not None:
            try:
                if call_gate(spec.expected_empty, _StateOnly(state),
                             items if spec.preserve_register_shape else list(items)):
                    res.gates.append(
                        GateResult(spec.key, False, "expected_empty"))
                    continue
            except Exception:
                pass   # best-effort: keep the contract rather than drop it

        role = spec.role
        if spec.resolve_role is not None:
            try:
                role = spec.resolve_role(state) or spec.role
            except Exception:
                role = spec.role   # refinement is best-effort
        roles.append(role)
        res.sources.append(spec.key)
        res.gates.append(GateResult(spec.key, True, revived=revived))

    res.contract = contract_for_roles(roles)
    return res


class _StateOnly:
    """Adapts a bare state mapping to the `.state` attribute emit-side
    predicates expect.

    `expected_empty` is written against the emit signature `(ctx, items)` but
    must read only state, because contract resolution runs before the build and
    has nothing else to offer. Same predicate, both phases, one signature.
    """

    def __init__(self, state: Mapping):
        self.state = state


CONTRACT_INSTRUCTIONS: dict[Contract, str] = {
    Contract.NAVIGATION_HANDOFF: navigation_handoff_guidance(),
    Contract.SYNTHESIS: (
        "A data widget will render below your reply. Synthesize over it -- lead "
        "with the comparison or recommendation the rows support. Do not list the "
        "rows back; the customer can already see them."),
    Contract.SYNTHESIS_WITH_TABLE: (
        "Summarize the supported conclusion and include the required written comparison "
        "table using available figures. Preserve units, comparison periods and material "
        "qualifications; identify unknown values rather than inventing them."),
    Contract.GUIDANCE: (
        "An action button will render below your reply. It carries only a "
        "destination, so your reply must carry the procedure: what happens after "
        "the tap, and anything needed first."),
    Contract.STEP_HANDOFF: (
        "A card carrying the ordered steps will render below your reply. Frame it "
        "in one or two sentences and hand off -- restating the steps duplicates "
        "what the customer is already reading."),
    Contract.ELICITATION: (
        "An input widget will render below your reply. Say what is being asked "
        "for and why it is needed. Do not describe a destination or a procedure; "
        "nothing proceeds until the customer responds."),
    Contract.ANSWER: (
        "A supporting widget will render below your reply. Answer the question in "
        "text; the widget is an affordance, not the answer."),
}


def instruction_for(contract: Contract | None) -> str:
    """The instruction block for a resolved contract. Empty means text-primary.

    Wrapped in a tag because it is injected last, where the model weights it
    most, and an unmarked paragraph there reads as part of the previous block.
    """
    if contract is None:
        return ""
    body = CONTRACT_INSTRUCTIONS[contract]
    return f"\n\n<presentation_contract id=\"{contract.value}\">\n{body}\n</presentation_contract>"


# --------------------------------------------------------------- emit


@dataclass
class EmitResult:
    """What the turn boundary did, and why. Surfaced in the console trace."""

    emitted: list = field(default_factory=list)    # [{key, widget_id, type}]
    gates: list = field(default_factory=list)      # [GateResult]
    companion: str = ""

    def declined(self) -> list:
        return [g for g in self.gates if not g.passed]


async def emit_staged(callback_context, specs: list[WidgetSpec],
                      request_id: str = "adkl", *, sink=None,
                      isolate_render_failures: bool = False) -> EmitResult:
    """Drain every live register at the turn boundary. Gates 1-6, in order.

    Returns an EmitResult rather than None: every declined gate is named, because
    "no widget appeared" with no explanation is the failure mode this whole module
    exists to remove.
    """
    state = callback_context.state
    result = EmitResult()
    first_caption, floor_companion = "", ""

    for spec in specs:
        # Isolated delivery failures are terminal for this invocation, including
        # partial multi-payload delivery. Never retry accepted siblings implicitly.
        failure_key = f"temp:adkl_widget_render_failure:{spec.key}"
        invocation = str(getattr(callback_context, "invocation_id", ""))
        if isolate_render_failures and state.get(failure_key) == {"invocation": invocation}:
            result.gates.append(GateResult(spec.key, False, "render_failed",
                                           "already attempted this invocation"))
            continue
        # 1 -- live?
        consider, revived = spec_is_live(state, spec)
        if not consider:
            result.gates.append(GateResult(spec.key, False, "not_live"))
            continue

        # 2 -- already emitted by an explicit render tool? Not a defect: the
        # widget DID render, and the staged path defers to that work.
        if state.get(spec.state_emitted):
            result.gates.append(
                GateResult(spec.key, False, "already_emitted",
                           "an explicit render tool emitted this type"))
            continue

        # 3 -- the spec's own suppression rule
        if spec.suppress is not None:
            try:
                if spec.suppress(state):
                    result.gates.append(
                        GateResult(spec.key, False, "suppressed"))
                    continue
            except Exception as exc:
                result.gates.append(
                    GateResult(spec.key, False, "suppressed",
                               f"predicate raised: {type(exc).__name__}"))
                continue

        # 4 -- dirty but holding nothing means a stage call produced no item
        items = state.get(spec.state_items)
        if not items:
            result.gates.append(
                GateResult(spec.key, False, "no_items",
                           "register was flagged this turn and is empty"))
            continue

        # 5 -- build
        try:
            built = await spec.build(callback_context,
                                     items if spec.preserve_register_shape else list(items), request_id)
        except Exception as exc:
            # A broken builder costs its own widget, never the turn.
            result.gates.append(
                GateResult(spec.key, False, "build_failed",
                           f"{type(exc).__name__}: {exc}"))
            continue

        if not built:
            expected, predicate_error = False, ""
            if spec.expected_empty is not None:
                try:
                    expected = call_gate(
                        spec.expected_empty, _StateOnly(state),
                        items if spec.preserve_register_shape else list(items))
                except Exception as exc:
                    # Reported, not swallowed. Folding this into "built_nothing"
                    # made a misconfigured predicate look like a normal empty
                    # build -- the exact silent degradation this module exists to
                    # remove. Fails toward alerting, since an unknown answer to
                    # "was this empty on purpose" is worth a look.
                    predicate_error = f"expected_empty raised {type(exc).__name__}"
            result.gates.append(GateResult(
                spec.key, False,
                "expected_empty" if expected else "built_nothing",
                "declared as an expected outcome" if expected
                else (predicate_error or "the build produced no payload -- "
                      "this one is worth alerting on")))
            continue

        # 6 -- render. Opt-in isolation retains accepted payload receipts and
        # captions while declining the failed spec, never the unrelated specs.
        try:
            for index, entry in enumerate(built):
                payload, caption = entry if isinstance(entry, tuple) else (entry, "")
                widget_id = (spec.widget_id if len(built) == 1
                             else f"{spec.widget_id}-{index}")
                if sink is None:
                    _render(callback_context, widget_id, payload)
                else:
                    import inspect
                    accepted = sink(callback_context, widget_id, payload)
                    if inspect.isawaitable(accepted):
                        accepted = await accepted
                    if accepted is not None:
                        raise WidgetError("widget sink must return None or raise")
                result.emitted.append({
                    "key": spec.key,
                    "widgetId": widget_id,
                    "type": (payload or {}).get("type") or spec.payload_type,
                })
                if caption and not first_caption:
                    first_caption = caption
                if not floor_companion:
                    floor_companion = spec.default_companion
        except Exception as exc:
            if not isolate_render_failures:
                raise
            # No raw exception messages: sinks can carry private host payloads.
            state[failure_key] = {"invocation": invocation}
            result.gates.append(GateResult(spec.key, False, "render_failed",
                                           type(exc).__name__))
            continue

        # MANDATORY, and the reason is an ADK contract rather than bookkeeping:
        # ADK emits the after-agent event only when the callback returns content
        # or mutates state. render_ui_widget mutates event actions, NOT state --
        # so without this write ADK drops the event and the widget is lost.
        state[spec.state_emitted] = True
        if not floor_companion:
            floor_companion = spec.default_companion

        # A note is evaluated only after this spec actually emitted, so
        # "no widget, no sentence" is structural rather than conventional -- a
        # server-authored line about the card is a lie on a turn without it.
        if spec.companion_note is not None:
            try:
                note = (spec.companion_note(state) or "").strip()
                if note:
                    state["staged_companion_note"] = note
            except Exception:
                pass   # a caption must never break a turn

        # Revive bookkeeping. The loop owns the arithmetic because only it knows
        # whether a given emit was revived.
        if spec.revive_cap:
            try:
                state[spec.state_revive_count] = (
                    int(state.get(spec.state_revive_count) or 0) + 1
                    if revived else 0)
            except (TypeError, ValueError):
                state[spec.state_revive_count] = 0

        result.gates.append(GateResult(spec.key, True, revived=revived))

    # Companion floor, applied once after every spec had a chance at a grounded
    # caption: prefer a real one, else the first emitting spec's default, so a
    # staged widget can never reach the user with no visible text at all.
    result.companion = (first_caption or floor_companion or "").strip()

    # The flush RAN, so no flush is owed -- true even when every spec declined,
    # since a decline is a decision and not a lost widget. This is the half
    # that gives PENDING_KEY its meaning: without it the marker never comes
    # down and the turn-start reader above would report the same register
    # forever.
    #
    # Guarded, not unconditional: writing state on a turn that staged nothing
    # would add a persistent state delta -- and hence an after-agent event ADK
    # would otherwise drop -- to every turn of every host running this feature.
    #
    # Deliberately after the loop, so a raising sink (the emit contract lets
    # one propagate) leaves the marker set and the drop is reported next turn.
    if state.get(PENDING_KEY):
        state[PENDING_KEY] = False
    return result


def _render(callback_context, widget_id: str, payload: dict) -> None:
    """Call render_ui_widget, building the UiWidget the way ADK expects.

    Imported lazily and tolerant of a plain-dict context so the emit loop is
    testable without ADK -- the gate order is the part worth testing, and
    requiring a live runtime to reach it would mean testing none of it.
    """
    try:
        from google.adk.events.ui_widget import UiWidget  # type: ignore
        widget = UiWidget(id=widget_id, provider="adkl", payload=payload)
    except Exception:
        widget = type("UiWidget", (), {
            "id": widget_id, "provider": "adkl", "payload": payload})()
    callback_context.render_ui_widget(widget)


# --------------------------------------------------------------- staging

# The persistent "a turn-boundary flush is owed" marker, and the ONLY piece of
# the staged contract that outlives a turn: `state_dirty` and `state_emitted`
# are `temp:` by derivation (see WidgetSpec) and ADK never persists a `temp:`
# key (google.adk.sessions.State.TEMP_PREFIX).
#
# Its lifecycle is therefore load-bearing, and it is the reference
# implementation's, not an invention here: `stage` sets it, `emit_staged`
# clears it, and a turn-start reader reports whatever is still set. Set-at-stage
# with the clear in the turn-start reset -- which is what this module did
# before -- means the marker only ever says "something was staged once", and
# the one turn where it matters is the turn its evidence is thrown away.
#
# Un-namespaced deliberately. A host that already runs a staged pipeline owns a
# marker of exactly this name (`widgets.adopt` exists for such a host), so ADKL
# sharing the key is only safe while ADKL follows the same lifecycle. It now
# does: the writes are set-on-stage and clear-on-flush, never a blind clear
# ahead of the host's own hooks.
PENDING_KEY = "staged_emit_pending"


def stage(state, spec: WidgetSpec, items: list) -> None:
    """Write items into a register and flag it dirty. What a stage tool calls.

    Two writes, always together. Flagging without items yields a `no_items`
    decline; items without the flag is silently never emitted. Doing both here
    means a stage tool cannot do one and forget the other.
    """
    state[spec.state_items] = list(items)
    state[spec.state_dirty] = True
    # Lets a turn-start watchdog notice a register that was staged and never
    # drained, which is the signature of a missing emit callback. Cleared by
    # `emit_staged`, so what survives a turn boundary is exactly a flush that
    # did not happen.
    state[PENDING_KEY] = True


def clear_turn_flags(state, specs: list[WidgetSpec]) -> list:
    """Turn-start reset. Returns registers staged last turn but never emitted.

    Those are worth reporting: a register flagged and left undrained means the
    emit callback did not run, which is exactly the silent failure that cost a
    debugging round here before this module existed. ADK drops `after_agent` on
    `end_invocation`, on a raising run implementation and on a client
    disconnect, so this is a live path rather than a hypothetical one.

    The cross-turn answer CANNOT come from `state_dirty`/`state_emitted`. Both
    are `temp:` by derivation (see WidgetSpec) and ADK never persists a `temp:`
    key, so at a turn start both read falsey and a dirty-and-unemitted test
    returns [] for every drop it is meant to report -- which is what this
    function did, while its caller and at least one host read the empty list
    as "nothing was dropped".

    What survives a turn boundary is the pair designed to: the persistent
    register CONTENTS and `PENDING_KEY`, which `stage` sets and `emit_staged`
    clears. The dirty test is KEPT as the first disjunct, for a second `begin`
    inside one invocation, where the temp flags are still live.

    This no longer clears `PENDING_KEY`. Clearing it here is what made the
    report impossible, and on a host that owns a marker of that name -- the
    reference implementation's is spelled identically -- this hook runs at
    composed before-agent index 0, ahead of every host hook, so the clear also
    silently disarmed the host's own watchdog. `emit_staged` owns the clear
    now, which is where the fact it records ("the flush ran") is actually
    known.
    """
    owed = bool(state.get(PENDING_KEY))
    dropped = [s.key for s in specs
               if not state.get(s.state_emitted)
               and (state.get(s.state_dirty)
                    or (owed and state.get(s.state_items)))]
    for spec in specs:
        state[spec.state_dirty] = False
        state[spec.state_emitted] = False
    return dropped
