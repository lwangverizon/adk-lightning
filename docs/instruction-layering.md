# Audit the final instruction layers

Use this guide when changing a system prompt, tool docstring, skill, reference,
workflow or callback that adds model-visible instructions. The aim is **one clear
owner per behavior**, not the shortest possible prompt. Neither a clean audit nor
fewer tokens establishes a fully optimized agent.

## Ownership contract

| Layer | Owns | Must not become |
| --- | --- | --- |
| System kernel | Identity, scope, trust/authority, universal safety, generic execution and response standards | A catalog of domain procedures, product facts or skill-specific routing |
| Capability description | What the capability does and how it differs from neighbors | A second procedure or a duplicate of the full tool docstring |
| Tool docstring/schema | Identity, arguments, results, side effects, intrinsic preconditions and failure contract | Cross-tool choreography, domain routing or an override of system policy |
| Active skill/workflow | Task decisions, clarification gate, evidence sources, sequence, recovery and completion | Ambient instructions on unrelated turns |
| Reference | Optional/rare or extensive knowledge used at a specific step | An extra round trip for every task's essential first step |
| Session/tool data | Current facts and explicit uncertainty | Instructions hidden in arbitrary data or an authorization grant |
| Presentation contract | Current surface-specific delivery and text/UI relationship | Global tool selection or domain policy |
| Trusted code | Authorization, consent validation, idempotency, state/number invariants | A model's interpretation of prose as permission |

A loaded skill may be transported inside the system instruction. That is **not**
itself a layering violation. The audit records the logical owner independently of
transport position. A callback-owned domain workflow is still a workflow even if
its source file is called `callbacks.py`; move ownership into the active domain
execution path, not merely into a renamed global constant.

## Write instructions that are easy to execute

For each task, present the small decision first:

1. **Entry:** when this branch applies; distinguish adjacent intents.
2. **Clarify:** what missing fact materially changes the next action; ask and stop.
3. **Read:** minimum authorized evidence; batch independent reads after the gate.
4. **Decide:** distinguish supported, unsupported and unknown findings.
5. **Complete or recover:** exact stop condition; missing evidence is not denial.

Use bounded examples for observed mistakes, not an exhaustive phrase router.
Do not let later “always search” or “always enroll” steps override a branch that
must clarify and wait. An active cart, current page or loaded skill is context,
not proof of the customer's intent or consent. A public search tool exposed before
its owner loads can bypass every prerequisite in that owner's prose. Fix the
exposure/activation contract rather than copying the domain gate into the kernel.

When moving text, verify the **served variant**, not only the base SKILL.md. A
shorter prompt that points to an unloaded reference is not an improvement. Keep
static rules, dynamic state and trusted controls distinct; do not delete a safety
boundary to meet a size budget.

## API: inspect the actual outgoing request

`adkl.instruction_audit.audit_request` accepts final system text, outgoing tool
**descriptions**, exact `InstructionSpan`s, the currently loaded/prepared owners,
and explicit `required_owners` for gated tools. It does not classify arbitrary
natural language as domain policy. It never mutates the request, hides tools,
rewrites policy, calls a model or executes business operations.

`inspect_request(request, **kwargs)` reads public fields on an ADK `LlmRequest`:
`config.system_instruction` and `config.tools[].function_declarations`. A
`tools_dict` dispatch-only denial does not count as an exposed tool. Unsupported
non-text instructions, built-in tool shapes or duplicate declarations raise an
error rather than return a misleading clean report. Schemas/history are not
included in this audit's byte metrics; use [request costs](doctor-boundaries.md)
for full request measurements.

The host provides spans with:

- `surface`: `system` or `tool:<outgoing_name>`;
- `start`, `end`: Python character offsets `[start, end)`, not bytes/tokens;
- `layer`: `system`, `skill`, `workflow`, `reference`, `tool`, `state`, `presentation`;
- `scope`: `universal`, `procedure`, `interface`, `data`, `presentation`;
- `owner`: bounded non-sensitive source/owner identifier;
- `dynamic`: whether this portion varies per request.

Every non-whitespace character must be attributed. Overlapping or invalid spans
raise errors. This catches unknown late additions but cannot detect a host that
incorrectly labels all text universal. Attribute at construction, then validate
against the exact final bytes. Do not search-and-replace arbitrary prompt content
to make span offsets or audit outcomes pass.

Capture **after** skill/tool preparation, all before-model transforms and final
capability catalog injection, ideally in the model adapter immediately before
provider conversion. No later mutation may be omitted. `boundary="adk_request"`
is not proof of the provider wire request; use `provider_request` only when the
host actually captures that boundary. `phase` is `cold`, `followup`,
`topic_switch` or `recovery`.

