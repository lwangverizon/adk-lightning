# Shared presentation feature

Status: historical design roadmap; application references describe the original
design inputs, not projects currently shipped in ADKL. See the maintained
[minimal examples](../projects/README.md). The display-only pack/result-binding slice is implemented;
see [the implemented contract](shared-features.md). Expectations, repair,
interactive actions and export described below remain proposals. This extends the existing
`staged_widgets` feature rather than adding a competing emission pipeline.

## Implemented host-owned reply policy

The separate [shared presentation-contract API](presentation-contracts.md) provides
opt-in versioned visual reply guidance. Hosts supply eligible roles and inject it
once; native gating, delivery and business constraints remain host-owned. This is
implemented policy preparation, not the render-plan/repair proposals below, and
does not automatically replace existing widget contract wording.

## Outcome and boundary

ClawTutor and prospect reference versioned ADKL presentation packs. They do not
implement staging registers, ADK emission callbacks, companion-text handling,
primitive converters, or Flutter card classes for supported presentations.

ADKL owns presentation execution. Domain tools still own curriculum retrieval,
question selection, grading, product offers, eligibility, identity, and writes.
An agent declares which presentations it enables and when its workflow expects
one. ADKL cannot infer those business decisions from arbitrary tool output.

The smallest project configuration is a feature plus a pack reference. Existing
tool integrations can additionally declare a result binding. No project Python
presentation implementation is required for the standard path.

## Findings motivating the design

- `adkl/widgets.py` already owns roles, contracts, staging, suppression gates,
  and emission, but specs still require a project `build` callable.
- `adkl/a2ui.py` imports a project converter package. Portable conversion is
  therefore not yet a platform capability independent of `tools.repo`.
- `ChatSession` attaches native skill tools, but does not attach a separate
  always-available set of presentation tools. ClawTutor's greeting and fact tools
  are absent even after loading all included skills.
- ClawTutor directly registers widgets and returns semantic `card` JSON. The
  original application collects returned cards at completion; Lightning relies
  on ADK widget actions. Registration errors in copied tools are swallowed.
- ClawTutor's primitive greeting converter reads different fields from its
  tool. Its quiz converter duplicates label nodes as column children.
- `emit_staged` returns a companion caption, but `ChatSession._make_emitter`
  records that result and returns `None`; it does not deliver that caption.
- Flutter can display primitive cards, but button actions are currently inert.
- Prospect config adopts an external spec registry with builders and predicates.
  Its configured external checkout was unavailable during this design review;
  full mapping of its cards requires inspecting that registry in its approved
  environment. The design does not claim all prospect cards already fit it.

## Public configuration

Illustrative syntax; the current validator does not accept these new fields.
Keep `staged_widgets` as the single execution switch and add `presentation` as
its versioned, validated configuration resource.

```yaml
features:
  staged_widgets:
    inject_contract: true
  pipeline:
    - skill_retrieval
    - staged_widgets

presentation:
  version: 1
  packs:
    - adkl/tutoring@1
  tools:
    exposure: agent
  requirements:
    missing: report
    recovery_attempts: 0
```

Prospect references `adkl/commerce@1` instead. Both can reference
`adkl/common@1`; dependencies between packs resolve once and name collisions
fail preflight. A pack major version is a schema/behavior contract; the project
runtime lock pins the actual package build. Features and pack changes invalidate
the current worker and console conversation, just like other execution changes.

Requirement modes are explicit: `optional`, `report`, or `require`. No implicit
rule requires a card on every greeting, clarification, or text-only response.
The example reports missing expected cards; it does not promise enforcement.

## Pack contract and ownership

Each ADKL-owned pack contains:

1. Namespaced presentation kinds and versioned semantic payload schemas.
2. Typed tool declarations generated from those schemas, with descriptions,
   defaults, validation, and presentation-only side effects.
3. Presentation roles and default companion copy.
4. Pure semantic-payload-to-primitive converters.
5. Supported action schemas, capability requirements, fixtures, and tests.

