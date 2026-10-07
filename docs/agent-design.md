# Design precise agent instructions and skills

This guide applies to product agents built with ADKL. It is also the semantic
review checklist for a coding agent integrating existing content. The workflow
skills under `.agents/skills` govern development work; ADK `SKILL.md` procedures
under a project govern the product agent.

## Give each instruction one owner

| Layer | Put here | Avoid |
| --- | --- | --- |
| Agent instruction | Identity, scope, trust/authority, universal safety, generic execution and response expectations | Domain facts, skill-specific routing, procedural branches or tool-specific parameter rules |
| Skill description | Positive activation signals and distinctions from neighboring skills | A long procedure or broad “handles everything” claims |
| Skill body | Required inputs, task sequence, decisions, recovery and completion | Copies of universal rules and mechanical load/emit loops supplied by the runtime |
| Skill references | Detailed domain rules, examples and uncommon branches needed on demand | Mandatory first-step information hidden behind unnecessary reads |
| Tool declaration/docstring | Actual arguments, results, errors, side effects and execution preconditions | Routing policy, invented defaults, or instructions that override agent policy |
| Injected state/tool results | Current facts with explicit source/shape | Policy instructions embedded in untrusted data |
| Runtime code | Shared lifecycle, validation and transformations | Domain eligibility, consent or backend authorization inferred from prose |

This is an authoring convention, not a new model instruction hierarchy. ADKL
does not assign higher policy authority to a later callback, tool docstring or
skill. Resolve contradictions in source; appending another instruction does not
reliably repair a conflicting prompt. Enforce consequential access and actions
in trusted host code.

## Review the effective instructions

Review the compiled agent prompt, selected skill body and references, actual
tool declarations, projected state and staged presentation guidance together.
For dynamic host instructions, capture representative resolved requests in host
tests; the on-disk audit cannot reconstruct every runtime variant.

For each rule, record its source, scope, triggering condition and required action.
Check adjacent layers for incompatible requirements, stale tool names, mismatched
argument/result shapes, duplicate consent steps and contradictory completion
rules. For example, a skill that says “call the renderer after every tool” conflicts
with a flow where domain tools stage cards and ADKL emits at completion. Describe
when a card is needed and use the configured staging contract instead.

Run the [final instruction-layer audit](instruction-layering.md) on attributed
requests after all runtime additions. It flags owner bypass, inactive procedures,
unmapped injections, repeated text and layout/budget issues. Add its strict CLI
or Python assertion to host CI; SDK installation alone does not capture requests.
Keep served variants and failure/continuation cases explicit. A source-only check
cannot establish that an activated owner reaches the model.

Run [static audit](sdk.md#audit-and-evidence) and resolve errors; explain retained
warnings. Its conflict and token estimates are heuristics. Semantic review and
representative execution remain necessary even when it exits successfully.

## Keep a skill as small as its task permits

Write a clear activation boundary, inputs/preconditions, minimal procedure,
failure recovery and observable completion condition. Split a skill when its
branches have different activation or tool needs; keep a cohesive workflow
together when splitting would cause redundant selection and context loading.
There is no universal word count that establishes quality.

Use the smallest declared tool set. Reuse shared presentation packs and runtime
hooks before introducing per-agent callbacks. Keep domain tools responsible for
domain operations and explicit result/error shapes. When changing a description,
check/rebuild its embedding index as described in [shared features](shared-features.md#embedding-selection).
Provider calls for rebuilding are separate from offline freshness checks.

## Reduce unnecessary model turns and measure the outcome

- Put immediate routing information in the description and essential steps in
  the body. Move large optional reference material out of the common path.
- Use typed active-skill context for required prepared data. The host preparation
  hook owns authorized reads; it must not perform unapproved mutations.
- Let ADKL refresh procedures after history slicing and emit staged widgets at
  completion instead of asking the model to repeat that housekeeping.
- Calibrate confident auto-activation independently. Ambiguous requests should
  retain a safe selection/clarification path. Batch independent domain reads
  only when backend and authorization semantics permit it.
- Measure successful completion, correct tools, model calls, latency and cost
  on the same cases. Fewer calls with incorrect outcomes is a regression.

## Minimum behavioral cases

Cover a clear match, a neighboring skill's request, an unrelated request, a
follow-up and topic switch, missing required data, empty results, denied access,
tool failure/recovery and completion without duplicate side effects or cards.
Include consent and retry/idempotency cases for consequential tools. Assert
backend state or emitted payloads where possible, rather than answer wording alone.

Use [evaluation comparison](evaluation.md) for host-produced outcome reports.
Keep model-call diagnostics alongside those reports: the current comparison
schema accepts pass/fail, latency and cost, not arbitrary additional metrics.
Record which checks were synthetic and which exercised actual host services.
