# Unified Capability Selection

<a id="description-first-native-capability-bindings"></a>

**One selection surface for skills, workflows and specialist agents.** ADKL
brings their descriptions into a common catalog, optionally shortlists them
together, and lets the parent model choose an action. You do not need a separate
skill selector, workflow router and agent router for this integration path.
Native ADK still owns activation and execution; the host owns business policy.

`adkl.capability_bindings.CapabilityBindings` accepts existing native ADK Skill,
Workflow and BaseAgent objects. Developers keep SKILL.md frontmatter/body,
workflow graphs and specialist prompts unchanged.

## Names and responsibilities

Use **Unified Capability Selection** as the feature name in product descriptions,
guides and examples. “Capability selector” is informal shorthand, not a separate
API or router agent. Avoid “capability orchestration” as the feature name: ADKL
is not replacing native execution with a new orchestration engine.

| Term / public API | Responsibility |
| --- | --- |
| Unified Capability Selection | The end-to-end feature: one discovery surface, parent-model choice and native execution with explicit policy boundaries |
| `CapabilityBindings` | Composes native capabilities and the shared runtime lifecycle |
| `CapabilityRetrieval` | Optional shared shortlisting using a host-supplied ranker and retention policy |
| `CapabilitySelection` | Ranker result containing candidate names and an explicit fallback marker; not the model's execution decision |
| Parent-model selection | Chooses whether to load, invoke, delegate, answer directly or clarify |
| `CapabilityPolicy` | Host eligibility and dispatch authorization hooks; business tools still enforce authorization |

The API names and module paths are unchanged. Retrieval, discovery, dispatch and
execution remain distinct technical phases, not competing feature names.

## Choose a selection path

| Need | Integration | Defaults and ownership |
| --- | --- | --- |
| Skills only, with built-in embedding retrieval and optional automatic activation | `RuntimeBindings` or the file-configured factory with `skill_retrieval` | Small-catalog bypass defaults to fewer than five skills; optional pins/history/context policy |
| One description-based surface across skills, workflows and specialists | `CapabilityBindings` | Exposes the host-eligible catalog; the parent chooses, with explicit skill loading by default |
| One ranked shortlist across that mixed catalog | `CapabilityBindings(retrieval=CapabilityRetrieval(rank, retain=...))` | Host supplies ranking and continuation; no built-in embedder, small-catalog bypass or automatic activation |

