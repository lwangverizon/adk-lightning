# Shared runtime bindings for existing ADK agents

`adkl.bindings.RuntimeBindings` installs skill selection, history processing,
active-skill context and staged presentation into a host-owned native `LlmAgent`.
It uses the same lifecycle implementation as `adkl.agent_runtime.build_agent`.
The application retains its model, dynamic instruction, planner, subagents,
Runner, session service and authorization.

The intended split is small agent-specific skills and domain tools, plus one
application-wide integration for identity, data preparation and transport. Those
host policies cannot safely be inferred from a skill or replaced by tool visibility.

Try the credential-free native Runner example from this checkout:

```bash
uv sync --locked --extra runtime
uv run --no-sync python -m examples.native_binding_smoke
```

## Preserve a host-owned skill lifecycle

Pass `native_toolset=host_toolset` when the host owns ranking, activation, tool
exposure and procedure loading. Supply `skills` and `tools` as explicit inspection
declarations; they are not installed as a second toolset. Do not enable
`skill_retrieval` or `skill_state_injection`, or supply selector/preparation/route
hooks in this mode. Packs/core tools are also rejected: the host owns exposure.
Use adopted `widget_specs` for independent staged presentation.

`merge_into()` retains the exact native toolset once, including when it already
occurs in the host's tools. It does not wrap its methods, change its caching, or
close it separately. Native ADK owns its lifecycle. Date seeding is disabled.
ADKL resets only its own diagnostics and widget flags before host callbacks.

An optional `native_observation(context)` returns a diagnostic dictionary (sync
or async). It runs before host before-model callbacks, after native preprocessing,
so it can observe cache-hit turns without reranking. It must not mutate requests
or perform matching; catch errors inside the provider for best-effort telemetry.
ADKL records `execution_owner: host`; declarations are not execution evidence.
Final request observers remain available through `before_model_observers`.

For **inspection only**, use `adkl.inspection.attach(agent, skills=..., tools=...)`.
This adds explicit metadata, not tools or callbacks, and returns the same agent.
Do not attach twice or attach to an already bound agent. The metadata is excluded
from native serialization. Use a fresh factory result to refresh declarations.
Neither interface requires subclassing ADKL internals. Inspection reports
`executionOwnership` separately from configured feature names.

## Construct from native objects

```python
from google.adk.agents import LlmAgent
from adkl.bindings import RuntimeBindings

# resolved_skills are native ADK Skill objects. domain_tools are native BaseTool
# instances or functions. query_embedder implements async embed(text).
binding = RuntimeBindings(
    skills=resolved_skills,
    tools=domain_tools,
    vectors=skill_vectors,  # skill name -> vector, same space as query_embedder
    embedder=query_embedder,
    descriptions=skill_descriptions,
    feature_config={
        "pipeline": ["skill_retrieval", "history_slicing", "skill_state_injection"],
        "skill_retrieval": {
            "auto_activate": False,
            "continue_active": True,
            "max_activated_skills": 3,
            "auto_activate_exclude": ["sensitive_workflow"],
        },
        "history_slicing": {"recent_turns": 5},
        "skill_state_injection": {"skills": {
            "account_help": {
                "summary": {"from_state": "account_summary", "type": "object", "required": True},
            },
        }},
    },
    prepare_skill=prepare_domain_context,
)
agent = LlmAgent(
    name="account_assistant", model=host_model, instruction=host_instruction,
    planner=host_planner, sub_agents=host_subagents,
    **binding.agent_kwargs(callbacks={"before_tool_callback": [authorize_tool]}),
)
```

This is an integration sketch: the host supplies the named objects. Construct
one binding per agent and call `agent_kwargs()` once. Pass original callbacks
through this method, not again on the agent. Do not also install the smaller
callback adapter or a second selection toolset. Tools referenced in skill
metadata must exist; use `core_tools=[...]` only for deliberately global tools.
Unknown features and missing declared tools fail construction.