See the runnable, credential-free [example](../examples/instruction_layer_audit.py):

```bash
uv run --no-sync python -m examples.instruction_layer_audit
```

## Findings and CI gate

| Rule | Meaning |
| --- | --- |
| `PROMPT_SURFACE_CONFLICT` | Existing narrow literal formatting conflict among universal system spans; conditional/scoped semantic conflicts need review |
| `INSTRUCTION_PROCEDURE_WRONG_LAYER` | Host attributed a domain procedure to the kernel/tool interface |
| `INSTRUCTION_OWNER_INACTIVE` | Skill/workflow/reference text is present without its owner active |
| `INSTRUCTION_TOOL_OWNER_BYPASS` | Gated outgoing tool has no acceptable owner active |
| `INSTRUCTION_PROVENANCE_INCOMPLETE` | Final text is missing attribution, possibly a late injection |
| `INSTRUCTION_STATIC_AFTER_DYNAMIC` | Static instructions follow dynamic context; terminal presentation is excepted |
| `INSTRUCTION_TEXT_DUPLICATED` | Exact long instruction sentences repeat within/across attributed blocks |
| `INSTRUCTION_TOOL_LAYER_MISMATCH` | Tool description carries content attributed to another layer |
| `INSTRUCTION_STATE_POLICY` | State is attributed as policy rather than data |
| `INSTRUCTION_SYSTEM_BUDGET`, `INSTRUCTION_TOOL_BUDGET` | Host-specified UTF-8 byte ceiling exceeded |

Findings include bounded identifiers and counts, never source excerpts or customer
text. Identifiers are host-supplied: use synthetic labels, not account IDs. Repeated
sentences are INFO; other findings are WARN. Safety repetition needs review, not
mechanical deletion. Budget thresholds are explicit host choices, not universal
Gemini limits. Static-after-dynamic is a cache-layout review hint, not a measured
cache-miss claim.

For local synthetic JSON input matching `audit_request` keyword arguments (spans
as objects), run:

```bash
uv run --no-sync python -m adkl.instruction_audit fixture.json --fail-on warning
```

Exit codes: 0 no threshold findings; 1 findings; 2 invalid/incomplete capture.
Use `--fail-on info` to gate duplicates too. In Python tests assert `passed` and
optionally `not report["findings"]`. The CLI is the strict CI gate; Doctor retains
its existing WARN-advisory exit semantics. Neither silently auto-fixes anything.

## Doctor integration

Doctor checks an optional `doctor-instructions.json` in the host project:

```json
{
  "version": 1,
  "captures": [
    {"id": "web-cold", "phase": "cold", "source": "fixtures/web-cold.json"},
    {"id": "uat-followup", "phase": "followup", "source": "fixtures/uat-followup.json"}
  ]
}
```

Each file is an `audit_request` input as above. Inputs must be local non-secret
JSON files, with no symlinks/traversal and at most 2 MiB each. Missing or invalid
expected files fail the check. With no configuration, `instruction_layers` is
**not_checked**, not passed. Doctor never imports a host or recaptures a request
for this check. Results are labeled **saved_request_fixture**, not runtime evidence.
The manifest must enumerate the intended surface/cohort/phase matrix; Doctor cannot
infer missing cases from undeclared environments.

Editing/removing a capture does not establish that runtime behavior was fixed.
Comparison coverage hashes include captures and attribution, so such changes mark
old absent findings **notVerified**, not resolved. This deliberately conservative
behavior avoids treating fixture edits or narrower coverage as optimization proof.
Run the host's actual capture tests for fresh evidence.

## Acceptance for an instruction change

- Inspect every served variant at cold start, after activation, short follow-up,
  topic switch and failure/recovery; include a direct-tool/voice path if served.
- Assert clarification precedes investigation; no question repeated for explicit
  intent; a bare “yes” does not resolve alternatives or grant action consent.
- Keep real prefetch/callback ordering in isolated scripted tests. Intercept
  external effects at the backend boundary, not by deleting the owning callback.
- Test current-result failure shapes, not a generic fake envelope that discards
  workflow-owned recovery guidance. Unknown eligibility must remain unknown.
- Repeat model cases and non-domain neighbors. Record model/version, thinking,
  fallback behavior, prompt/tool bytes, input/cached tokens, calls, latency,
  supported claims and consent outcomes. Compare like-for-like samples.
- For Gemini 3.6, test reasoning levels on the same fixtures; do not assume higher
  thinking repairs ownership, or that “3.6 optimized” describes a mixed-model fleet.

A clean structural audit is necessary evidence, **not full optimization, semantic
consistency, production readiness or authorization enforcement**. Host CI adoption
and a pinned SDK update are required before these checks flag an external
application's future changes automatically.
