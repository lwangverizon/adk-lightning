# Doctor boundary contracts and request measurements

Doctor can review three additional kinds of evidence without importing a host or
executing a tool: declared output-field consumers, retained boundary-contract
results, and retained assembled-request counts. These extend the existing
[Doctor report](doctor.md) and [host verification workflow](verification.md), not
the Runtime's admission or authorization behavior.

## Boundary contracts

`verification.yaml` accepts three additional built-in contract names:

```yaml
contracts: [consent_binding, outcome_consistency, handoff_continuity]
```

Tag suite cases with the relevant names in `adkl_contracts`. The existing worker,
input/profile identities, complete case/repetition checks, and Doctor freshness
rules apply. Old configurations need no migration. Add cases before recording a
new baseline; changing tags invalidates earlier coverage. A case boolean is
host-reported evidence, not automatic instrumentation. Separate scenarios/tags
when their failures need independent attribution.

The lightweight functions in `adkl.contract_probes` return booleans:

| Helper | Required host observations |
| --- | --- |
| `consent_binding(*, checks, effects, expected_effects)` | Exactly six boolean predicates at the write boundary: `affirmative`, `account_matches`, `target_matches`, `fresh`, `next_user_turn`, `not_consumed`; ordered logical effect IDs at the fake backend. All predicates true requires nonempty expected effects; any false requires none. Duplicate unexpected writes fail. |
| `outcome_consistency(observed, *, expected, observers)` | A mapping of every explicitly required observer to a normalized terminal status: `success`, `empty`, `suppressed`, `error`, `timeout`, or `cancelled`. All observers agreeing on the wrong outcome still fails. |
| `handoff_continuity(*, observed, expected)` | Exact `session`, `invocation`, `query_digest`, `handoff_used` and unordered unique `tools` observations at discovery time. A corrected final prompt cannot replace the earlier discovery observation. |

Use synthetic identities and digests, not customer data. Include affirmative,
decline/mixed intent, account/target change, expiry, replay and concurrent-write
cases for consent. Include legitimate empty/suppressed outcomes, backend failure,
operation/total deadline and cancellation for outcomes. Include short follow-ups,
explicit topic changes, session switches, eviction/return and absent standalone
tools for handoff. These are scenario recommendations, not automatically generated
coverage; tagging one happy-path case does not establish the entire matrix.

The [native regression fixtures](../tests/runtime/test_boundary_probes_native.py)
exercise real ADK Runner tools, callbacks and discovery with synthetic models.
Deliberately faulty hosts demonstrate that checks fail. Their normalized timeout
and cancellation envelopes do not establish real scheduler cancellation safety.

**Not security enforcement:** predicates are supplied by trusted instrumentation;
the helper does not infer consent from text, authenticate account ownership,
implement a financial gate, prove UI display, or supply distributed idempotency.
The host/backend must enforce those requirements independently. Never run these
fault fixtures against real mutation backends.

## Incident-derived probes

Recent host fixes exposed a common gap: a valid declaration does not prove that
its next consumer, another execution path, or a later turn behaves correctly.
Three additional `adkl.contract_probes` helpers use the existing retained
verification workflow. They require explicit observations; Doctor never executes
producers, consumers, retrieval or sinks to collect them.

| Helper | Observations and failure detected |
| --- | --- |
| `result_integrity(*, observed, expected)` | Each mapping contains exactly `status`, `cached`, `fallback`. Status is `present`, `empty`, `unknown`, `partial` or `error`; the other values are booleans. Compare actual consumer classification, cache write and fallback action against independently authored expectations. Detect metadata mistaken for a malformed item, or unavailable data collapsed into empty success. |
| `capability_reachability(*, catalog, indexed, exposed, dispatchable, required, references)` | Unique nonempty identifiers in each inventory, with a nonempty required set. Every required capability must survive all four boundaries; every resolved reference target must be exposed. Detect missing index entries, vanished follow-up capabilities and obsolete owner references. Extra dispatch-only denial handlers are allowed. |
| `delivery_consistency(*, staged, accepted, claimed, expected)` | Unique logical IDs captured independently at stage, sink acceptance and completion-claim boundaries. Accepted items must have been staged, and ordered acceptances and claims must equal the host expectation. Failed-sink scenarios permit empty acceptance/claims, but an entirely unobserved scenario fails. Sink acceptance does not imply browser rendering or human queue admission. |

