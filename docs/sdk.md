# ADKL Runtime: SDK contract and diagnostics

Start with the [quickstart](quickstart.md) or [incremental adoption guide](adoption.md)
for a short walkthrough. This page documents the exact runtime contract.

**ADKL Runtime** means shared execution patterns inside native ADK agents, not a
separate server. Choose `adkl.adapters.build_callback_adapter` for selected
callbacks, `adkl.bindings.RuntimeBindings.merge_into` for an existing constructor,
or the project-file factory below. `adkl.runtime` contains public callback
factories and wiring; it is not the primary full-agent construction API.
[Doctor](doctor.md) diagnoses declarations, while [Console](authoring.md) adds local
authoring/inspection and disposable tests. Neither replaces production hosting.

The public `adkl.agent_runtime.build_agent(project_root, env=..., model=...,
embedder=...)` returns a native ADK `LlmAgent`. `model` and `embedder` are optional
injected implementations for tests or host-owned integrations. Without overrides,
the configured provider and explicit embedding model/dimensions are used.
`AgentRuntime(...).app` provides an ADK App when the host needs one. The host owns
its Runner, session service, run limits, identity and tool authorization.

The wheel is installable with the optional `runtime` extra. It contains ADKL,
not project content, credentials or deployment profiles. Existing repository
uv commands and locks remain unchanged. Different projects may import identical
Python module names: isolate those projects in separate processes, as the console
worker does. The SDK is not a tenant or hostile-code isolation boundary.

## Host-owned agents

Use [RuntimeBindings](runtime-bindings.md) to supply native skills and tools while
keeping the host constructor. It shares the project factory’s lifecycle and accepts
context-preparation and widget-delivery hooks.

## Incremental callback adoption

For incremental adoption into an existing native ADK agent, use
`adkl.adapters.build_callback_adapter(feature_config, ...)`. It returns explicit
before-model, before-tool and after-tool constructor kwargs while the host keeps
its model, skill toolset, guards and runner. Raw after-tool observers run before
sequential transforms; only transforms may return replacement results. Supported
features and migration limits are detailed in the
[incremental adoption guide](adoption.md).

The opt-in `tool_history_repair` feature repairs completed outgoing tool batches
on copies, preserving stored events and recording unknown outcomes without
retrying tools. `tool_result_projection` selects declared fields from a list
path while retaining every row and the result envelope; it leaves source state
intact. These share the YAML factory path. Neither requires application-specific imports;
neither enables authorization, private-wheel parity or standalone export.

## Native invocation contract

1. Reset per-turn diagnostics before application callbacks can short-circuit.
2. Run declared project callbacks with native ADK context/state.
3. Select candidates using retrieval and configured state pins, or expose all
   eligible descriptions below the small-catalog cutoff. Prune activation to
   reachable skills; optionally load a confident match using native ADK loading.
4. Native SkillToolset exposes only selected skills and tools of active skills.
   Pure pack presentation tools and explicit `tools.core` bindings are global.
5. Apply history/context callbacks on every model call. Refresh active procedures
   so slicing away an old load response cannot remove their current instructions.
6. Stage validated tool-result presentation per invocation and emit at completion.

Selection is context-scoped; it does not mutate `agent.tools` between user turns.
The same agent can serve different ADK sessions without sharing skill selection.
Concurrent writes to the same session remain the host's responsibility.
Separate bindings in a native parallel tree keep their selection and preparation
bookkeeping agent-scoped. Application state and deliberately shared widget
registers remain host-owned.
Host before callbacks retain native ADK short-circuit semantics. Shared staged
emission precedes host after-agent callbacks; their returned content takes
precedence over the companion fallback. After-tool transformations are composed so presentation sees transformed results.

`skill_retrieval.state_pins` entries accept `state_key`, `skill`, and optional
`active_when` (`non_empty` or `truthy`). Pins are additive and may exceed
`total_cap`; they cannot revive excluded or unconfigured skills. Clearing the
source state removes the pin on the next turn. A pin makes a skill available;
it does not itself activate it. Exclusion/tool visibility is not authorization.

Set `skill_retrieval.continue_active: true` to retain up to
`max_activated_skills` (default three) recently active
skills when a follow-up has no confident ranking winner. Retained candidates are
traced as `continuation`; they can exceed the candidate cap. A confident new topic
clears prior unpinned activation. Exclusions still apply. This policy is opt-in;
use explicit state pins for durable business
flows. Without continuation, callers must reload skills when selection changes.