For an existing ranking algorithm, pass `selector=...` instead of vectors and
embedder. Its async `match(text, state)` returns `adkl.retrieval.MatchResult`.
Candidates must be finite-scored and within the configured eligible
skills; a repeated candidate is de-duplicated on first occurrence rather than
rejected, since ranking is order-preserving and native ADK treats a repeat as
cosmetic. Each remaining rule reports its own violation and names the offending
skills. Automatic activation must identify the leading embedding candidate and
satisfy the binding's enable flag, exclusions, score and margin settings. A
strategy cannot expand the skill surface or enable disabled auto-activation.
The custom strategy owns ranking caps, pins and any query-history/cache policy;
the binding owns native loading and procedure refresh. Use the built-in vector
path to derive ranking settings from the same feature configuration.

Use `MatchResult.pinned=frozenset({...})` when a ranked candidate is also pinned;
do not duplicate its entry in `matches`. Pins must name exposed candidates.
Deterministic routing preserves this metadata, and continuation does not mutate
a selector's cached result. `skill_retrieval.pin_grace_turns` defaults to 5:
it counts confident competing selections that retain an already-active pinned
skill. Ambiguous turns do not consume the budget; winning again resets it.
Set 0 to disable pin-based retention. This limits active procedure retention,
not candidate reachability or authorization.

Vector callers own index provenance and regeneration. Every eligible skill must
have a vector; supplied vectors and query embeddings must use the same model and
space. The project-file factory additionally checks its vector-file metadata.
Set `skill_retrieval.query_history: true` to blend prior turns into the retrieval
query, so a terse follow-up keeps the skill still under discussion reachable. It
is an exponential moving average over one state-persisted vector, keyed per agent:
no extra embedding call and no growing state, with `query_history_weight`
(default 0.5, exclusive of 0 and 1) setting how long a topic is retained.

It widens the match set only. History candidates carry `source: "history"` and
score 0.0, so ranking, margin and every score stay current-turn and a skill that
only history surfaced can never auto-activate. State pinning covers the same
symptom for a *declared* in-progress flow; this covers the undeclared case. The
feature needs a writable session state and an agent scope; without either it
reports itself off through a warning rather than accumulating nothing silently.
A custom selector receives `scope` only if it advertises `accepts_scope = True`,
so the existing two-argument `match(text, state)` protocol is unchanged and a
host strategy owns its own history policy.

### Bounded weighted query history for custom retrieval

`adkl.query_history.QueryHistory` is a public, dependency-free Python helper for
custom selectors and host toolsets. It is **not** the `skill_retrieval.query_history`
EMA option above and does not silently replace its reachability-only semantics.
No new YAML feature or console setting is introduced.

```python
from adkl.query_history import QueryHistory

history = QueryHistory(weights=(0.60, 0.20, 0.10, 0.06, 0.04))
# Integration sketch: caller supplies the raw vector and invocation identity.
update = history.prepare(
    raw_query_vector, readonly_state,
    scope=agent_name, invocation_id=invocation_id,
    fingerprint="backend/model/retrieval-query/dimensions",
)
# Rank using update.vector, or use it only to widen candidates: host policy.
# Keep update owned by the live invocation, then in a writable callback:
history.commit(callback_context.state, update)
```

The window includes the current turn; five weights means current plus four
previous successfully embedded/committed turns. Each raw vector is normalized,
weighted newest-first, and the resulting query is normalized. Weights must be
positive, nonincreasing, and current-majority. Short windows renormalize available
weights. History contains raw normalized vectors and invocation IDs, never raw
messages or previously blended vectors. Assistant text is not inferred or embedded.

`prepare` reads but never mutates state. Persist with `commit` from a native
**event-producing callback**, such as `before_model`, after tool discovery and
before the model call. A writable preprocessing `ToolContext` alone does not
establish durable persistence. Cache the update alongside the match result so a
cache hit cannot skip the commit. Repeat model/tool calls in an invocation commit
only once. Active results and pending updates must survive unrelated cache
pressure; reclaim them at invocation completion, not through a diagnostic LRU.

State is agent-scoped under `adkl:weighted_query_history:<agent>`. Invalid history,
changed weights, dimensions or model fingerprint reset the window. Missing scope,
invocation ID or provenance and invalid current vectors raise. A read-only commit
raises rather than silently claiming persistence. A duplicate/stale commit returns
False; hosts still own serialization of overlapping turns and distributed session
concurrency. Old entries in a disabled window are not aged: clear its state key
when re-enabling after a pause if intervening turns must invalidate the context.

