# Compare agent evaluation results

ADKL compares baseline and candidate reports produced by your host evaluator.
It reports regressions and returns an exit code suitable for a CI job. It does
not run agents, grade conversations, verify submitted outcomes or deploy releases.

To execute a configured host evaluator from the console or CLI and retain results
for saved skill revisions, use the separate [verification workflow](verification.md).
The report-only comparator below keeps its existing interface and behavior.

## Try it with synthetic evidence

From the repository root:

```bash
uv run --no-sync python -m adkl.evaluation \
  examples/evaluation/baseline.json \
  examples/evaluation/candidate.json \
  --max-latency-ratio 1.1 --max-cost-ratio 1.0
```

The command prints JSON and exits `0`. These checked-in reports contain invented
measurements and placeholder digests solely to demonstrate the format. They are
not benchmark results. Only the Python standard library is needed for comparison.

## Produce real reports in your host

Use native ADK evaluation or your existing test runner to execute the same suite
against both revisions. Convert its observations into this exact schema:

| Field | Meaning |
| --- | --- |
| `version` | Integer `1` |
| `suite_sha256` | SHA-256 of the complete case set, inputs and expected outcomes |
| `evaluator_sha256` | SHA-256 of the grading implementation/settings and metric definitions |
| `subject.revision` | Nonempty source/release revision label |
| `subject.artifact_sha256` | SHA-256 identifying the evaluated artifact or complete agent manifest |
| `results` | Nonempty array of observations described below |

Every observation has exactly these fields:

```json
{
  "case_id": "denied-account-action",
  "repetition": 1,
  "passed": true,
  "latency_ms": 240,
  "cost_usd": 0.001
}
```

`passed` must come from your outcome assertions: for example, the expected backend
state was reached or a denied action never executed. A plausible answer or a
completed conversation alone is not a success criterion. Include failed attempts
and their resource usage. Do not convert unavailable measurements into zero.

Record wall-clock latency and total variable cost consistently. Include the
model, tools, prompts, skills, indexes and dependency identities in the subject
manifest. Keep a stable grader when comparing models or configurations. For
model-based grading, record the grader model, prompt, settings and rubric as
evaluator inputs. Preserve reports and supporting traces under host access rules.

## Understand the gates

| Check | Behavior |
| --- | --- |
| Comparable evidence | Requires identical suite/evaluator digests and case/repetition pairs |
| Candidate quality | Requires 100% reported success by default; `--min-pass-rate` accepts 0–1 |
| Per-case regression | Rejects any case whose successful repetition count decreases |
| Latency, optional | `--max-latency-ratio` bounds candidate p95 relative to baseline p95 |
| Cost, optional | `--max-cost-ratio` bounds candidate total cost relative to baseline total cost |
| Invalid reports | Rejects missing/extra fields, empty results, duplicates, bad types and non-finite/negative metrics |

Repetitions are aggregated per case. An improvement on one case cannot conceal
a decline on another. Changing which repetition succeeds, with an unchanged
per-case success count, is not a regression. No significance test or confidence
interval is computed; select a sufficient repeated-run design for your workflow.

p95 uses the nearest-rank method. Cost per success includes costs from failures
and is `null` if nothing succeeded. It is a reported metric, not the cost gate:
the gate compares total cost on the matched observation set. With a zero baseline
for a ratio-gated metric, only zero candidate usage passes. The output includes
the configured policy, subjects, source report hashes, summaries and failures.

## Use the exit status in CI

```bash
python -m adkl.evaluation baseline.json candidate.json > comparison.json
```

| Exit | Meaning |
| --- | --- |
| `0` | Supplied evidence meets the configured comparison gates |
| `1` | Valid evidence failed one or more gates; JSON report is on stdout |
| `2` | Invalid input or policy; errors are on stderr |

Make the host job required if it gates promotion, and retain both output streams.
Do not append `|| true`. The ADKL repository CI tests this comparator using
synthetic evidence; it does not run a customer evaluation pipeline.

## Limits of the evidence

Matching digests are host declarations. The comparator hashes the report bytes
but cannot verify that the claimed agent, suite or evaluator actually ran. If
both reports omit the same cases, this tool cannot discover the omission from
the suite digest. The host must enforce complete execution and trusted report
production. Timeouts must become failed observations or an explicit host-job
failure, never silently disappear.

There is no skip status in this version. Different suites or graders require a
fresh baseline. A passing comparison is neither signed provenance nor production
authorization. Static [audit](sdk.md) remains separate and still reports
behavioral evaluation as unrun.
