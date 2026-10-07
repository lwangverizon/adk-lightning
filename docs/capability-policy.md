# Capability policy and workflow execution helpers

These Python APIs provide eligibility and dispatch guards for
[Unified Capability Selection](capability-bindings.md) through `CapabilityBindings`. They do
not change RuntimeBindings or enable YAML/console capability authoring.

## Eligibility and execution

Pass `policy=object` implementing `expose(context, catalog)` and
`authorize(context, request)`. Both may be async. `expose` returns registered
capability names only. `authorize` returns
`adkl.capability_policy.CapabilityDecision(allowed, reason)`; reasons are bounded
codes, not private text. Policies must be read-only, inexpensive and idempotent.

Skill delegates are constructed from eligible local definitions before native
loading/tool resolution. Skill tools require an eligible activated owner;
shared tools execute once under a currently eligible active owner. Workflow and
standalone declarations are filtered before model execution. Dispatch rechecks
policy, including for previously obtained tool objects. Policy errors deny the
operation; exposure errors abort rather than expose an unrestricted catalog.
The scoped adapter inherits native SkillToolset dynamic enumeration so tools
activated by a load are visible to host before-model preparation in the same
invocation. It does not modify private ADK cache flags. There is no additional
registry/executor resource allocation or global invocation cache.

Dynamic policy supports specialists declared with `mode="single_turn"`. ADK
creates their native callable tools; ADKL wraps only the request-local canonical
dispatch mapping and filters ineligible declarations. Child instructions, native
execution, plugins and parent return behavior remain ADK-owned. Shared agent/tool
objects are not rewritten. Chat/task modes with a policy are rejected before
parenting: transfers and resumed task execution need a separate scope contract.
Static specialist catalogs continue to use native semantics. Cohort variants
must be distinct registered capabilities filtered by host policy; no automatic
logical-name remapping or durable continuation is supplied.

## Events and outcomes

`on_event(event)` receives immutable `CapabilityEvent` records for requested,
started, completed, failed, denied and cancelled adapter operations. Observer
errors are logged without payload and do not affect execution. The last 64 events
are retained in agent-scoped temp state; execution never consults that history.
Call IDs are bounded and strip the known provider `__thought__` suffix.

A nonexistent/filtered tool can be rejected by native ADK before the adapter,
so no adapter event is manufactured for that attempt. With the locked ADK,
a model call to a filtered specialist raises a native missing-tool error; ADKL
does not convert it into a successful parent answer. A previously exposed tool
that reaches the adapter after eligibility is revoked instead returns
`CAPABILITY_DENIED` and records a denied event. Parent guards/plugins may
short-circuit tools before adapter execution as well. These records are not a
complete authorization audit. Host transaction audit remains authoritative.

`outcome_adapters={capability_name: callback}` explicitly maps `(request, result)`
to `CapabilityOutcome(status, reason)`. With no adapter, completed means the
operation returned, not business success. Exceptions/cancellation propagate after
terminal events. No raw arguments, results, private text or usage are stored in
the generic event record. Arbitrary native plugin overrides are not sandboxed.

## Pre-emission validation

`pre_emit(context)` returns a CapabilityDecision before the shared emitter.
Denial clears pending flags and records a code-only diagnostic; nothing is sent
by that emitter. A validator exception, invalid return type or cancellation also
clears pending flags before propagating, with a code-only validation diagnostic.
Shared host-completion composition still applies to ordinary emission errors.
This is a whole-binding gate, not a per-widget gate. Cancellation of an
entire native invocation still depends on native cleanup; no durable rollback or
exactly-once delivery is promised.

## Explicit workflow helpers

`adkl.structured_call.structured_call` performs one schema-validated private call
through a host BaseLlm. Supply model, schema, instruction, payload, context, optional
GenerateContentConfig, prepare/observe hooks and timeout. It closes the provider
iterator, does not stream internal text or fabricate usage, and has no automatic
repair generation. `observe` receives every response including error usage.
When supplied a native context with `get_invocation_context()`, the helper reserves
one call from the shared ADK invocation budget before contacting the provider.
This does not create native usage events; the host must still account for actual
usage through its observer. Preparation may not introduce executable tools.

This is explicitly **not** a child LlmAgent: parent callbacks/plugins/planners
are not inherited. The host must supply required preparation/accounting hooks and
provider configuration. It is appropriate for bounded private data judgments,
not a general substitute for native child-agent execution.

`adkl.tool_execution.invoke_tool(tool, args, context, before=(), observers=(),
transforms=())` provides explicit guard/raw-observer/transform composition for
function-node business calls. A before-hook response prevents execution. It does
not emulate Runner plugins, auth negotiation or confirmation. Tools depending on
those facilities must execute in an appropriately configured native child agent.
In-tool business authorization remains required.

## Evidence and remaining host acceptance

Native tests cover policy refusal, state-driven filtering, terminal failure and
cancellation events, business outcomes, pre-emission denial, private structured
output and child-tool callback ordering. Scripted models establish mechanics,
not semantic routing accuracy. Hosts must evaluate current-context filtering,
retained sessions, mixed intents, private model accounting, factual claims and
transport/frontend behavior before enabling their candidate.
