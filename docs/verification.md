# Verify saved skill changes

ADKL can run a configured host evaluator in a fresh local project worker, record
a baseline, and compare saved changes against it. The Project panel shows the
result, changed input files, measurements and the retained evidence path. The
host supplies the agent execution and grading; ADKL schedules complete case runs,
checks their identities and compares observations with the existing
[evaluation comparator](evaluation.md).

This is a local development workflow. It does not publish an agent, connect to
the production process, or certify inputs and side effects omitted by the host.
Only invoke evaluators appropriate for your local execution profile. They may
call models or business systems if the host configures them to do so.

## Try the maintained native example

Prepare [the native example](../projects/native-agent/README.md) with its locked
runtime first, then run from the ADKL checkout:

```bash
uv run --no-sync python -m adkl.verification baseline projects/native-agent
uv run --no-sync python -m adkl.verification candidate projects/native-agent
uv run --no-sync python -m adkl.verification status projects/native-agent
```

In the console, open **Project → Verify skill changes → Record baseline**.
Save a skill edit in the editor, return to Project, and select **Verify saved
changes**. Expand the changed input files and compare the observations. Use
**Refresh verification** after an external editor changes files while Project
is open. Unsaved editor buffers are not evaluated.

The example checks staged cards, the absence of stale cards on follow-up, and an
expected instruction in the actual model request. Remove `acknowledge thanks
without a card` from the display skill and the candidate fails both cases.
Restore the phrase and rerun to recover. Its model and embedder are synthetic;
zero provider cost is real for this fixture, but it is not a model-quality or
business-outcome evaluation. Latency is measured locally.

## Connect a host evaluator

Create `verification.yaml` in the same project as `agent.yaml`:

```yaml
version: 1
profile: local
evaluator: evals.skill_changes:evaluate
evaluator_files:
  - evals/skill_changes.py
subject_files:
  - agent.py
  - skills
  - instruction.md
suite: evals/cases.json
timeout_seconds: 120
policy:
  min_pass_rate: 1.0
```

Paths above are illustrative: declare actual host files. Paths can name files or
directories. Include tool code, prompt resources, model settings, retrieval
indexes and all other local inputs needed to reproduce behavior. ADKL always
adds `agent.yaml`, `execution.yaml`, `pyproject.toml` and `uv.lock` to the subject
identity, together with mapped native skill sources or the configured skill
directory. It does not infer the transitive dependencies of arbitrary Python
factories. Include grader helpers and judge settings in `evaluator_files`.

Paths stay inside the project; hidden paths, secrets with common private-file
extensions, declared profile credential files, execution source exclusions and
generated directories are rejected. Symlinks resolving to eligible
project-contained inputs are allowed; directory cycles are rejected. Python cache
directories are ignored. Do not put credentials into declared content. File
digests and names are retained, not full skill text or conversation transcripts.

The suite is a JSON object with unique case IDs and a repetition count:

```json
{
  "repetitions": 3,
  "cases": [
    {"id": "followup", "input": ["Show steps", "thanks"], "expected_cards": [1, 0]}
  ]
}
```

ADKL reserves `id` and optional `adkl_contracts`; other case fields are host-owned. The host module exports:

```python
async def evaluate(*, case: dict, repetition: int) -> dict:
    # Run the host's agent/evaluator and measure the complete observation here.
    # Return exactly these fields from its outcome assertions and usage data:
    return {"passed": passed, "latency_ms": elapsed_ms, "cost_usd": measured_cost}
```

This is a signature sketch, not a runnable grader. A complete credential-free
adapter is in [evaluate_changes.py](../projects/native-agent/evaluate_changes.py).
Synchronous callables are also supported. The worker calls the evaluator once
for each case/repetition pair, sequentially. The host should reset the agent,
session and fixture state for each observation, close its resources, and adapt
native ADK evaluation or its existing evaluation system as appropriate.

`passed` must be a boolean. Optional `request_costs: true` in configuration requires
numeric-only assembled-request captures on every observation; see
[boundary contracts and measurements](doctor-boundaries.md). Without that opt-in,
the existing three-field observation contract is unchanged. Measurement captures
are retained separately from the unchanged evaluation v1 report.

Latency and cost must be finite, nonnegative numbers.
Missing measurement is an error, never inferred zero. Failed outcomes return
`passed: false` with their measured usage. Exceptions, timeouts, missing fields
and incomplete coverage fail the run; they do not become passing observations.
The timeout covers the whole worker request, is 120 seconds by default, and can
be set from 1 to 600 seconds. Suites allow 1–1000 cases and 1–100 repetitions.

The configured local [execution profile](environments.md) supplies the host
interpreter and named credentials. Remote profiles are rejected. Verification
does not use the current chat session or its prepared test context. Put required
context into your cases and adapter. The editor's `?env=` overlays are rejected
for verification actions; explicitly configure the host evaluator's environment.
The browser cannot submit a different callable or arbitrary command.

## Lifecycle contract probes

