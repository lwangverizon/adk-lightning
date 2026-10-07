# ADKL as a reusable agent engineering layer

Design proposal, September 6, 2026. This proposes direction and acceptance
criteria. The first implementation is now described in [SDK contracts](sdk.md).
The baseline table below records the state before that implementation, not the
current capability inventory.

## Product thesis

ADKL turns production-derived agent patterns into versioned runtime contracts,
authoring conventions and repeatable quality checks on top of Google ADK.
Teams should not need to rediscover callback ordering, state lifetimes or prompt
placement every time they build an agent with a coding assistant.

Two entry points should consume the same definition and runtime:

- Engineers use ADKL with ADK in their own application, session service and
  deployment. Adoption must not require the console or development server.
- Business authors use the console to compose approved skills, tools and
  presentation packs, inspect findings, and test workflows. Engineers supply
  executable integrations and their operational constraints.

The promise is consistent execution of declared contracts and evidence of agent
quality. “Always clean” is an aspiration, not a universal model guarantee.
Production experience motivates a pattern; reusable fixtures establish whether
the generalized implementation preserves it.

## Shared architecture

Both YAML/UI authoring and a typed Python authoring interface should resolve to
one validated agent definition, capability report and native ADK runtime factory.
Preview, SDK applications, evaluation and eventual runnable export must consume
that factory. UI-only callbacks would recreate the consistency problem.

The SDK should offer a complete factory and composable pattern adapters with
explicit dependencies, callback phases and state ownership. An adapter must
reject incompatible configuration or callback composition instead of silently
overwriting application hooks. Custom extensions must report what they enforce
and supply contract tests; they cannot inherit a blanket certification.

ADK retains ownership of its runner, agents, sessions/events, native skills and
tool execution. ADKL owns selection policy, context assembly, presentation
coordination and diagnostics. Projects own business procedures, domain tools,
data and policy choices. Runtime imports remain optional; static authoring and
audit stay stdlib + PyYAML.

## Initial runtime patterns

| Pattern | Shared contract to build | Project inputs | Baseline before implementation |
| --- | --- | --- | --- |
| Embedding skill selection | Separate candidates, activation, procedure loading and tool exposure; explicit continuation, ambiguity and failure policy; source/model/index identity | Descriptions, exclusions, ranking thresholds, permitted continuation rules | Partial preview: cosine/BM25 selection and native SkillToolset; no previous-turn ranking or pre-LLM activation |
| Staged widget emission | Validate and stage typed tool results per invocation; ordered deduplicated emission; companion-text contract; cancellation and failure behavior | Presentation pack, result binding, domain payload and display intent | Implemented preview staging and packs; required-widget enforcement, actions and export absent |
| History slicing | Construct bounded model context while preserving current turn and valid tool exchanges; keep durable session history; explicit over-budget behavior | Recent-turn/token budget and retention policy | Preview bounds actual user turns; a complete token-budget policy is proposed |
| Skill state injection | Inject declared typed context only for active skills; identify source, freshness, missing-value behavior, scope and cleanup; distinguish data from instructions | Namespaced read/write declarations and trusted state providers | Partial generic state/file context append; no complete skill-scoped lifecycle |

Treat “skill state injection” as both the skill activation lifecycle and the
business context that a loaded skill needs. These are separate state namespaces.
Persist changes through ADK context/event mechanisms; do not create a parallel
session store. Missing required context must lead to an explicit recovery path.
Topic switches must remove obsolete skill context and tool exposure. Context
from users or tool results must not acquire policy authority through injection.

A proposed invocation sequence is: read committed session state → select
candidates and resolve activation → load the procedure and permitted tools →
assemble instructions and scoped state → slice model history → invoke the model
and tools → validate/stage presentation → finalize and emit. Native model-driven
skill loading can require another model call; the same context rules must apply
after loading and on every subsequent call. Selection alone never authorizes an
action. Authorization remains an executable tool-boundary requirement.

Every pattern needs a versioned configuration schema, compatibility declaration,
observable events, synthetic fixture, failure tests and migration guidance.
Do not promote preview support to SDK/export support until those paths exist.

## Make the repeated audits a product capability

### Instruction ownership and conflicts

Use explicit ownership rather than resolving contradictions by concatenation:

