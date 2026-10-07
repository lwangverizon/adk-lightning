# ADKL technical review — September 20, 2026

ADKL is an engineering-alpha Python SDK and local Flutter console for reusable
Google ADK integration patterns. It is ready for technical evaluation with
synthetic examples. Production readiness and improved agent outcomes require
separate acceptance evidence.

This review covers source commit
`04d8f683222614264de7b09e43059b2e3b3cf073`. Results below were obtained locally
on that clean revision before the accompanying documentation/package-description
cleanup. They are not remote CI results or a certification by the ADK team.

## Architecture and ownership

| Layer | Owns | Review entry point |
| --- | --- | --- |
| Application | Native agent construction, tools, identity, authorization, Runner, sessions and deployment | [Integration playbook](../agent-integration.md) |
| ADKL Runtime | Optional selection, context, history, result handling and staged presentation patterns | [Runtime bindings](../runtime-bindings.md) |
| Capability composition | Mixed discovery of native skills, workflows and specialists; host-provided eligibility and dispatch policy | [Capability bindings](../capability-bindings.md) |
| ADKL Doctor | Declaration diagnostics and inspection of retained host evidence | [Doctor boundaries](../doctor-boundaries.md) |
| ADKL Console | Local authoring, factory inspection, disposable conversations and host-provided verification | [Project onboarding](../project-onboarding.md) |

The [architecture map](../architecture.md) connects public entry points to source
and behavioral tests. Adoption paths are alternatives: applications can use one
callback, shared bindings or the file-configured factory. Installing the SDK
starts no service. The base authoring package depends only on PyYAML; ADK and
LiteLLM belong to the optional runtime extra.

## Validation evidence

The full Python suite used the repository lock: Google ADK **2.8.0**, LiteLLM
**1.99.0**, Python **3.13**. Flutter dependencies used the checked-in pub lock.

| Local check | Observed result |
| --- | --- |
| `python -m pytest tests --ignore=tests/tools -q -ra` | 1,277 passed; 153 subtests passed; five experimental/deprecation warnings; no skips |
| `ruff check adkl tests scripts` | Passed the configured defect checks; this is not full style/type coverage |
| `python scripts/ci/check_docs.py` | Passed for 60 documentation files; local targets only, not URL or fragment validation |
| `python scripts/ci/sdk_release.py check` | Passed metadata/release-note checks; no publication performed |
| `examples.sdk_smoke`, `examples.native_binding_smoke`, `examples.native_capabilities` | Passed with native ADK and synthetic models |
| `flutter analyze` / `flutter test` | No analyzer issues; 214 tests passed |
| `flutter build web --release` | Passed; artifact manifest created and verified |
| `bash scripts/ci/audit.sh` | No known vulnerabilities found in the exported locked Python runtime dependencies |
| SDK wheel and sdist build, then `scripts/ci/onboarding_smoke.py` | Passed isolated installation, locked downstream project, verified bundle and native execution, including staged A2UI v0.9/v1.0 |

The first Python run had four failures because the execution sandbox denied
localhost socket binding. The complete suite passed after enabling that access.
The downstream onboarding environment resolved ADK **2.9.2** and LiteLLM
**1.102.0**; only onboarding smoke coverage, not the full suite or dependency
audit above, applies to that resolution. Declared dependency ranges are not a
tested compatibility matrix.

No live-model/business-system evaluation, interactive browser walkthrough,
secret scan, remote pipeline/protection verification or deployment acceptance
was performed. GitLab API authentication was unavailable. Historical host
adoption evidence is separate from these SDK checks.

## Suggested review walkthrough

1. Run the [credential-free quickstart](../quickstart.md) and inspect how a
   declared result projection changes the next native ADK model request.
2. Follow the [native-agent example](../../projects/native-agent/README.md):
   inspect its host-owned constructor, load the console, and send `Show v0.9`,
   `Show v1.0`, then `thanks`. Check the staged cards and absence of stale emission.
3. Use the example's saved-change verification flow to compare a baseline with
   a deliberate skill regression, restore the instruction, and verify again.
4. Inspect the [capability example](../../examples/native_capabilities.py) and
   discuss eligibility, native execution and application authorization boundaries.

These examples demonstrate execution mechanics with scripted responses. Rehearse
the browser flow on the presentation machine; build/widget tests do not establish
the visual result of an interactive session.

## Work needed for broader adoption

- **Measure benefit:** compare the same model, tools and representative cases
  with and without selected ADKL patterns. Report task outcomes, latency, token
  use and onboarding effort; synthetic test counts do not measure agent quality.
- **Establish release evidence:** repair runner prerequisites, obtain a green
  verification pipeline and exercise immutable package publication. Ordinary
  push/MR/schedule pipelines are disabled by the [current policy](../ci-cd.md#current-pipeline-policy).
- **Broaden compatibility:** test supported ADK/Python combinations and replace
  host reliance on private seams with reviewed public extension contracts.
- **Complete target-specific acceptance:** full A2UI conformance, remote console
  transport and generated-code parity remain outside current support. Production
  hosting has separate [entry criteria](../ci-cd.md#production-delivery-target).

Useful collaboration topics are stable native extension points, upstreamable
contract tests, and an independently reproducible reference integration.
The [roadmap](../../ROADMAP.md) describes planned work; this review does not imply
funding, endorsement or a committed delivery date.