[Doctor](doctor.md#retained-lifecycle-contract-evidence) can read current retained
results without executing this workflow. Optionally add expected contract names
to `verification.yaml`:

```yaml
contracts: [no_replay, single_emitter, state_isolation, rejected_call_recovery]
```

Tag each relevant suite case with the same names, for example:

```json
{"id": "empty-after-effect", "adkl_contracts": ["no_replay"], "fault": "empty_response"}
```

Tags must be unique supported names. Contracts omitted from `contracts` but tagged
on a case are also reported. A required contract without cases is missing coverage;
it cannot pass vacuously. Tag only behavior the grader actually asserts. Multiple
tags share the case's pass/fail observation; use separate cases for independent
attribution. Every repetition must pass for a contract, independently of aggregate
pass-rate allowances. Baselines remain baseline-only evidence.

`adkl.contract_probes` provides lightweight boolean assertions over **host-recorded
observations**. It neither instruments arbitrary host code nor executes tools:

| Helper | Observation boundary and required scenario |
| --- | --- |
| `no_replay(executions, *, expected)` | Ordered logical effect IDs recorded at a fake backend. Execute once, then inject empty output, timeout or persistence failure; detect an unsafe whole-turn replay. Expected IDs must be nonempty and unique. |
| `single_emitter(attempts, *, owner, expected)` | Ordered `(owner, item_id)` pairs recorded before sink attempts, including failures. Test partial emission and cancellation; a native fallback must not retry SDK attempts. Expected IDs must be nonempty and unique. This does not assert frontend acknowledgement. |
| `state_isolation(actual, *, expected)` | Exact equality of nonempty, explicitly selected observations. Include persisted and returned state, owner and pending outputs. Exercise failed/empty/cancelled refresh, follow-up and interleaved old/new work. |
| `rejected_call_recovery(outcomes)` | Exactly `[False, True]` from an invalid then valid call in the same invocation. Additionally assert the rejected call caused no forbidden effect. |

[Consent binding, outcome consistency and handoff continuity](doctor-boundaries.md)
and [incident-derived result, reachability and delivery probes](doctor-boundaries.md#incident-derived-probes)
are additional supported probes using this same tag/evidence workflow. Doctor's
repeatable [`--require-contract` gate](doctor.md#required-evidence-gate) can require
current passing evidence without executing the evaluator during a scan.

Use these inside your existing evaluator and combine their results with the host's
other assertions. Measure latency and cost as usual; unexpected exceptions fail the
run. Never point a fault-injection fixture at real payment or mutation backends.
These assertions check supplied observations only: incomplete instrumentation can
hide effects and arbitrary Python state cannot be inferred from an agent snapshot.

The [native example evaluator](../projects/native-agent/evaluate_changes.py) uses
`state_isolation` for no stale widgets on follow-up. Its suite is tagged so the
standard baseline/candidate commands produce Doctor contract evidence. Additional
[native regression examples](../tests/runtime/test_contract_probes_native.py) use
real ADK Runner/tools and SDK emission with scripted models and fake backends;
deliberately faulty host variants demonstrate that replay, second-emitter,
stale-state and premature-slot-claim defects fail the observation checks. They
are examples, not automatic coverage of an application that installs ADKL.

## Read the evidence

Each run is saved once under `.adkl/verification/<run-id>.json`; `state.json`
selects the current baseline and most recent attempt. Earlier run files remain.
The record includes input file hashes, suite/evaluator hashes, execution profile,
worker identity, complete observations and comparison results. The subject
revision is its content digest, so uncommitted saved edits have an identity too.

**Record baseline** replaces the reference pointer only after a complete valid
run. A baseline may contain failed observations; recording it does not claim it
meets the candidate quality gate. **Verify saved changes** preserves the baseline
and applies the configured policy to the new run. `min_pass_rate` defaults to
1.0; optional `max_latency_ratio` and `max_cost_ratio` use the comparator's
[existing definitions](evaluation.md#understand-the-gates).

A changed suite, evaluator, dependency lock or execution profile requires a new
baseline. A changed subject file makes the latest result stale. A failed attempt
replaces the latest result even if an earlier candidate passed. Files changed
during execution invalidate that run. Starting a second run while one is active
is rejected. After a crashed CLI process, confirm its worker has stopped before
removing the `.adkl/verification/running` directory; no automatic lock recovery
is attempted.

CLI commands return `0` for a recorded baseline or a passing candidate, `1` for
a failed/regressed run, and `2` for setup errors. `status` returns `0` for passed,
baseline-recorded, not-run and unconfigured states; other states return `1`.
Use the candidate command as the host's local/CI gate, then pass the identified
revision through the host's own review and release process.

The evaluator is trusted host code. ADKL validates complete reported observations
and declared input identities, not whether a grader is correct or whether it
actually exercised every claimed business action. There is no signed attestation,
statistical significance test, deployment authorization or automatic rollback.
Externally changing models, remote data, unpinned artifacts and undeclared files
need host controls. Existing [report-only comparison](evaluation.md) remains
available without running host code.