Suggested shared types are `greeting`, `fact`, `steps`, `celebration`,
`choice_question`, `lesson`, `navigation`, and `product_list`. Packs group these
by use case without embedding ClawTutor or host-specific business rules. For example,
the tutoring pack can specialize a lesson into the ordered CPA stages, while
pricing calculations never become a commerce presentation responsibility.

Schema validation and converters remain stdlib-compatible. Native ADK tool
construction belongs behind the runtime dependency boundary. Pack discovery is
explicit and local; no HTTP request installs dependencies or imports unrelated
projects. A pack does not automatically authorize its actions.

## Shared execution path

`typed presentation tool / bound domain result -> validate -> stage -> prepare
render plan -> shape companion text -> emit -> serialize -> Flutter primitives`

### Produce semantic data

Two entry points share one staging API:

- **Native presentation tools:** ADKL supplies typed `present_*` tools selected
  by the pack. Attach enabled presentation-only tools at the agent level so
  greeting/fact availability does not depend on retrieval. If a tool is also
  named by a skill, deduplicate or reject the collision before model execution.
- **Declared result bindings:** existing domain tools retain their business
  work, but a binding selects an explicit result field and stages it. Capture
  the original successful result before result trimming, and append contract
  guidance after trimming. Never infer a card from arbitrary JSON or parse
  free-form model prose as the normal emission path.

For migration, a binding can say “successful `render_quiz` result, field `card`,
normalize using the bundled ClawTutor adapter.” This is declarative glue, not a
project converter. That adapter handles legacy field names and explicitly
claims ownership of delivery; direct registration must be removed or routed
through the same staging sink to avoid double emission. Do not deduplicate by
payload content, because identical cards can be intentional.

### Stage and prepare

Use an ordered invocation-scoped queue of presentation envelopes, not a single
slot per card type. Each envelope records schema version, kind, validated
payload, stable presentation ID, source tool-call ID, sequence, role, and
optional action references. Stable IDs distinguish a retry from a second
intentional card. Replace/remove requires an explicit ID.

ADKL manages register keys and lifecycle; projects never name dirty/emitted
keys. Cross-turn replay is off by default. A legacy adapter can preserve an
existing prospect revival policy, with that behavior visible in diagnostics.

Prepare one render plan from current queue state and supported renderer
capabilities. Conversion, gates, and failures are recorded on that plan; both
contract resolution and emission consume it. Recompute after any queue mutation
so the reply contract cannot describe a suppressed or failed conversion.

### Shape, emit, and finish

Inject role-based guidance before the next model response. A presentation tool
normally returns `staged` and permits a companion response; it does not assert
`skip_summarization` merely because data was staged. Legacy tools that skip the
model still use the completion-time companion policy.

At successful completion, finalize the plan and emit each prepared surface once
within the invocation. Respect ADK's state-write requirement for after-agent
widget events. If cards exist but visible text is empty, deliver one configured
caption through the canonical final-reply assembler before text/SSE `done` is
published. Merely storing a caption on an internal result is insufficient.

Cancellation, timeout, or fatal turn failure discards uncommitted presentation
state. This does not roll back domain-tool writes. Delivery guarantees are
invocation-local; there is no claim of exactly-once delivery across network
retries or restarts. JSON and SSE expose the same finalized reply and surfaces.

### Enforce expectations separately from staging

A reusable lifecycle cannot decide whether a lesson was required. Trusted
workflow state or an activated skill's declarative contract can set an
expectation such as `required_kinds: [lesson]`. Expectations must be established
before the response finishes, not inferred from model claims that it taught a
lesson. Activation-based expectations alone do not cover a routing failure;
trace expectation provenance and test routing separately.

In `report` mode, a missing expected card produces a structured diagnostic. In
`require` mode, permit at most one configured presentation repair attempt before
final delivery, using existing validated data and only presentation tools.
Never replay business tools or rerun grading/purchases during repair. If repair
fails, return an explicit presentation failure rather than successful completion
of the required workflow. Text-only turns remain valid when no expectation was
set. This feature is not trusted authorization.