These are alternative selection owners, not layers to stack. `CapabilityBindings`
reuses the shared runtime lifecycle; do not also install `RuntimeBindings` or its
`skill_retrieval` / `skill_state_injection` features. A host-native skill toolset
needs the [explicit discovery handshake](#unified-capability-retrieval).
For skill-only defaults, see [runtime bindings](runtime-bindings.md).

## Register native capabilities

```python
from adkl.capability_bindings import CapabilityBindings
from google.adk.agents import LlmAgent

# Host supplies these existing native objects and the model.
binding = CapabilityBindings(
    skills=skills,
    workflows=[device_workflow],
    sub_agents=[support_agent],
)
agent = LlmAgent(name="prospect", model=model,
                 instruction=host_instruction, **binding.agent_kwargs())
```

Run the credential-free examples with the locked runtime installed:

```bash
uv sync --locked --extra runtime
# Catalog composition without retrieval:
uv run --no-sync python -m examples.native_capabilities
# Mixed shortlisting, native workflow execution and explicit empty fallback:
uv run --no-sync python -m examples.capability_selection
```

The [selection example](../examples/capability_selection.py) checks that the ranker
receives all three kinds, unrelated operations are absent from the model request,
the selected workflow executes, and an empty fallback does not expose the whole
catalog. Its ranker and model are deterministic fixtures, not production ranking
or evidence of semantic routing quality.

<a id="why-a-capability-layer"></a>

## Why Unified Capability Selection?

Native ADK skills, callable workflows and subagents can already coexist on a
parent agent. This binding does not make them executable for the first time.
It adds a shared description/discovery contract and optional dispatch policy,
while leaving their different execution semantics intact.

Keep these decisions separate:

| Phase | Owner | Meaning |
| --- | --- | --- |
| Eligibility | Host policy | Which registered capabilities may be exposed now? |
| Retrieval | Optional host ranker | Which eligible descriptions are relevant? |
| Selection | Parent model | Load, invoke, delegate, answer directly or clarify? |
| Authorization | Dispatch policy and business tools | Is this operation allowed now? |
| Execution | Native ADK target | Run the skill tools, graph or child agent. |
| Completion | Explicit target/host contract | Who answers and owns continuation? |

A shortlist is neither activation nor authorization. Prefer one public business
capability over three equivalent skill/workflow/agent implementations. When
capabilities overlap, describe their distinct use cases and outcomes. Neither a
shared catalog nor successful dispatch tests establish semantic routing quality.

## Runtime contract

1. Construction validates native object types, unique names, descriptions,
   workflow input schemas and unparented specialist instances. `inspect()` reads
   normalized descriptions only; it never executes targets or reads instructions
   through a callable. Native ADK construction subsequently parents subagents.
2. One native SkillToolset handles skill loading, resources and activated state.
   There is no duplicate ADKL skill selector or rewritten SKILL.md format.
3. A before-model request preparation callback adds peer descriptions and the
   shared invocation protocol. No unselected skill bodies, workflow internals or
   specialist instructions are added by this binding. Native transfer guidance
   and native skill guidance remain; no private prompt processor is patched.
4. The parent's ordinary model generation chooses load_skill, a named native
   workflow operation, native specialist delegation, direct answer or clarification.
5. Native execution owns callback guards, workflow branching, specialist context
   and final-answer behavior. No custom Runner or graph-over-iterator adapter.

Skills use frontmatter.name/description; workflows use name/description and
input_schema; specialists use name/description and native mode. Callable workflow
registration uses `LlmAgent(tools=[workflow])`; no private NodeTool import.
Optional `skill_tools` and `max_activated_skills` are forwarded to the installed
native SkillToolset. Some ADK distributions do not implement these options;
unsupported requested options fail construction rather than being ignored.

`agent_kwargs(callbacks=...)` accepts native callback fields and composes them
through the same lifecycle as RuntimeBindings, without its selector. Descriptor
preparation follows shared request processing and precedes explicit final model
transforms and observers. Native before-hook short circuits remain authoritative.
It issues once per binding. Create fresh objects/bindings for another agent tree.
Do not install a second skill toolset or emitter, rewrite tools after construction,
or combine two independent selection owners. Arbitrary host migration is not
automatic; host callbacks and plugins still require acceptance.

## Compose host configuration and shared lifecycle

Use `merge_into()` to retain the host model, instruction, planner and generation
configuration. The input dictionary and its lists are not mutated. Register all
tools and specialists on the binding: nonempty `tools` or `sub_agents` in host
kwargs are rejected instead of silently promoted, combined or overwritten.
Existing ADKL lifecycle callbacks are rejected; do not compose two bindings.
Other host callbacks remain the host's responsibility, including removing any
old standalone emitter. Arbitrary callback bodies cannot be inspected for effects.

```python
# Integration sketch: host supplies configuration, callbacks, specs and sinks.
binding = CapabilityBindings(
    skills=skills, skill_tools=skill_tools, workflows=[device_workflow],
    feature_config={"pipeline": ["staged_widgets"]},
    widget_specs=widget_specs, widget_sink=deliver_widget,
    companion_sink=deliver_companion, request_id=request_id,
    on_warning=log_warnings, on_finding=record_findings,
)
agent = LlmAgent(**binding.merge_into(
    host_kwargs,  # no existing tools/sub_agents/ADKL lifecycle
    after_tool_observers=[observe_raw_result],
    final_model_transforms=[normalize_provider_request],
    before_model_observers=[observe_final_request],
))
```

Both issuance methods accept `after_tool_observers`, `before_model_observers`,
`final_model_transforms` and `after_model_transforms`, with the same contracts as
[RuntimeBindings callback composition](runtime-bindings.md#callback-ordering-and-inspection).
Host after-tool callbacks are replacement transforms; raw observers run first.
Shared emission precedes host completion hooks. Sink errors retain the existing
report/cleanup/re-raise policy. An optional `pre_emit` validator is described in
[capability policy](capability-policy.md). Guaranteed cleanup on arbitrary
cancellation paths is not provided.

`feature_config` defaults to an empty pipeline, not implicit retrieval or
presentation. Existing non-selection shared features are available with their
normal validation. `skill_retrieval` and `skill_state_injection` are rejected:
the native skill toolset owns loading and the parent owns selection. Date seeding
remains host-owned. Widget specs require explicit `staged_widgets` enablement.
Packs and automatic promotion of internal tools are not supported here.

`inspect()` now includes the standard runtime snapshot plus capability declarations.
`adkl.inspection.bindings(agent)` and normal host inspection discover the issued
binding. Selection is reported as `parent_model`, execution as `native_adk`;
these are declarations, not a trace proving which target ran. Standalone tools
are distinguished from skill-scoped tools. No dynamic policy is executed during
inspection and returned snapshots may be edited without changing the binding.

## Unified capability retrieval

Retrieval is the optional **shortlisting phase** of Unified Capability Selection,
not a second model-selection owner. For example:

```python
from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval, CapabilitySelection

# Integration sketch: host supplies native objects, ranking and continuation.
async def rank(context, catalog):
    names = await host_ranker(context, catalog)
    return CapabilitySelection(tuple(names))

def retain(context, catalog):
    eligible = {descriptor.name for descriptor in catalog}
    return tuple(name for name in host_continuation(context) if name in eligible)

binding = CapabilityBindings(
    skills=skills,
    workflows=workflows,
    sub_agents=single_turn_specialists,
    retrieval=CapabilityRetrieval(rank, retain=retain),
)
# Install binding.merge_into(host_kwargs) on the parent LlmAgent, once.
```

`host_ranker` and `host_continuation` above are application inputs, not ADKL APIs.
The [runnable selection example](../examples/capability_selection.py) uses no
embedding service, model credentials or external application.

`CapabilityBindings(retrieval=CapabilityRetrieval(rank, retain=...))` optionally
shortlists **all kinds together** before catalog emission and native dispatch.
Import `CapabilityRetrieval` and `CapabilitySelection` from
`adkl.capability_retrieval`. `rank(context, catalog)` receives immutable
`CapabilityDescriptor(name, kind, description, operation)` records already
filtered by host eligibility and returns `CapabilitySelection(names, fallback)`.
The host supplies its embedder/index, history weighting and ranking thresholds;
ADKL does not call another router model. Skill loading stays explicit.

The dependency-light `adkl.capability_contracts` module publishes
`CapabilityDescriptor`, `CapabilitySelection`, `CapabilityCatalog`,
`CapabilityRanker`, `CapabilityRetention`, `CapabilityDiscovery`,
`CapabilityToolGuard` and `CapabilityDiscoveryConsumer`. The consumer protocol
makes the host-native handshake explicit without requiring a new base class or
replacing native ADK. It is a typing contract, not runtime proof of compliance.
Existing imports of `CapabilityDescriptor` from `capability_bindings` and
`CapabilitySelection` from `capability_retrieval` remain compatible aliases.

Descriptor records can be passed directly to description-only rankers. Do not
convert workflows or agents into native Skill objects to make them searchable.
Keep embedding services, retrieval-text normalization and business history
policy in the host.

Ranking is cached once per session/agent/invocation and eligible catalog in a
bounded cache (default 128). Concurrent callers for that key serialize; failures
and cancellations are not cached. Optional `retain(context, catalog)` returns
eligible continuation names and is re-evaluated on each access, including after
skill activation. Unknown names and invalid results raise, not expose everything.
An intentional fallback must explicitly name the desired eligible candidates;
`fallback` must be a boolean and `fallback=True` alone does not widen exposure.
Business authorization is rechecked at dispatch, independently of ranking. Policy changes invalidate the ranking key.

With a host-native toolset, shared retrieval requires
`bind_capability_discovery(discover, guard)`. The toolset consumes `await
 discover(context)` instead of independently ranking skills; `guard(tool, owners)`
wraps its native dispatch. It must not emit a duplicate catalog, auto-load skills or
expose inactive skill business tools. Native EAGER skill disclosure requires the
explicit catalog-ownership declaration described below. The host still owns loader preparation,
cohort remapping, resource access and protected state pins. Inspection reports
`retrieval="shared_capability_shortlist"` and
`skillPolicyOwnership="shared_dispatch_host_loader"`. This is an explicit host
adapter contract, not a sandbox for arbitrary custom toolsets.

Only native **single-turn** specialists support dynamic retrieval. Chat/task
transfer and resumption need a separate authorization/continuation contract and
are rejected. This feature is Python-only; it does not add YAML or console
retrieval configuration. Without `retrieval`, existing catalog behavior remains.

## Background execution

For independent work that returns a task receipt while the user keeps chatting,
see [Background tasks](background-tasks.md). The opt-in `background_capabilities`
and `tasks` arguments reuse this shortlist and launch authorization path. Generic
owner-authorized task management remains available outside semantic retrieval.
The reference worker still executes native ADK; active-context lifecycle is not a
job scheduler and no automatic durable resume is promised.

## Capability active context

`CapabilityBindings(lifecycle=CapabilityLifecycle(...))` composes active context
with optional retrieval. Retrieval only ranks and retains host business pins;
bindings merge the lifecycle snapshot inside current eligibility. The SDK default
is off. This is independent of experimental native `SkillLifecycleConfig`.

Integration sketch (native owner and ranker are host inputs):

```python
from adkl.capability_lifecycle import (
    CapabilityLifecycle, CapabilityLifecycleConfig, SkillLifecycleAdapter,
)
from adkl.skill_lifecycle import active_skill_names

lifecycle = CapabilityLifecycle(
    CapabilityLifecycleConfig(
        max_active_skills=3, max_waiting_capabilities=3,
        continuation_ttl_seconds=300,
    ),
    skill_adapter=SkillLifecycleAdapter(
        owner=native,
        read_active=lambda ctx: active_skill_names(native, ctx),
        release=host_release_skill,
    ),
    scope=host_scope,
)
binding = CapabilityBindings(
    skills=skills, skill_tools=tools, native_toolset=native,
    retrieval=CapabilityRetrieval(host_ranker), lifecycle=lifecycle,
)
```

No skill adapter is needed for workflow/subagent/tool-only bindings. Skill
bindings require an explicit adapter for the exact installed native owner; no
method-name probing or private host flag inspection selects ownership. The host
must configure that owner with no competing activation cap/rescue writer. The
adapter reads ordered native activity and releases the same store; it must never
create a second activation list. Successful native loads enforce cap 3 by default,
preserving the just-loaded skill. Invalid loads do not evict. Discovery never
activates, unloads or renews lifetime. Do not combine this coordinator with native
lifecycle, legacy cap arguments or `retain_active_skills`.

Workflows, native single-turn subagents and standalone tools share one callable
adapter. Each authorized dispatch records running state for its invocation and
call. An operation outcome and its continuation are distinct:

```python
CapabilityOutcome(status="completed", reason="needs_input", continuation="waiting")
```

The operation succeeded; its journey still needs input. Default continuation is
`"none"`. Failure, suppression and cancellation release the callable's active
context, without cancelling business work or deleting native state. An explicit
waiting result retains the description, not a suspended execution, agent instance
or graph. A fresh native call still requires eligibility and authorization. A
single-turn specialist is supported; chat/task handoff and durable resume are
rejected until native ownership contracts exist. Completed device discovery does
not become sticky merely because it returned successfully.

`scope(context, name)` returns a trusted nonempty string (stored hashed) or None
to disable continuation. Waiting records contain bounded metadata, no arguments
or results. Count and TTL apply across callable types, not separately per type.
`prepare_request` reconciles expiry, changed scope and lost eligibility at a
writable boundary. Once that boundary observes a cart change, switching back
does not revive the old record. Read-only discovery hides invalid records but
does not itself persist invalidation. A scope change during execution cannot
retain the result in the new scope. Hosts requiring immediate invalidation outside
model preparation should explicitly call `reconcile` at their state boundary.

`lifecycle.release(ctx, name)` is trusted host cleanup for every registered kind,
not a model tool, cancellation or transaction API. Running callable release is
rejected. Call tokens prevent stale finishes overwriting a different invocation
in a shared context; native concurrent session writes are still not transactional.
This does not implement distributed locks, business idempotency or durable resume.

Hosts on older ADK must project released/ineligible skill procedure bodies out of
requests before the next generation, including within an invocation. Preserve
result evidence and call/response pairing, not inactive instructions. A host-owned
compatibility adapter must implement that projection; generic SDK lifetime policy
alone does not provide native EPHEMERAL or rewrite history. Persisted events stay
unchanged. Scope of skills is native activation; application identity boundaries
must still explicitly release or invalidate that native state as appropriate.

Inspection reports `capabilityLifecycle="skills_and_callable_continuation"`.
Python-only: no YAML/export/Console lifecycle switch. Native Runner tests cover
workflow and specialist continuation, persisted scope invalidation and shared
failure/cancellation semantics. This replaces the unshipped retrieval-lifecycle
candidate API (`retrieval.lifecycle`, workflow-only limits and waiting status).

## Native skill lifecycle

`CapabilityBindings(skill_lifecycle=native_config)` forwards an ADK
`SkillLifecycleConfig` to its native skill owner. It does not implement an ADKL
activation store, timer or eviction policy. Configure a host-supplied
`native_toolset` directly instead. Do not combine this option with the legacy
`max_activated_skills` argument: the native `max_active_skills` cap only counts
BOUNDED skills, not PERSISTENT or EPHEMERAL skills.

This is an **opt-in path**. The native APIs first shipped in a stable release
with ADK **2.11.0**, which is now the locked baseline; the native lifecycle and
Runner lanes run in the default suite there. Earlier releases (including 2.9.x)
lack them, so the declared `google-adk>=2.6.3` floor still installs but
unsupported runtimes fail construction. Explicit lifecycle configuration also requires the
ADK `SKILL_LIFECYCLE` feature to be enabled before construction, because native
unloaded-instruction pruning is gated separately from lifetime configuration.
ADKL never changes process-global ADK feature flags.

- PERSISTENT is the unchanged native default.
- BOUNDED evicts the least recently loaded bounded skill; reloading refreshes it.
- EPHEMERAL lasts one invocation, including every model/tool step in it. Under
  native `run_live` there is no per-message invocation boundary, so do not claim
  per-message expiration.
- Native unloading/expiration removes skill-owned tools. With the feature enabled,
  native request preparation replaces obsolete load responses with unloaded
  notices, preserving call/response pairing and the stored session events.
- Native parallel activation/unload state writes are not an atomic transaction.
  This binding does not repair that upstream concurrency limitation.

With retrieval, `retain_active_skills=True` optionally retains eligible native
active skill names alongside ranker and host `retain()` results. It uses native
`list_active_skills(context)` when available, so stale stored ephemeral names
cannot restore expired activation. This is continuation, **not auto-loading**:
retention never renews a lifetime, and a shortlisted expired skill must be loaded
again. Persistent retention can widen the shortlist over a long session; opt in
only deliberately. Older ADK activity reads are isolated in
`adkl.skill_lifecycle.active_skill_names`; lifetime configuration is never emulated.

`unload_skill` is cleanup: it can release a registered skill outside the shortlist
or current exposure, but still calls host `authorize`. Business operations remain
subject to both shortlist eligibility and active-skill checks. Hosts must permit
cleanup explicitly if their authorization policy rejects revoked capabilities.

Lifecycle-enabled default toolsets use native EAGER discovery to emit shortlisted
skill descriptions. ADKL emits the other capability descriptions and shared
protocol, without duplicating the native skill catalog. A host discovery consumer
that does the same may declare `emits_capability_skill_catalog = True`; it must
emit only the shared shortlist. The default handshake remains ADKL-owned catalog
emission for compatibility.

Guarded native tools retain request preparation and confirmation hooks, metadata
and response scheduling. Workflows are registered directly on the native agent;
the binding guards the native-converted request dispatch entry rather than
reimplementing workflow schema conversion, validation or execution.

## Preserve an existing host skill runtime

Opt in with `native_toolset=host_skill_toolset` and the same `skills` declarations.
This must be a native `SkillToolset` (subclasses supported). Configure activation,
retrieval and LRU options on that toolset, not on the binding. It is installed
unchanged as the sole skill owner; no generic scoped skill wrapper is added.

The host owns **all skill exposure, auto-preparation, dispatch authorization,
remapping and skill telemetry** in this mode. Binding `policy`, `on_event` and
outcome adapters do not wrap skill execution; they still control workflows,
standalone tools and callable specialists. Do not assume a policy's hidden skill
name removes it from the host toolset. Host preparation must enforce its own
eligibility rules before injecting instructions. Inspection reports
`skillPolicyOwnership="host"`, `skillExecutionOwnership="host"` and
`autoActivation="host_owned"`. Automatic preparation is not a fabricated tool call.

Native descriptions/instructions are supplied by the host toolset; the peer
catalog adds only non-skill descriptors to avoid duplicating the skill catalog.
Already-prepared skills need no explicit load. Host callbacks for request-local
context/history and persistence are still configured by the host. This opt-in
preserves existing host preparation, not automatic latency or security parity.
The default (no native toolset) remains explicit, ADKL-scoped skill loading.

## Tools behind capabilities by default

Prefer `skill_tools=[...]` with the existing skill frontmatter
`adk_additional_tools` declaration. Native ADK exposes them only after skill
activation. Workflow-internal tools and specialist tools remain on their native
owners and are not hoisted into the parent catalog. Missing declared skill tools,
duplicate names and cross-scope collisions fail construction.

Optional `tools=[native_tool_or_function]` explicitly registers standalone tools.
The default is empty; the binding never infers or promotes internal tools. This
supports libraries that need direct operations without imposing that surface on
hosts that choose only skills/workflows/agents. Inspection distinguishes scopes.

## Explicit limits

- Without policy, all supplied descriptions are visible. Optional host policy
  filters exposure and rechecks dispatch for skills, workflows, standalone tools
  and single-turn specialists; see [policy](capability-policy.md). This API does
  not implement an embedding provider or auto-activation. Use the optional
  shared retrieval adapter above for large catalogs; prompt-only filtering is
  not access control.
- Skill loading is explicit. Compared with a host that auto-loads skills, this can
  add a provider round trip. No BAU latency parity is claimed for such migration.
- A workflow returns data to the parent by default. Native Runner tests demonstrate
  that this normally costs a subsequent parent generation. A workflow that owns
  an accepted answer needs an explicit, tested completion policy to avoid rewriting;
  the binding does not infer it from description text or set skip_summarization.
- Handoff uses native ownership: specialist answers without a parent rewrite.
  Task/single-turn modes retain native semantics, including return-to-parent.
- No durable workflow resume guarantee, device pricing policy, UI change, YAML
  feature, console editor or export support. Policy is not a sandbox against
  arbitrary host plugins or direct application calls outside the binding.
- Existing host tool scopes, continuation, retrieval, presentation and model
  normalization need host acceptance before replacing its serving construction.

## Evidence and compatibility

`tests/runtime/test_capability_bindings.py` runs real native objects through
InMemoryRunner with scripted providers. It verifies all three model-selected
paths, description-only initial context, deferred skill instructions, workflow
arguments/results, specialist prompt isolation, callback denial and constructor
validation. Scripted selection proves dispatch, not semantic routing accuracy.

See [agent integration](agent-integration.md) for pinned consumption and
[runtime bindings](runtime-bindings.md) for the existing shared lifecycle API.
CapabilityBindings is an opt-in sibling API, not a new feature flag within
RuntimeBindings. It reuses shared runtime composition internally; hosts install
only CapabilityBindings. No existing RuntimeBindings defaults change.

`tests/runtime/test_capability_composition.py` additionally exercises native
workflow results through raw observers and transforms, final request preparation,
cache short circuits, host-constructor preservation, inspection, staged delivery,
sink failure and second-turn stale-output cleanup. Per-invocation eligibility,
execution events, pre-emission validation and explicit workflow helpers are now
available through [capability policy](capability-policy.md). Policy-controlled
single-turn specialists are covered by `test_capability_specialists.py` with real
native Runners. Dynamic chat/task handoffs, logical-name remapping and inferred
target-owned answer completion remain unsupported.
