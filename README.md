# adk-lightning

**Shared runtime patterns. Faster agent design. Native ADK.**

ADKL helps teams turn a working Google ADK agent into a repeatable way of building
agents. Adopt reusable runtime behavior through a Python SDK, then use the Flutter
console to design skills, inspect configuration and debug what actually runs.

When each agent grows its own retrieval policy, history callbacks and prompt
conventions, improvements are hard to share and behavior becomes hard to compare.
ADKL puts those patterns in one tested layer while keeping agent content in
reviewable files and application tools under your control.

**Engineering alpha.** The SDK and local console work today. Production hosting,
complete protocol conformance and guaranteed agent quality are not implied.

[Quickstart](docs/quickstart.md) · [SDK adoption](docs/adoption.md) ·
[Unified Capability Selection](docs/capability-bindings.md) ·
[Background tasks](docs/background-tasks.md) ·
[Create an agent](docs/project-onboarding.md) · [Documentation](docs/README.md) ·
[Contribute](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Community release readiness](docs/community-release.md)

## Try a native tool turn without credentials

From this checkout, with Python 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked --extra runtime
uv run --no-sync python -m examples.sdk_smoke
```

The example runs a real ADK Runner with a synthetic model and tool. It verifies
that result projection preserves declared fields, including false values. It
does not call a model provider or external business system.

For a graphical walkthrough, use the [native agent example](projects/native-agent/README.md)
to inspect skills, run staged A2UI turns and verify a saved skill change.
See the [technical review brief](docs/reports/technical-review-2026-09-20.md)
for dated validation evidence and remaining acceptance work.

## Unified Capability Selection

**One selection surface for skills, workflows and specialist agents.** Instead
of maintaining separate discovery paths for each kind, register native ADK
capabilities together and let the parent model choose the appropriate action
from their descriptions. Native ADK still owns execution.

```text
Native capabilities → host eligibility → optional shared shortlist
                    → parent-model choice → dispatch authorization → native execution