## Conversion and client contract

Resolve standard converters from ADKL packs, independent of project imports.
Retain the current project converter mechanism only as an explicit legacy
extension. Unknown or unavailable converters produce a named failure, not a
successful empty surface.

Compile to the primitive format ADKL currently understands. Preserve the
existing `trace.widgets[].a2ui` envelope during migration so the Flutter client
can reuse `WidgetCard` and `A2uiView`. Do not mix upstream ClawTutor's v0.8 custom
React components with the v0.9-shaped primitive format used here. Treat this as
a supported ADKL protocol subset, not blanket A2UI implementation coverage.

Validate component IDs, roots, references, cycles, supported primitives, payload
sizes, and action schemas before delivery. Converter tests start with real
typed-tool payloads. Unsupported rich presentation semantics must be reported;
flattening a lesson into text does not establish interactive lesson parity.

Maintain distinct capability claims for `display` and `actions`. Display-only
controls remain visibly labeled as previews. Action execution is a separate
delivery increment: allowlisted handlers, opaque server-bound presentation and
question IDs, session binding, expiry, duplicate-submit handling, and structured
results that can continue chat. Never expose generic tool invocation through a
button. Grade quiz answers server-side when that capability is enabled; never
ship a correct answer key as a workaround for missing persistence.

## Migration and delivery increments

1. **Shared display foundation:** extract a builtin registry, typed presentation
   tools, ordered staging queue, shared finalization, and converters. Reuse
   existing role/contracts and gate semantics. Add capability/schema checks.
2. **ClawTutor:** select tutoring pack, replace presentation implementations with
   pack tools or explicit bindings, preserve curriculum and quiz-bank logic,
   declare teaching-flow expectations, and remove its duplicate converters.
   Preserve greeting frequency and multiple-card behavior.
3. **Prospect:** inspect actual specs and map standard navigation/product/data
   presentations to packs. Keep business predicates and legacy builders behind
   an adapter until equivalent mappings are verified. Remove per-project
   presentation machinery incrementally; do not silently discard suppression,
   reranking, or revival behavior to make the YAML shorter.
4. **Interactions:** implement and test action transport and approved handlers
   for quiz submission/lesson continuation. Navigation and customer operations
   retain their own explicit contracts.

Do not rename a feature as a substitute for implementing these increments.
Existing `widgets.adopt` and direct emission remain supported in compatibility
mode with a single delivery owner. Export stays blocked until generated agents
actually wire packs, tools, lifecycle, and conversion. Remote prospect execution
remains subject to the existing remote-worker boundary.

## Acceptance evidence

- A new project references a bundled pack and displays a greeting, steps, and
  choice question with no local presentation Python or converter module.
- Native ADK execution proves tool availability, validated staging, contract
  injection, after-agent event delivery, and companion fallback without a model
  network call. Invalid arguments produce no staged card.
- Two same-kind cards preserve order; retries do not duplicate; later turns,
  Clear, cancellation, and project switching cannot leak stale surfaces.
- Required/optional turns, repair success/failure, conversion failure,
  skip-summarization, and all-suppressed plans have meaningful regressions.
- Real tool payloads preserve greeting fields and quiz labels without duplicate
  nodes. Flutter renders the resulting fixtures inside the chat panel.
- JSON/SSE parity and final/text equality hold with companion fallbacks.
- ClawTutor live greeting, lesson, and quiz turns are checked separately from
  offline tests; prospect domain integration runs in its approved environment.
- Trace reports source, staged count, emitted count, gate/conversion failures,
  requirement status, and actual display/action support. A passing static lint
  does not count as executed behavior.

The first deliverable is reference-only presentation reuse and verified native
card display. Full original-application parity additionally requires domain
state and action integration; those remain explicit acceptance criteria.