`pin_grace_turns` (default 5) bounds active pin retention across confident competing
selections; 0 disables that retention. Ambiguous turns do not decrement it and a
new win resets it. Pins still keep candidates reachable independently of retention.

`build_agent(..., seed_today=True)` and `AgentRuntime(..., seed_today=True)` seed
and refresh an ADKL-owned `today` date. Existing host values are preserved. Use
`seed_today=False` when the host owns the date, timezone or calendar; see the
[binding ownership contract](runtime-bindings.md#compose-an-existing-constructor).

Auto-activation requires both the configured score and margin. Margin is computed
before candidate truncation and the score floor. When disabled or ambiguous,
the model uses native `load_skill`. Embedding outage, invalid query dimensions
or non-finite query values trigger the reported eligible-candidate fallback and
never automatic activation. `lexical` controls lexical-overlap backfill, not a
full BM25 implementation. The old `bm25` alias still works with a deprecation
warning; conflicting values of the two keys are rejected.

For the default small-catalog bypass, built-in EMA query history and the separate
host-owned weighted-history helper, see [retrieval policy](runtime-bindings.md#small-catalogs-skip-retrieval).
All automatic activation remains opt-in; a catalog description is not an active
procedure, and a state pin is not authorization.

## Skill state injection

```yaml
features:
  skill_retrieval:
    auto_activate: true
    state_pins:
      - {state_key: active_homework, skill: homework-session}
  history_slicing: {recent_turns: 5}
  skill_state_injection:
    skills:
      homework-session:
        topic: {from_state: homework_topic, type: string, required: true}
  pipeline: [skill_retrieval, history_slicing, skill_state_injection]
```

This excerpt supplements the usual model, vectors, skill and tool configuration.
Fields accept `string`, `number`, `boolean`, `object`, `array`, or `any` (default).
Missing optional values are omitted and named in diagnostics; missing required
values and invalid types fail before invoking the model. The projected JSON is
limited to 64 KiB. Values are data, not policy instructions. The host must decide
which state providers are trusted and when their values expire.

Projection is rebuilt for active skills each model call, rather than copied into
persistent prompt state. Native `metadata.adk_inject_state` interpolation is also
honored. Old context may still exist in retained conversation history; projection
is not data erasure. History slicing changes the outgoing request, not durable
ADK events. It currently uses user-turn counts, not a hard token budget.

## Audit and evidence

Run `python -m adkl.audit /path/to/project` for JSON output; exit status is nonzero
on errors. `GET /api/audit` exposes the same report for the selected on-disk
project. The existing editor also shares the lint rules and now reads the exact
compiled prompt with selected slots, rather than every neighboring prompt file.

The report resolves catalog tool names, checks prompt/skill/docstring conflicts,
reports prompt references that require skill-scoped tools, description overlap
and token-cost heuristics, and supplies per-skill
review prompts for activation boundaries, recovery, completion and avoidable model
calls. It identifies static evidence and reports behavioral evaluation as unrun.
The source digest covers config, compiled prompt, skill Markdown and the tool
catalog; it is not a complete executable release digest.

Native session state `adkl:last_turn:<agent>` records per-agent matches, fallback,
activation, missing context and model calls. The legacy `adkl:last_turn` snapshot
is the most recent writer and may belong to another agent in a parallel run.
These snapshots are diagnostics, not host control inputs. Console traces expose `modelCalls` and
`activatedSkills`. Compare these with actual task outcomes; a low call count
alone is not a quality pass. Model-assisted semantic review and versioned
behavioral grading remain future work. Existing lint heuristics cannot prove all
natural-language instructions consistent or all skills appropriately complex.

## Compatibility and remaining limits

Host-produced behavioral results can be compared with the separate
[evaluation comparison CLI](evaluation.md). It checks evidence compatibility and
reported regressions; it does not execute agents or change the static audit's
`behavioralEvaluation: not_run` status.

The console uses this factory; external applications no longer need to import
`ChatSession` to get ADKL behavior. Declared tool import failures now block
construction rather than silently running a prompt-only approximation. Remote
execution profiles remain blocked. Unsupported features still fail through the
shared capability manifest, including direct SDK calls.

Generated standalone export remains guarded; the supported runtime path is the
installed SDK plus project files and the host's native Runner. This release does
not add interactive quiz grading, authentication, background index publication,
or production deployment infrastructure. Optional retrieval history and custom
weighted-history policies are described in [runtime bindings](runtime-bindings.md).
Tool-result sizing and its experimental migration are documented in the
[generic result guard](tool-results.md).