```

Use `CapabilityBindings` to compose the catalog and shared runtime lifecycle.
Add `CapabilityRetrieval` when you need one host-ranked shortlist across all
kinds; the host supplies ranking, embeddings and continuation policy. ADKL does
not add a router-model call. The parent can load a skill, invoke a workflow, call
a single-turn specialist, answer directly or clarify. Shortlisting does not
activate a skill or authorize a business operation.

[Read the guide](docs/capability-bindings.md) or run the credential-free example
with the locked runtime installed:

```bash
uv run --no-sync python -m examples.capability_selection
```

This is an opt-in Python SDK feature, not a YAML switch or console workflow
editor. Opt-in [background tasks](docs/background-tasks.md) reuse the shortlist
for receipt-returning launches and offer authorized status/result/cancel tools;
the host supervises independent workers. Dynamic chat/task handoff and durable
in-flight resume are not supported. Synthetic
examples verify wiring, not routing accuracy, latency or cost improvements.

## Runtime, Doctor and Console

| Part | Purpose | Start here |
| --- | --- | --- |
| **ADKL Runtime** | Reusable execution patterns inside native ADK agents: selection, context, history, tool-result handling and staged presentation | [SDK integration](docs/agent-integration.md#choose-one-constructor-integration) |
| **ADKL Doctor** | Diagnose declarations, inspect retained host contract-test evidence, compare findings and export coding-agent fix briefs | [Doctor CLI and workflow](docs/doctor.md) |
| **ADKL Console** | Author skill files, inspect a local factory, test disposable conversations and run host-provided verification | [Local console](docs/project-onboarding.md) |

These are capabilities of the SDK and local UI, not separate hosted services.
Runtime participates in execution; Doctor diagnoses wiring and declarations.
Doctor does not fix files or certify behavior, and the Console does not attach
to live production sessions. Installing the SDK starts no server.

The SDK and local Console are Apache-2.0 and free to use and self-host.
For read-only declarations inside an existing service, [embedded inspection](docs/embedded-inspection.md)
provides explicit agent registration and optional authenticated FastAPI/portal mounting.
The host publishes generation changes; no per-session or fleet inspection is implied.
Separate [offline](docs/static-portal.md) and
[connected declaration](docs/connected-inspection.md) inspection candidates have
their own access and deployment requirements. Managed services remain
[proposed work](COMPETITIVE_PRODUCT_PLAN.md#free-tooling-and-managed-services).

**Integrating with a coding agent?** Start at [AGENTS.md](AGENTS.md) and the
[integration playbook](docs/agent-integration.md). They route an existing host
to the supported API, callback contract and verification cases. Until ADKL is
published, hosts build a wheel from a pinned `main` commit and record it in their
own lockfile; they do not import from a mutable checkout. Claude Code has a
[shared entry point](CLAUDE.md); other assistants can read the same playbook.

## Two ways to use ADKL

| Start from | Workflow |
| --- | --- |
| An existing ADK application | Add selected callbacks or [bind all four runtime patterns](docs/runtime-bindings.md) without replacing your agent constructor, Runner or session service. Attach a local factory to inspect and test it in the console. |
| A new agent idea | Create a project in the console, edit skills and configuration, prepare its runtime, test locally and produce a verified bundle for your deployment environment. |

The file-configured templates explicitly select `provider: gemini` for the public
Gemini API. Existing configurations that omit a provider retain the legacy
OpenAI-compatible `gateway` path; provision its URL/key explicitly rather than
assuming an internal service is available. Native injected models remain host-owned.

The SDK factory returns native ADK agents. The console preview uses the same
factory, so authoring and execution share a configuration contract. You can adopt
one pattern at a time; the host continues to own identity, tools and deployment.

Applications stay in their own repositories and install ADKL as a dependency.
The [projects directory](projects/README.md) contains two minimal maintained
examples, including **staged UI and A2UI v0.9/v1.0**, and can hold ignored local
projects created by the console. Application data and projects are not shipped
inside the SDK wheel. Console assets ship as a separate ZIP; see
[installation](docs/project-onboarding.md#install-the-sdk-and-console).

## What you can reuse

| Pattern | What ADKL provides |
| --- | --- |
| [Unified Capability Selection](docs/capability-bindings.md) | One discovery surface and optional shared shortlist for native skills, workflows and single-turn specialists; parent-model choice with separate host eligibility and dispatch authorization |
| Skill selection | Embedding retrieval, explicit state pins, confident activation and follow-up continuation around native ADK skill loading |
| Skill context | Typed state injection and procedure refresh so active instructions survive history slicing |
| Model context | History slicing and completed-tool-history repair through composable callbacks |
| Tool results | Explicit application-declared projection and a [generic size guard](docs/tool-results.md); oversized results become incomplete previews, while domain-specific reduction stays with the application |
| Presentation | Reusable packs, staged widget emission and rendering paths for all 18 A2UI basic primitives in v0.9 and the v1.0 candidate |
| Agent design review | Shared CLI/editor lint, static audits and [attributed final-request layering checks](docs/instruction-layering.md) across prompts, skills and tool declarations |
| Boundary evidence | Doctor checks explicit tool-output consumers and retained host contract/request measurements; it does not execute tools or enforce consent |
| Debugging and evaluation evidence | Runtime diagnostics, local host-evaluator runs for saved changes, and baseline/candidate report comparison |

For existing applications, use `CapabilityBindings.merge_into()` for Unified
Capability Selection, or `adkl.bindings.RuntimeBindings.merge_into()` for the
skill-only/shared lifecycle path. Use `adkl.adapters.build_callback_adapter()`
for selected callbacks only. New file-configured agents can use
`adkl.agent_runtime.build_agent()` or `AgentRuntime(...).app`.
These are alternative integration paths, not layers to install twice.

**Skill-only retrieval is a separate adoption path.** In `RuntimeBindings` and
the file-configured factory, `skill_retrieval` is opt-in. With it enabled, fewer
than five eligible skills bypass the selector and expose descriptions in the
prompt; set `min_skills_for_retrieval: 0` to keep a small catalog on that path.
Automatic activation is separately opt-in. Built-in EMA history widens candidates
without changing current scores; `QueryHistory` supports host-owned weighted
history. These defaults do **not** apply to `CapabilityRetrieval`.
See [runtime bindings](docs/runtime-bindings.md) and the
[selection-path comparison](docs/capability-bindings.md#choose-a-selection-path).

Use audits to find conflicting instructions and unnecessary complexity. Use
diagnostics and evaluations to measure model calls, task outcomes and resource
use. ADKL makes these patterns reusable; tests and host evaluations establish
whether they improve a particular agent.

## Design principles

- **Native ADK integration.** Reuse ADK agents, skills and callbacks with explicit
  composition and tested invocation contracts.
- **Files remain canonical.** Prompts, skills and YAML configuration stay readable,
  diffable and independent of the console.
- **One declared behavior.** Shared capability checks distinguish implemented,
  partial and unsupported execution paths.
- **Measure improvements.** Prefer smaller instructions and fewer unnecessary
  model turns, then verify outcomes against representative cases.
- **Keep boundaries explicit.** Tool visibility is not authorization, a local
  worker is not tenant isolation, and a generated bundle is not a production host.

## Current boundaries

| Area | Current scope |
| --- | --- |
| Serving | The console server is for local, single-user development. Production identity, tenant isolation and infrastructure belong to the host. |
| Evaluation | [Change verification](docs/verification.md) runs a configured local host evaluator and checks declared inputs and complete reports. The host owns execution assertions and grading; report-only comparison remains available. |
| Presentation | Local controls and action inspection work. Agent-side action dispatch, catalog functions and full v1.0 protocol conformance remain unsupported. |
| Packaging | SDK-backed bundles support native ADK serving. Generated-code export has incomplete parity and rejects unsupported capabilities. |
| Remote execution | Remote profiles are declarations; console remote transport is not implemented. |

See the [SDK contract](docs/sdk.md), [capability inventory](adkl/capabilities.py)
and [delivery requirements](docs/ci-cd.md) before depending on a behavior.

## Develop and contribute

```bash
uv run --no-sync python -m pytest tests --ignore=tests/tools -q
```

Start with the [architecture and public API map](docs/architecture.md).
See [development](docs/development.md) for the lightweight lane, Flutter checks
and browser media tests. Contributions that improve native ADK compatibility,
reusable patterns, clear agent authoring and reproducible evaluation are welcome.
The [roadmap](ROADMAP.md) separates those priorities from implemented features.

The base authoring package uses Python stdlib and PyYAML. ADK/model dependencies
are optional runtime extras, and a test holds that line: every `adkl` module
except three toolset wrappers imports with ADK absent. SDK distributions exclude `projects/`; agent content,
credentials and deployment profiles are supplied by the host.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). The same terms
cover the SDK, the console and the maintained examples in `projects/`.
Redistribution terms for any third-party material bundled into a future release
still need review; the license declaration does not settle that on its own.
