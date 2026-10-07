# Reusable agent features

ADKL owns execution mechanisms; projects own agent content and domain behavior.

To maintain or extend these mechanisms, use the
[feature maintenance guide](feature-maintenance.md) for the module map and steps.

| ADKL | Project |
| --- | --- |
| Embedding calls, cosine ranking with lexical backfill, native skill loading | Skill descriptions, references, vector index and ranking policy |
| Typed display tools, staging, companion contract, event transport | When the agent should show a card; domain data |
| Semantic JSON validation and A2UI primitive conversion | Quiz selection/persistence, curriculum, offers, eligibility and other domain tools |

## Unified Capability Selection (Python SDK)

[Unified Capability Selection](capability-bindings.md) provides one discovery
surface across native skills, workflows and specialist agents. `CapabilityBindings`
composes the catalog and lifecycle; optional `CapabilityRetrieval` uses host ranking
to shortlist all kinds together before the parent model chooses an action. Native
ADK executes the target, with separate host eligibility and dispatch authorization.

This is not an `agent.yaml` feature. Its host-owned ranking and explicit skill
loading differ from the skill-only retrieval below; see the
[selection-path comparison](capability-bindings.md#choose-a-selection-path).

## Opt in through agent.yaml

Existing native constructors can use the same presentation packs through
`RuntimeBindings(presentation=...)`; see [native staging](runtime-bindings.md#staged-presentation-and-delivery).
`presentation.exclude_kinds` removes selected pack tools while retaining generic
console preview support for domain-owned cards of those kinds. Domain validation,
persistence and private-field filtering remain the application's responsibility.

```yaml
features:
  skill_retrieval:
    vectors: embeddings/skills.json
    embedding_model: gemini-embedding-001
    dimensions: 0
    top_k: 4
    total_cap: 5
    min_score: 0.15
    lexical: true
    auto_activate: false
  staged_widgets:
    inject_contract: true
  pipeline: [skill_retrieval, staged_widgets]

presentation:
  version: 1
  packs: [adkl/tutoring@1]
  bindings:
    render_quiz:
      result_field: card
      kind: quiz
```

The agent still declares its provider/model, instructions, skills and domain
tool catalog normally. Omit `bindings` if using only built-in display tools.
No project callback, converter, matcher subclass or widget spec is required.

Available packs:

- `adkl/common@1`: greeting, fact, steps and celebration.
- `adkl/tutoring@1`: common plus quiz and concrete/pictorial/abstract lessons.
- `adkl/commerce@1`: common plus navigation and product-list previews.

ADKL generates pack tool declarations for the console and skill lint without
importing ADK or project code. Project catalogs contain domain tools only;
conflicting pack tool names are errors. Skills may reference shared display
tool names such as `render_steps` without implementing them locally.

Once display tools are available, skill lint and
[doctor](doctor.md#widget-output-coverage) report a skill that binds none of them
as text-only output. Record a skill whose turns are deliberately bare text in its
frontmatter, so the choice is data rather than an omission:

```yaml
---
name: escalate
description: Hand a stalled conversation to a human agent.
metadata:
  adkl_presentation: text_only
---
```

`text_only` is the only supported value; anything else is an error rather than an
opt-out. The `metadata:` nesting is required: ADK validates top-level frontmatter
keys against a closed set, while `metadata` is free-form and unrecognized keys in
it are ignored, so the annotation does not affect skill loading.

## Result binding contract

A bound domain function returns `{"status": "success", "card": {"type": "quiz", ...}}`.
Legacy `status: rendered` is accepted too. Errors do not stage a card. The
configured field must match the enabled kind. ADKL validates and copies the
payload, strips quiz answer keys, stages it and returns a companion-text hint.
Keep confidential data out of presentation payloads and tool responses.

Pure display tools are agent-level. Bound domain tools remain skill-scoped:
retrieval chooses candidate skills and native ADK loading exposes their
declared tools, either through a confident pre-model activation or `load_skill`. This is not authorization for domain side effects; projects must
still enforce identity, consent and write policy inside domain code.

Delivery is semantic JSON → validated invocation queue → staged-widget contract
→ native UiWidget event → A2UI primitives → existing Flutter chat renderer.
The queue preserves order, deduplicates function-call IDs and is invocation-scoped.
Limits are 16 cards per invocation and 64 KiB per payload. ADKL provides fallback
companion text when the completed turn emits widgets but has no text response.

## Embedding selection

For the YAML-configured **skill-only** path, `features.skill_retrieval` is the
selection policy. It does not configure Unified Capability Selection. Project
vectors are data, not an implementation of selection. Build them with
`python -m adkl.embed projects/PROJECT/agent.yaml`; this calls the configured
embedding provider. `--check` is offline and checks source freshness. Queries use
`RETRIEVAL_QUERY`; skill descriptions use `RETRIEVAL_DOCUMENT`.

Selection supports cosine ranking, optional lexical backfill, candidate caps,
excluded skills, explicit session-state pins and optional active-skill
continuation for ambiguous follow-ups. Confident auto-activation uses
native skill loading before the model; ambiguity is measured before candidate
truncation. SDK and preview share this path. Pins can exceed the candidate cap
but cannot reintroduce excluded or unconfigured skills. Embedding failure exposes
eligible candidates without auto-activation. Optional rolling-query history widens
reachability without changing scores; the separate weighted-history helper leaves
ranking policy to the host. Trusted explicit route anchors are supported through
`route_skill`; see [runtime bindings](runtime-bindings.md).

## Generic result sizing

[`tool_result_trimming`](tool-results.md) is an opt-in JSON size guard. Oversized
results become bounded, explicitly incomplete previews rather than guessed row
or field subsets. Application teams own detailed reduction and safe recovery;
TOON encoding is a separate, application-evaluated optimization, not a size limit.

## Limits and migration

This is display-only, not complete upstream UI parity. Quiz and navigation
buttons do not submit actions, grade answers or navigate. A configured pack
does not force every reply to contain a widget. Required-widget expectations,
repair/retry policy and agent-side interactive action dispatch remain
unimplemented. Generated-code export rejects this configuration. SDK-backed
[project bundles](project-onboarding.md#bundle-and-deployment-boundary) support
native ADK serving with the installed runtime; host/client behavior still needs
end-to-end verification.

The [staged-ui example](../projects/staged-ui/README.md) uses the shared common
pack. The [native-agent example](../projects/native-agent/README.md) stages
explicit A2UI operations through native runtime bindings. Applications and
their domain implementations remain in their own repositories.

Offline native tests exercise a new project with both features, actual ADK
staging/emission, native skill-scoped quiz access and second-turn isolation.
Live model behavior, customer integrations and interactive quiz grading are
separate evidence, not established by those tests.


## Shared SDK and state

The console and `adkl.agent_runtime.build_agent` use the same native callbacks
and selected-skill toolset. See [SDK contracts](sdk.md) for skill context schemas,
history behavior, audit output, error handling and the standalone Runner example.