Keep the original raw vector for answer caches and repeat detectors, and current
text for lexical matching. The helper does not change activation policy, pins or
permissions. If a host ranks on the blend, scores and margins change: evaluate and
recalibrate auto-activation before rollout. Embed failures/short-circuit turns
without an update do not advance this vector window. This is a low-cost topic
continuity signal, not assistant-option or pronoun resolution.

### Small catalogs skip retrieval

`skill_retrieval.min_skills_for_retrieval` defaults to 5. With fewer than five
eligible skills, ADKL skips the embedder and custom selector entirely and puts
all eligible skill names/descriptions directly in the native prompt, even if
`skill_catalog` is `tool`. Exactly five still retrieves; set the minimum to 0 to
force retrieval for a small catalog. Count is after exclusions, before top-k/caps.

This does not load all skill bodies or infer automatic activation. The model
still calls `load_skill`; explicit trusted `route_skill` hooks and already-active
skill continuation retain their existing behavior. Diagnostics report
`retrieval_mode: all_descriptions`, `source: catalog`, zero scores/margin and no
inferred pick. No query history advances on these turns. Vector validation at
construction is unchanged. Direct `SkillRetrieval` callers get the same shortcut.

This policy belongs to ADKL's native selection lifecycle. Hosts that deliberately
replace that lifecycle (such as Prospect's native-delegating compatibility facade)
are not automatically changed; merely upgrading ADKL does not override host-owned
selection. No Prospect-specific threshold or rule is installed.

## Compose an existing constructor

Prefer this path for an existing host so planner, generation settings and other
native constructor fields survive the migration.

```python
# host_kwargs is the host's existing native constructor dictionary.
if use_shared_lifecycle:
    binding = RuntimeBindings(skills=resolved_skills, tools=domain_tools,
                              feature_config=shared_features)
    declarations = binding.inspect()  # repeatable; does not consume the binding
    host_kwargs = binding.merge_into(host_kwargs)
agent = LlmAgent(**host_kwargs)
```

This sketch uses host-supplied inputs. `merge_into()` returns a new dictionary
and leaves the input and its tool/callback lists unchanged. Callback values may
be a callable, a sequence or `None`; the lifecycle ordering below still applies.
Unrelated constructor fields pass through. Existing host tools remain globally
visible. A tool also owned by the binding is rejected, since retaining it globally
could bypass skill scope. Remove those entries from the host tool list explicitly.
Existing native skill toolsets and duplicate named tools are rejected. Arbitrary
dynamic host toolsets are retained without enumeration; the host owns their
runtime collisions and authorization.

Both `merge_into()` and `agent_kwargs()` issue one agent's mutable lifecycle once.
Use `inspect()` for repeated declaration review before or after composition; create
a fresh binding to rebuild an agent. Do not reuse issued kwargs for another agent.
These methods do not evaluate host instructions or execute tools during inspection.

Selection and preparation bookkeeping are agent-scoped within ADK's temporary
invocation state, including parallel agents using the same skill name. Native
agent names must remain unique in the tree. The compatibility `adkl:last_turn`
snapshot describes the most recent writer; per-agent diagnostics live at
`adkl:last_turn:<agent>`. These are diagnostic data, not host control inputs.
Host business state and explicitly shared widget registers still require the
host's concurrency policy.

`seed_today=True` supplies an ISO local date when `today` is absent and refreshes
dates ADKL itself seeded on later invocations. A pre-existing value is preserved;
replacing ADKL's value relinquishes its ownership. Set `seed_today=False` on
host bindings when the host owns date/time policy, including when assigning the
same value ADKL previously generated. Existing unmarked session values are
treated as host-owned; explicitly clear an old ADKL default to adopt refreshing.
The same option is available on `build_agent()` and `AgentRuntime`.

## Diagnostics on the host path

The console reads resolution warnings and output findings from its own endpoints.
A host binding has no endpoints, so pass the channels explicitly. Without them
the diagnostics are computed and discarded, which is not the same as absent.

```python
binding = RuntimeBindings(
    skills=resolved_skills,
    feature_config=shared_features,
    on_warning=log.warning,   # list[str], once per agent_kwargs()/merge_into()
    on_finding=audit.record,  # list[str], per turn, from output filters
    root=project_root,        # resolves prompt_state_injection from_file slots
)
for note in binding.resolution_warnings():   # readable without a callback
    log.warning(note)
```

`on_warning` receives what `features.resolve` and the SDK capability manifest
flagged but allowed: an `enabled: false` kill switch that beat pipeline
membership, a non-stable feature in the pipeline, an option made inert by a gate
that skips the block reading it, a feature whose short-circuit shadows a later
one, and partial runtime support. `resolution_warnings()` returns the same list
on demand; `binding.inspect()["warnings"]` carries it too. Both are `list[str]`.

`on_finding` receives output-filter findings, including every PII redaction the
pipeline performed. A host running `pii_redaction` without this callback has no
audit trail for it. Findings are diagnostic strings, not host control inputs.

`root` is required for any `prompt_state_injection` slot with `from_file`;
construction fails with the slot name if it is missing. Both callbacks must be
callable or `None`, checked at construction rather than at first use.

Staged widget emission failure is reported through `on_finding` and does not
abort the host's `after_agent_callback` chain, because the lifecycle wrapper
replaces that chain rather than appending to it — a raise would skip the host's
own per-turn cleanup. With no `on_finding`, the fault is re-raised after the
chain has run, even when a completion callback returned content. A failing finding
callback also cannot skip host completion; the delivery exception is re-raised
with the reporting failure as its cause. Native short circuits still stop later
host completion callbacks after the first content result. When reporting succeeds,
that content retains precedence. A type-only `delivery_error` is recorded under
`adkl:widgets:<agent>`; exception text is not copied into that diagnostic. Failed
invocations do not guarantee persistence of every state delta.

## Domain context preparation

### Domain routing

For trusted pins or deterministic anchors, `route_skill(text=..., state=...)`
may return a candidate skill name (or None), synchronously or asynchronously.
This explicit policy bypasses embedding confidence, but cannot select outside
eligible candidates, override exclusions, or enable disabled auto-activation.
Diagnostics identify `activation_source: route` separately from embedding selection.
The host owns the route's authorization and calibration.

```python
async def prepare_domain_context(*, skill_name, context, reason):
    # Resolve identity from trusted context; perform authorized, idempotent reads.
    # Commit required state before returning. Do not return a tool response.
    if skill_name == "account_help":
        context.state["account_summary"] = await authorized_account_summary(context)
```

Preparation runs before explicit `load_skill` host guards, so a guard can read
prepared state. It also runs before automatic native loading, which does not
pass through ordinary tool callbacks. Therefore the preparation hook must itself
validate any identity/authorization required for its reads. Raise on failure;
ADKL does not activate the skill or call the model after failed auto preparation.
A required state projection also fails before the model if data is absent or has
the wrong type.

Successful preparation is memoized by skill for the invocation; it resets next
invocation. Parallel calls can overlap before that marker is committed: providers
must support idempotent reads and own any single-flight/locking requirement.
Preparation is not account caching, freshness enforcement or transaction consent.
Unavailable explicit skill names do not trigger preparation. Host guards still
run for explicit tools and can refuse execution; selection is not authorization.

Project-file SDK callers can pass the same `prepare_skill`, `widget_sink` and
`request_id` hooks to `build_agent()` or `AgentRuntime`. These are Python integration
parameters, not editable YAML callback fields.

## Staged presentation and delivery

Native bindings accept `presentation={"version": 1, "packs": ["adkl/common@1"]}`
and install shared tools and the widget specification. Enable `staged_widgets`
in the feature pipeline. Tool-name collisions fail construction. Use
`exclude_kinds` to retain a domain implementation of a pack kind; excluded kinds
remain available to the console's generic preview but have no installed pack tool.

Wrap a domain card function with `adkl.staged_tool.StagedResultTool(fn, spec)`
and install the same register-backed spec in `widget_specs`. Successful
`success`/`rendered` results must contain a dict in `card` (override with
`result_field`). The wrapper suppresses immediate widget delivery, stages the
payload and returns `staged`, allowing companion text before end-of-turn emission.
Failed results are unchanged. Domain fields are preserved: the domain tool must
remove private answers and validate its own payload.

The [minimal native agent](../projects/native-agent/README.md) is a complete,
credential-free example of a host constructor with all four bindings and staged
A2UI cards. Its tool stages `{"type": "a2ui", "operations": [...]}` payloads;
the console uses the shared versioned reducer without a host converter registry.
Each payload carries a complete surface snapshot. See
[A2UI payload support](a2ui-support.md#native-staged-payloads) for its boundaries.

Pass `widget_specs` (native `adkl.widgets.WidgetSpec` objects, or specs converted
with `adkl.widgets.adopt`) and enable `staged_widgets`. Existing tools may keep
writing their declared register keys; new tools can use `adkl.widgets.stage`.
Domain builders, suppression and revival policies remain explicit spec hooks.
The binding does not require replacing existing semantic cards with generic packs.

Adopted builders and expected-empty predicates receive the original register
shape, including dictionaries. SDK-owned list registers retain list-copy semantics.

`adopt()` preserves a declared `payload_type` and accepts known role strings or
foreign enums with matching string values. Unknown static roles produce a Python
warning and inspection/audit findings (`WIDGET_ADOPTION_LOSS`); they still render
without a reply contract. A missing spec key fails adoption instead of silently
dropping the spec. Inspect `adoptionWarnings` before completing migration.

Set `staged_widgets.inject_contract: false` when the host owns reply shaping.
ADKL still resolves and records the contract and emits widgets; it simply omits
its instruction block. This lets inspection remain useful without double injection.

`widget_sink(context, widget_id, payload)` may be synchronous or asynchronous.
It returns `None` after accepting delivery, or raises. A raised delivery error
propagates and does not mark the spec emitted. Partial multi-payload delivery is
possible; the host owns deduplication, retry and client acknowledgement. This is
not an exactly-once transport guarantee. Without a sink, the existing generic
`render_ui_widget` path is used. `request_id(context)` optionally supplies dynamic
correlation to the builder instead of the configured fixed string.

`companion_sink(context, text)` takes ownership of fallback companion text when
a widget emitted but the model produced no text. It follows the same synchronous
or asynchronous, return-None-or-raise contract. Without it, ADKL returns the
companion as model content, which enters conversation history. Inspection reports
`companionDelivery` independently of widget delivery.

The lifecycle clears stale dirty/emitted flags before host short circuits,
records surviving dropped register keys in `temp:adkl_dropped_stages`, refreshes presentation
guidance and emits at completion. Shared emission runs before host after-agent
callbacks, so a content-returning completion hook cannot silently skip it. Host
completion content takes precedence over the shared companion fallback. Move
cache admission and post-emission analytics into those host completion callbacks.
ADK may already have discarded temporary keys at a new invocation; durable
failed-delivery auditing remains the host’s responsibility.
If a host needs a different emission position, keep that flow on its old binding
until its ordering is explicitly adapted; do not register two emitters.

## Skill ownership and callback placement

Defaults for the following options are defined in the
[skill feature options](../adkl/feature_definitions/skills.py) and exposed by the
feature catalog in the Console. The meanings are:

With `skill_retrieval.native_activation` set to `replace`, ADKL prunes ADK's
shared activation list to the current selected/retained skills. `merge` preserves
other writers' entries after ADKL's own; `own` leaves the shared key to native
loading/host code and keeps a private continuation record. Neither mode broadens
ADKL's selected tool/procedure surface. Hosts choosing merge/own own pruning of
the shared key, which can otherwise grow.

`skill_retrieval.skill_hook_position` set to `last` places refresh after host
before-model callbacks. `first` places shared procedure/context refresh ahead of
host callbacks; final transforms and observers still follow. Use it when host context processing
requires native-like ordering. Do not install duplicate lifecycle hooks.

`skill_retrieval.skill_catalog` set to `tool` exposes `list_skills`; `prompt`
places descriptions in the prompt instead. The small-catalog cutoff above
forces prompt mode. On ADK 2.11 and later this uses native
`discovery_mode=SkillDiscoveryMode.EAGER`. Older ADK releases fall back to
removing `list_skills` from ADK's internal tool list (deprecated in 2.11), which
fails explicitly if that compatibility surface disappears.

## Callback ordering and inspection

For format-aware output processing, pass `after_model_transforms=[...]` to
`agent_kwargs()` or `merge_into()`. When explicit transforms or shared output
filters are installed, the chain passes host response replacements to subsequent
shared filters and then explicit transforms. A return of None retains the current
response; errors propagate. Without either, native host ordering is unchanged.
This does not buffer streaming deltas. See [migration notes](native-host-migration.md)
for retired declarations and deprecated blanket output policies.


- Before-agent: reset diagnostics/stage flags, host callbacks, shared selection.
- Before-model: host callbacks, shared history/context callbacks, staged guidance,
  refreshed active skill procedures and typed context, final host request
  transforms, then observers.
- Before-tool: shared explicit-load preparation, host guards.
- After-tool: declared raw-result observers, host replacement transforms, shared
  replacement transforms. Observers must not mutate results and must return None.
- After-agent: shared emission, host callbacks; native short-circuit semantics
  remain within the host callback sequence.

Use `after_tool_observers=[...]` for telemetry that must see the original result.
Use `before_model_observers=[...]` to inspect the request after shared processing;
these hooks must return None and must not mutate the request.
Host `after_tool_callback` entries are transforms that see the latest replacement,
including empty values. Put application-specific tool-result reduction here;
ADKL's [generic size guard](tool-results.md) never selects business fields or rows.
For gateway normalization or model-specific steering
after ADKL processing, pass `final_model_transforms=[...]` to `agent_kwargs()` or
`merge_into()`. Each receives `callback_context` and `llm_request`, mutates the
request in place, and returns None (or raises); sync and async hooks are supported.
Returning content is an error rather than a silent short circuit. Observers run
after these transforms. Native ADK processing outside this callback chain and
host Runner plugins retain their own ordering. Inspection reports the transform
count; this is not an arbitrary callback-position editor.

Attach the host's factory to the console using the existing
[project attachment workflow](project-onboarding.md#existing-adk-applications).
Inspection sees the native constructor and shared callbacks; local Test uses a
disposable Runner. It does not prove host authentication, production transport or
runtime-only tool availability. Feature changes in a host factory must be made
in that factory; the console does not translate arbitrary Python into YAML.

<a id="native-capability-catalog"></a>

## Unified Capability Selection

For one selection surface across native skills, workflows and specialists,
see [Unified Capability Selection](capability-bindings.md), implemented by
`CapabilityBindings` with optional `CapabilityRetrieval` shortlisting. This
separate opt-in constructor API uses a host-supplied catalog and native dispatch.
Its `merge_into()` reuses
shared callbacks, transforms, staged widgets and inspection without installing a
second selector. Install one binding, not both. It does not automatically migrate
ranked host skill selection or infer workflow answer ownership.

## Adoption sequence and remaining work

1. Capture existing behavior and callback order in host tests.
2. Replace history processing once; preserve gateway-specific transforms until
   outgoing-request parity is established.
3. Adopt widget specs and the host sink; compare real client payloads and retries.
4. Move selection and context together, preserving ranking via a custom selector
   where needed. Keep automatic activation disabled until calibrated separately.
5. Remove duplicated host mechanics after its actual end-to-end evaluation passes.

The binding removes a major migration prerequisite; it does not complete a host
migration. Hybrid ranking beyond the built-in lexical and rolling-history
backfills, variant-resolution policy and host-specific transport evaluation remain
separate work. Private runtime distributions require their own compatibility
suite. ADKL static audits still cannot prove semantic instruction consistency or
behavioral quality.

Native inspection includes declared skills, tool schemas and scopes, callback
order, shared features and static content findings. It does not evaluate dynamic
instruction callables during inspection. A local Test records the last root-agent
request's composed instruction and content count after shared processing. This
local diagnostic snapshot can contain host context; clear or the next turn resets
it. Widget contracts and emission gates appear in the turn trace. This is a local
disposable Runner, not attachment to an already running production process.