Names in `contracts` and case `adkl_contracts` are `result_integrity`,
`capability_reachability` and `delivery_consistency`. Use the
[required-evidence gate](doctor.md#required-evidence-gate) when absent or stale
coverage must fail the host's CI check. Static `doctor-tools.json` contracts
remain useful but cannot substitute for running the real consumer.

Run the credential-free [broken/fixed example](../examples/incident_contracts.py):

```bash
uv run --no-sync python -m examples.incident_contracts
```

It detects four intentionally faulty toy host cases (metadata classification,
unknown versus empty, follow-up ownership, failed delivery) and passes their
corrected versions. These are generalized synthetic reproductions, not replays
of historical application commits. [Native boundary tests](../tests/runtime/test_incident_probes_native.py)
also observe actual ADK tool responses, callbacks, outgoing declarations and
completion with a scripted model. The sink remains synthetic, not a frontend.

### Turn a fix into lasting evidence

For each incident record the failure, violated invariant, affected variants,
regression fixture and verification receipt. First show that the fixture fails
against the broken implementation and passes after correction, with legitimate
negative controls. Run the actual producer/parser/consumer, not a hardcoded
success observation. Keep business expectations in host fixtures, never inferred
from product names or natural-language heuristics in the SDK.

Recommended host matrix:

- Result consumers: metadata-bearing success, empty success, missing/null,
  partial failure, redirect and malformed envelope; independently inspect cache
  writes, fallback actions and downstream conclusions.
- Reachability: cold/follow-up/topic-switch turns, skill/workflow cohort arms,
  intended surface/environment variants, missing index and stale aliases.
  Use stable capability identities across boundaries; normalize loader names
  and workflow aliases in host instrumentation.
- Delivery: available/unavailable, success/failure/cancellation at the actual
  sink, stale invocation and duplicate emitter attempts. Use `state_isolation`
  and `single_emitter` alongside delivery checks where appropriate.
- Stale dispatch and refresh: existing `rejected_call_recovery`, `consent_binding`
  and `state_isolation` probes with fake backend effects and persisted state.
- Concurrency: host-owned two-run/session tests and fault injection. These three
  new helpers do not implement scheduler coordination or distributed idempotency.

One tagged case does not certify this entire matrix. Tags and host observations
are trusted claims, not automatic instrumentation or semantic validation.
Reachability is not activation, authorization, vector freshness or retrieval
quality; use host index validators and behavior evaluations as well. Instruction
layering, model false positives, latency and real frontend rendering retain their
own checks. No automatic prompt rewrite, tool exposure change, SDK pin update in
consuming repositories or production mutation is performed.

## Explicit tool-output consumers

Place optional `doctor-tools.json` next to `agent.yaml`:

```json
{
  "version": 1,
  "tools": [{
    "name": "offers",
    "version": "v4",
    "fields": {
      "/options/*/tier": "string",
      "/options/*/delta": "number"
    },
    "fixtures": ["fixtures/offers.json"],
    "consumers": [{
      "name": "formatter",
      "source": "format_offers.py",
      "version": "v4",
      "fields": {"/options/*/tier": "string"}
    }]
  }]
}
```

The referenced files must actually exist. A matching synthetic fixture is:

```json
{"options": [{"tier": "less", "delta": -5}]}
```

This is a deliberately small **field contract, not JSON Schema**. Paths are
slash-separated object keys; `*` traverses every array item. Keys contain ASCII
letters/digits, underscore or hyphen. Types are `string`, `number`, `integer`,
`boolean`, `object`, `array`, or `null`. Types match exactly between declarations;
no coercion/subtyping is inferred. Booleans are not numbers. All declared paths
must exist in every fixture and match their declared type. An empty array does
not establish wildcard field evidence; provide a representative nonempty fixture.
Use separate contracts for alternative output shapes; optional fields/unions,
references, arbitrary JSON Schema and prose parsing are not supported.

Each tool needs a unique name, bounded version identifier, at least one fixture
and one uniquely named consumer. Consumers may declare only the fields they use.
Additional fixture fields are allowed. Contract/fixture JSON rejects duplicate
keys, must be at most 2 MiB per file, and may not traverse symlinks. Inputs stay
inside eligible project source, excluding credential files and private/generated
paths under the existing verification rules. Fixtures must be synthetic: Doctor
reports field paths/types, not fixture values, but files remain on disk.

Doctor produces `TOOL_OUTPUT_VERSION_MISMATCH`,
`TOOL_OUTPUT_CONSUMER_MISMATCH`, or `TOOL_OUTPUT_FIXTURE_MISMATCH` errors. Invalid
configuration is an incomplete check (exit 2), not a successful empty inventory.
Missing configuration is `not_checked`. The additive `toolOutputContracts`
summary and Markdown brief list consumers for coordinated review. Consumer source
paths identify ownership, not proof their implementation follows the declaration.

Fixture corrections with the same inventory/contract can resolve findings.
Removing a consumer/field/fixture or changing declarations conservatively changes
coverage; absent findings become `notVerified` and need a new baseline. Doctor
never executes consumers, discovers undeclared dependencies, or certifies runtime
compatibility. Do not bump versions solely to silence a warning.

## Retained assembled-request measurements

Opt in through the existing host evaluator:

```yaml
request_costs: true
```

Each evaluator observation still returns `passed`, `latency_ms`, and `cost_usd`.
It additionally returns `request_costs`, a nonempty ordered list of captures:

```json
{
  "request": 1,
  "phase": "cold",
  "boundary": "adk_request",
  "tokenizer_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "bytes": {"system": 100, "tools": 200, "history": 30, "other": 10, "total": 340},
  "proxy_tokens": {"system": 25, "tools": 50, "history": 8, "other": 3, "total": 84}
}
```

The digest above is illustrative. Supply the actual tokenizer/algorithm identity.
ADKL does not tokenize, download a tokenizer, or infer billing. Counts are integers
from 0 to 10^12. There must be 1–1000 captures per case/repetition, numbered from 1
without gaps. Unknown fields (including raw prompts) are rejected. Byte partitions
must be disjoint and sum to total; independently tokenized partitions need not sum.
Gateway hoisting moves content between partitions; it is not automatically savings.

Phases: `cold`, `followup`, `topic_switch`, `recovery`. Boundaries: `adk_request`,
`provider_request`. An ADK capture is not the provider's serialized wire request.
Keep the capture serializer/tokenizer in `evaluator_files`; include settings and
retrieval indexes in `subject_files`. Use an explicit verification profile for each
surface/environment. No cross-profile parity or matrix completeness is inferred.
An opted-in run requires captures for every case/repetition, including failed
outcomes; missing data cannot silently disappear from the denominator. These rules
cannot detect instrumentation that omitted an actual model request while falsely
reporting a shorter contiguous sequence.

Captures are stored alongside, not inside, the unchanged evaluation v1 report.
Doctor validates them and exposes numeric aggregates at
`verificationEvidence.requestCosts`, grouped by boundary, phase and tokenizer.
Each group reports count and per-partition maxima. Maxima may originate in different
requests: never add them to reconstruct a single request. The Markdown fix brief
shows the same summary. The existing Console API/export carries it; a dedicated
interactive cost panel is not included in this increment.

Only current completed runs expose aggregates. A baseline is labelled
`baseline_recorded`, a regressed candidate remains `regressed`, and stale/profile-
mismatched/failed evidence exposes no old counts as current. Removal or disabling
of measurement configuration invalidates evidence identity. No arbitrary cost
threshold or optimization recommendation is inferred from these proxy counts.

The connected gateway accepts and validates the additive summary but deliberately
does not publish it; no deployed cost claim is added to connected inspection.
Older strict receipt consumers must upgrade or deliberately project their supported
fields. Current Doctor checks remain read-only, and `behavioralEvaluation` remains
`not_run` even when retained host observations pass.
