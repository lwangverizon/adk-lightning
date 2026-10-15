# Capability inference-call budgets

Doctor reads optional `doctor-calls.json` and retained host observations. It never
runs an evaluator, model, tool or production query. The Markdown brief always
shows **PRODUCTION CAPABILITIES — LLM CALL BUDGET**, including an explicit unknown
state when unconfigured. This is a CLI/JSON diagnostic surface; the static portal
and Flutter console do not yet display the new coverage table.

## What the budget means

Default **3 inference attempts per customer invocation**, WARN at **>3**. This is
an advisory engineering budget, not an industry standard or runtime cutoff.
Do not set ADK's execution cap to three or remove consent, authentication,
grounding or recovery to silence a warning. Four observed calls do not prove a
capability *requires* four calls. `TURN_COST_HIGH` remains a separate token-size
estimate.

Count every actual model request, including nested agents, LLM-backed tools,
classifiers and observable retries/fallbacks. Count one streaming request once,
not per chunk or tool call. Exclude embeddings, deterministic tools, user messages
and evaluator/simulator calls. Record logical IDs to distinguish retries from new
reasoning steps. Hidden provider retries remain an instrumentation limitation.
A measured deterministic invocation can have zero calls; missing evidence cannot.

The host owns complete instrumentation, scenario identity, outcome grading and
inventory coverage. Inclusive owner rows must not be summed across an invocation.
Host assertions are not signed execution proof or verification of deployed flags.

## Configuration

```json
{
  "version": 1,
  "catalog": "prod",
  "coverage": "partial",
  "budget": 3,
  "max_age_hours": 168,
  "inputs": ["agent.py", "agent.yaml", "execution.yaml", "uv.lock"],
  "capabilities": [{
    "id": "customer.device-upgrade",
    "kind": "workflow",
    "surface": "web",
    "variant": "prod-default",
    "scenarios": ["happy", "clarification", "recovery"],
    "evidence": "evidence/device-calls.json"
  }]
}
```

`budget` and `max_age_hours` are optional with defaults shown. Other keys are
required. `catalog` is prod/uat/test. `coverage` is host-declared partial/complete;
Doctor cannot infer fleet-wide completeness. Kinds are skill/workflow/specialist/
tool/orchestrator. IDs, surfaces, variants and scenarios are bounded identifiers,
not customer text. IDs must be unique across surfaces and ownership boundaries.
Set `evidence` to null for each not-yet-measured capability. Unknown inventory or
scenarios must stay visible instead of being dropped to make a report green.

Declare all behavior, configuration, evaluator and dependency inputs relevant to
these measurements. `input_digest(root, inputs)` hashes their bytes; paths must
be regular project files, not credentials, symlinks or the evidence itself.
Inputs omitted by the host cannot be covered by freshness claims.

## Evidence schema

```json
{
  "version": 1,
  "capability": "customer.device-upgrade",
  "configuration": "prod-default",
  "source_digest": "<64-character SHA-256 from input_digest>",
  "recorded_at": "2026-10-10T12:00:00+00:00",
  "evidence": "MEASURED_EVAL",
  "observations": [{
    "id": "happy-trial-1",
    "scenario": "happy",
    "passed": true,
    "attempts": [
      {"id": "request-1", "logical_id": "step-1", "owner": "parent"},
      {"id": "request-2", "logical_id": "step-2", "owner": "device-workflow"}
    ]
  }]
}
```

Evidence classes: SCRIPTED_REPLAY, MEASURED_EVAL, MEASURED_PRODUCTION. Static graph
node counts are not accepted as model-call evidence. Duplicate invocation or
attempt IDs, unrecognized scenarios, malformed counts/labels, future timestamps,
unknown fields and unsafe paths fail the check without echoing input contents.
Only numeric counts and bounded identifiers are needed, never prompts or sessions.
Each row is a **complete invocation**, not a partial stream or truncated trace.
Instrumentation adapters remain host-owned; Doctor validates shape, not capture
truth. The host must use stable case IDs across baseline/candidate evaluations.

Freshness states: NOT_MEASURED, PARTIAL, STALE, CURRENT. A changed source digest,
variant or expired timestamp is stale; missing expected scenarios is partial.
CURRENT means matching declared inputs, age and scenario coverage, not live Prod
attestation. One observation per scenario is not a robust statistical evaluation.
The report provides sample size, scenario coverage, median, maximum, exceedance
count, success rate and retry attempts in JSON. p95 is omitted below 20 samples;
20 is a reporting minimum, not a statistical confidence guarantee.

## Recording at a host model boundary

`adkl.inference_calls.InvocationCalls` is an opt-in recorder, not automatically
installed runtime instrumentation. Create one per invocation, share it with nested
model adapters, call `begin(logical_id=..., owner=...)` once per actual request,
and call `end(attempt_id)` in a `finally` block after consuming the stream. Call
`finish(passed=host_outcome)` after all requests settle. Unfinished requests are
rejected; failures still count. Independent parallel calls have distinct logical
IDs; retries reuse the logical ID with a new attempt ID. Do not also instrument
provider and framework boundaries into the same counter.

`evidence(root, inputs, capability=..., configuration=..., evidence_class=...,
observations=..., started_digest=...)` creates a numeric receipt for a newly
completed run. Obtain `started_digest` with `input_digest` before running; source
changes during capture are rejected. It does not prove that every model path was
instrumented. Imported production traces must retain their original capture time
and identity rather than using this helper to stamp historical data as current.

## Findings and comparison

- `CAPABILITY_LLM_CALLS_OVER_BUDGET`: any observed invocation exceeds the budget,
  including unsuccessful/recovery invocations. Grouped by capability.
- `CAPABILITY_LLM_CALLS_UNMEASURED`: absent evidence or missing scenarios.
- `CAPABILITY_LLM_CALLS_EVIDENCE_STALE`: source/configuration/age mismatch.
- `CAPABILITY_LLM_COVERAGE_PARTIAL`: inventory does not claim complete coverage.
- `CAPABILITY_LLM_CALLS_REGRESSION`: median increases against `--baseline` on
  matching case IDs/scenarios, sample counts, configuration, evidence class and
  budget. Applies to scripted/evaluated cases, not unpaired production traffic.

All are WARN; they do not enforce a runtime limit. No first-class per-capability
exception mechanism is implemented yet. Budget changes alter comparison coverage
and cannot silently resolve findings. Partial/stale/absent evidence also cannot
resolve earlier findings. Review the baseline and outcome distribution before
accepting an increase. A warning being absent does not certify performance.

## Host rollout

1. Enumerate every enabled production surface/variant and capability kind.
2. Start unknown; add repeated isolated happy, clarification/consent, recovery
   and mixed-intent observations with actual model-boundary instrumentation.
3. Compare outcomes, latency, tokens and costs alongside counts using the host's
   existing [verification](verification.md). This feature does not yet join those
   other metrics into the call table or compute serial critical-path depth.
4. Add separately authorized read-only production aggregates; keep unknown
   gateway retry coverage explicit. Never export customer sessions for this gate.

References informing this advisory design:
[Google ADK efficiency metrics](https://google.github.io/adk-docs/evaluate/criteria/#inference_call_count_v1),
[Anthropic agent patterns](https://www.anthropic.com/engineering/building-effective-agents),
and [agent evaluation practices](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
ADK's informational efficiency metrics do not accept thresholds; this policy is
Doctor-owned. Confirm your installed ADK version before relying on newer metrics.