| Surface | Responsibility |
| --- | --- |
| Application/system policy | Universal behavior, limits and cross-skill rules |
| Skill description | When to activate and when to exclude this skill |
| Skill procedure | Task steps, branching, recovery and completion |
| Tool schema/docstring | Inputs, outputs, errors, side effects and invocation preconditions |
| References and injected state | Knowledge and current facts with provenance |

A skill must not relax application policy; a docstring must not become a second
business workflow. Runtime checks enforce consequential preconditions. This is
an authoring contract, not a claim that heading order changes model message
authority or that prose can enforce authorization.

Extend the existing shared lint implementation rather than adding independent UI
rules. Audit the assembled prompt, selected skills, actual exposed tool schemas,
presentation contract and context declarations for a resolved agent revision.
Findings should identify both conflicting sources, scope, confidence and a
suggested correction. Reject structural contradictions that are mechanically
established. Keep heuristic prose findings reviewable; word overlap alone does
not prove a semantic contradiction.

### Skill precision and complexity

Each skill should specify activation examples and exclusions, required inputs,
minimal tools, procedure, recovery and completion. Keep one coherent outcome per
skill; split when activation boundaries or responsibilities differ, not merely
because a token threshold is exceeded. Put substantial conditional detail in
references, while keeping routing signals in the description.

Existing cost and conflict lint supplies a starting point. Add reviewed cases
for positive activation, adjacent intents, negative activation, terse follow-ups,
topic changes, missing state, empty results and tool errors. Optional model-assisted
review can identify ambiguous or overloaded prose, but must remain distinct from
deterministic validation and cite the evidence behind its judgments.

### Model-call efficiency

Optimize model calls, latency and cost per verified successful task. Count model
calls separately from user turns, tool calls and embedding requests. Shorter
instructions or fewer calls alone do not establish an improvement.

Prefer deterministic state injection over asking for known facts; bind a widget
to an existing successful domain result when a separate display call adds no
decision; execute fixed tool dependencies in code; batch independent reads when
semantically safe. Consider pre-LLM activation only with tested ambiguity and
continuation policies. Preserve necessary clarification and confirmation.

Measure actual call traces, task success, incorrect actions, input/output tokens,
and p50/p95 latency against the same model/tools/scenarios. Budget exceptions
should name their reason. A failed shortcut must not look like an efficiency win.

## Delivery sequence and acceptance

1. Capture the four external application patterns as synthetic contract fixtures, with expected
   state transitions, model inputs, tool availability and widget events. Record
   where the production behavior still needs confirmation; no private checkout
   or customer credentials should be required for ordinary tests.
2. Extract one shared runtime factory from preview and exercise it from a small
   standalone ADK application. First acceptance: both paths produce equivalent
   routing/loading, sliced context, scoped state and staged output for the same
   fixtures, including failure and second-turn cases.
3. Complete activation/state lifecycles and index readiness. Test ambiguous
   matches, embedding outage, stale indices, topic changes, expired context and
   cancellation without cross-session or cross-invocation leakage.
4. Expose the shared audit report in CLI/CI and console. Include source evidence
   and distinguish structural errors, heuristic warnings and measured behavioral
   regressions. Calibrate quality/cost thresholds against reviewed baselines.
5. Validate reuse in two different domains, such as commerce and tutoring, with
   no copied framework callbacks. Expand business-author UI templates around
   stable contracts, then enable runnable export after parity evidence exists.

Release evidence should bind agent content, runtime/ADK, tools, model settings,
index and evaluator revisions. Existing production integrations, remote CI and
live-model evaluation remain separate checks; this proposal executes none of them.

## Sources and implementation anchors

- [Capability inventory](../adkl/capabilities.py),
  [shared presentation/retrieval features](shared-features.md),
  [preview construction](../adkl/chat.py), and [callbacks](../adkl/runtime.py).
- [Instruction compiler](../adkl/instruction.py),
  [shared lint](../adkl/skill_lint.py), [conflict checks](../adkl/lint_conflict.py),
  and [cost checks](../adkl/lint_cost.py).
- [Existing evaluation gates](ci-cd.md#agent-content-and-evaluation-gate).
- ADK's [native SkillToolset source](https://github.com/google/adk-python/blob/main/src/google/adk/tools/skill_toolset.py)
  and [state documentation](https://github.com/google/adk-docs/blob/main/docs/sessions/state.md)
  inform the native extension boundary. Upstream main is not a compatibility
  promise; verify contracts against this project's locked ADK version.
