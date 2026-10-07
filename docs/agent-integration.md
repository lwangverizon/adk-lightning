# Integrate ADKL with a coding agent

Use this playbook when asked to “integrate ADKL and follow best practices.”
ADKL is an engineering-alpha library for shared Google ADK runtime behavior and
local authoring. Start with the host's working agent; establish behavior through
execution tests and host evaluations.

Run [ADKL doctor](doctor.md) to collect deterministic findings and export a
coding-agent fix brief. Use `--inspect` explicitly for native declaration checks;
the default scan does not import host code.

Use the [host acceptance contract](host-acceptance.md) to verify inspection,
source editing, chat and shared runtime adoption before handing off a migration.
Connect a [host evaluator](verification.md) when the team needs repeatable
baseline/candidate checks after saving a skill. Declare the host's actual source,
model/index settings and grading inputs; the console does not infer them.

## Runtime, Doctor and Console are independent adoption choices

**Runtime** installs shared execution behavior into native ADK agents.
**Doctor** diagnoses the project's configuration and declarations without requiring
a full runtime migration. **Console** adds local authoring, factory inspection and
disposable test conversations; it does not become the application's production
Runner or connect to its live sessions. SDK installation starts no server.

Use Doctor to assess the starting point, choose one constructor integration below,
and attach the Console only when useful. These names describe capabilities, not
separate packages or managed endpoints. Paid managed inspection, execution and
fleet services are [future product direction](../COMPETITIVE_PRODUCT_PLAN.md#free-tooling-and-managed-services),
not prerequisites for using the free SDK/local UI.

## Read in this order

1. Read the host's repository instructions and identify its agent constructor,
   callback chain, dependency lock, Runner/session service and tests.
2. Read [current boundaries](../README.md#current-boundaries) and choose one path
   below. Read that path's contract before editing.
3. Read [agent design](agent-design.md) when changing instructions, skills or tool
   declarations. Read [execution environments](environments.md) only if attaching
   to the console, and [delivery](ci-cd.md) when preparing deployment.

Use docs and source from the **same ADKL revision as the installed SDK**. Record
that revision, the wheel SHA-256 and the host's resolved ADK version. Dependency ranges in
[pyproject.toml](../pyproject.toml) describe allowed resolution; the repository
[lock](../uv.lock) and native tests establish the tested baseline. A private ADK
distribution needs host compatibility checks. Do not replace its package sources
or regenerate its lock merely to match this checkout.

Roadmaps and dated reviews describe intent or historical evidence. If a guide
disagrees with code, inspect the capability declaration, implementation and native
test, report the discrepancy, and correct the guide in the authorized repository.
Do not assume an undocumented or underscore-prefixed API is stable.

## Choose one constructor integration

For an existing host with domain card tools, use native binding presentation packs
and `StagedResultTool` as described in [the binding contract](runtime-bindings.md).
To enable the original skill editor for native agents, register the actual host
`SKILL.md` files in [authoring source mappings](project-onboarding.md#map-native-skills-for-editing).
This preserves host ownership while enabling revision-checked saves and worker reload.
Preserve deterministic routes through `route_skill`; use final model observers
for request parity. After attaching the factory, run `python -m adkl.projects doctor /path/to/host` and
inspect the native declarations in Project. Keep host execution evidence separate
from SDK synthetic tests and remaining production evaluations.

| Host need | Public entry point | Contract and executable evidence |
| --- | --- | --- |
| Inspect native declarations without changing execution | `adkl.inspection.attach` | [Native attachment](runtime-bindings.md#preserve-a-host-owned-skill-lifecycle) |
| Adopt presentation/callbacks while retaining the exact host skill toolset | `RuntimeBindings(native_toolset=...)` | [Native ownership](runtime-bindings.md#preserve-a-host-owned-skill-lifecycle), [native tests](../tests/runtime/test_native_attachment.py) |
| Add history processing or result projection/trim while retaining existing skill orchestration | `adkl.adapters.build_callback_adapter` | [Adoption](adoption.md), [example](../examples/sdk_smoke.py), [native tests](../tests/runtime/test_callback_adoption.py) |
| Adopt shared selection, active context and staged lifecycle in an existing native constructor | `adkl.bindings.RuntimeBindings` then `merge_into(host_kwargs)`; `agent_kwargs()` for explicit assembly | [Bindings contract](runtime-bindings.md), [example](../examples/native_binding_smoke.py), [native tests](../tests/runtime/test_bindings.py) |
| Select across native skills, workflows and specialists with shared callbacks/staging | `CapabilityBindings.merge_into(host_kwargs)` | [Unified Capability Selection](capability-bindings.md), [shortlist example](../examples/capability_selection.py) |
| Launch independent long-running work through the same shortlist | `CapabilityBindings(background_capabilities=..., tasks=...)` with host-supervised `TaskWorker` | [Background tasks](background-tasks.md); host owns authenticated identity, task/worker authorization, storage and worker lifecycle |
| Build a new native agent from project files | `adkl.agent_runtime.build_agent`, or `AgentRuntime(...).app` for an ADK App | [SDK contract](sdk.md), [project creation](project-onboarding.md), [native tests](../tests/runtime/test_sdk_runtime.py) |

Import these APIs from their documented modules. `adkl.runtime` exposes callback
factories and wiring; it is not a hosted runtime or the primary constructor API.
`NativeLifecycle` and `SelectedSkillToolset` are internal mechanisms, not
application subclassing APIs.
Use explicit feature configuration and review the resolved defaults in
[features.py](../adkl/features.py) and [feature definitions](../adkl/feature_definitions).
An enabled feature must be supported for the selected target by
[capabilities.py](../adkl/capabilities.py); do not bypass rejection to make a demo run.

## Consume a pinned `main` checkout today

ADKL is not published to PyPI yet. For an external application, its coding agent
must receive a local ADKL checkout and an exact commit. It reads `AGENTS.md` and
this guide from that checkout, then builds a plain SDK wheel from the same commit.
The host installs that wheel as a direct local dependency and commits the host
lockfile. This is the supported pre-release workflow.

Do **not** import `adkl` directly from the checkout, add an editable dependency,
commit a wheel to either repository, or install whatever happens to be at `main`
when starting an application. `main` remains the integration source, not a
versioned package channel. While `pyproject.toml` still has a development version,
that version alone does not identify a particular `main` build; the commit and
wheel SHA-256 do.

### Check host artifact provenance before upgrading

Compare the host's committed pin, lockfile, installed package and source branch,
not just a sibling checkout or a guide titled “latest.” Record any host-maintained
SDK patches and their hashes as additional build inputs. A patched wheel is not
byte-equivalent to the plain upstream wheel at the same commit/version; do not
replace it until its extra imports and behavior have been reviewed and tested.
The plain upstream workflow below remains the default.

Example, using explicit paths and replacing `COMMIT_SHA` with the full commit
selected by the integrator. Use the authorized remote that actually contains that
commit; mirrors can lag. Set `ADKL_REPOSITORY_URL` to the authorized repository
URL provided by your maintainer; an internal mirror is not a runtime requirement.

```bash
# Clone once, then resolve and record the exact main commit for this migration.
git clone "$ADKL_REPOSITORY_URL" /work/vendor/adk-lightning
git -C /work/vendor/adk-lightning checkout --detach COMMIT_SHA
git -C /work/vendor/adk-lightning rev-parse HEAD

# Build a plain SDK wheel into a commit-specific local artifact directory.
cd /work/vendor/adk-lightning
uv sync --locked --extra runtime
uv run --no-sync python scripts/build_sdk.py \
  --out-dir /work/artifacts/adkl/COMMIT_SHA
shasum -a 256 /work/artifacts/adkl/COMMIT_SHA/adk_lightning-0.1.0-py3-none-any.whl

# Add the exact local wheel to the host project and commit its changed lockfile.
cd /work/host-agent
uv add --no-sync /work/artifacts/adkl/COMMIT_SHA/adk_lightning-0.1.0-py3-none-any.whl
uv sync --locked
```

Use the actual wheel name emitted by the build, because it changes when ADKL's
metadata version changes. Keep the commit-specific artifact directory immutable:
a later build goes in a new directory. The host's `pyproject.toml` and `uv.lock`
will record the local source for its environment. For a colleague or CI runner,
rebuild from the recorded ADKL commit, verify the recorded SHA-256, then run the
host's normal locked sync; a local file path is not automatically portable.

The plain wheel contains `adkl` and distribution metadata only. It excludes docs,
examples and `projects/`, so the coding agent still needs the matching source
checkout to read integration instructions. A plain wheel has no console assets.
For the console use the separately built console archive described in
[project onboarding](project-onboarding.md), again built from the same commit.

When ADKL has stable releases, this section will be replaced by installation from
a versioned package registry. The integration API and host tests remain the same;
only artifact acquisition changes. See [SDK releases](sdk-releases.md).

## Map ownership before replacing code

| Concern | ADKL mechanism | Application responsibility |
| --- | --- | --- |
| Selection | Vector retrieval or validated custom selector; native loading, pins, continuation | Skill descriptions, index/model identity, calibrated thresholds; custom ranking/cache policy |
| History | Outgoing request slicing and completed-tool-history repair | Durable events, retention policy, pending/resumable calls, provider-specific normalization |
| Skill state | Typed projection for active skills; `prepare_skill` hook | Trusted identity, authorized reads, freshness, concurrency and cache policy |
| Presentation | Packs or adopted `WidgetSpec`s; staging and completion emission | Domain payloads, custom sink transport, retry/deduplication and actual client behavior |
| Instructions | Compiled prompts, procedure refresh, static audit and [attributed final-request audit](instruction-layering.md) | Correct source/layer attribution, full served-variant capture after callbacks, domain policy, semantic review and behavioral evaluation |
| Tool results | [Generic size safeguard](tool-results.md) and optional explicit field projection | Schema-specific reduction, redaction, pagination, full-result retention and safe recovery; optional TOON encoding/evaluation |
| Serving | Native agent construction and local console inspection | Runner, sessions, authorization, deployment and monitoring |

The goal is domain skills and tools per agent, with shared application integration
for identity, preparation and transport. Do not move domain policy into ADKL or
remove a host feature that the chosen ADKL path does not implement.

## Implement and verify incrementally

1. **Capture the baseline.** Record constructor inputs, all callback phases and
   ordering, tool exposure, active state, model-call counts and expected client
   payloads. Identify fixtures for denials, errors and follow-up turns.
2. **Wire once.** Preserve the host's model, instruction, planner, subagents,
   Runner and session service. For existing constructors, prefer
   `binding.merge_into(host_kwargs)` to preserve native fields and callbacks.
   Use `agent_kwargs(callbacks=...)` for explicitly assembled constructors. Follow
   the [composition contract](runtime-bindings.md#compose-an-existing-constructor)
   for tool ownership and collision checks; issue each binding only once per agent.
   Do not also install the adapter, an old shared emitter or a second selector.
3. **Preserve order deliberately.** Follow the
   [callback ordering contract](runtime-bindings.md#callback-ordering-and-inspection).
   Raw-result telemetry belongs in `after_tool_observers`; observers return
   `None` and must not mutate results. Replacements are transforms. Explicit-load
   preparation precedes host guards; automatic loading skips ordinary tool
   callbacks, so preparation must enforce authorization for its own reads.
4. **Migrate one mechanism at a time.** Replace its old implementation in the
   candidate path after wiring it. Keep the old constructor available as a host
   rollback path. Start with auto-activation disabled; enable only after routing
   score/margin calibration. A state pin makes a skill reachable, not authorized.
5. **Prove the host contract.** Test required state missing/wrong type, empty and
   false tool results, host short circuits, denied domain tools, embedding failure,
   topic changes, second-invocation isolation and widget sink failure/retry where
   applicable. Ensure old callbacks no longer execute twice. Do not optimize
   model-call count by removing required consent or verification.
6. **Inspect locally when useful.** Attach a synchronous zero-argument factory
   returning native `BaseAgent` or `App` using
   [project attachment](project-onboarding.md#existing-adk-applications). Configure
   the host's independent runtime/profile. Console Test uses a disposable local
   Runner; feature edits to host Python must be made in the factory.
7. **Evaluate and hand off.** Run actual host cases, compare equivalent baseline
   and candidate evidence with [evaluation comparison](evaluation.md), and record
   the rollback procedure. Remove remaining duplicated mechanics only after the
   host's end-to-end checks pass.

## Migration checks for current runtime bindings

Use the [runtime binding contract](runtime-bindings.md) for exact semantics:

- With retrieval enabled, `min_skills_for_retrieval` defaults to 5: fewer eligible
  skills bypass even custom selectors and put descriptions in the prompt. Set it
  to 0 when the host needs selection for a small catalog; this does not load every
  procedure or enable automatic activation.
- Built-in `query_history` is off by default and widens candidates without changing
  scores. `adkl.query_history.QueryHistory` is a separate explicit prepare/commit
  helper for host ranking; it is not a replacement for the built-in EMA option.
- Review [tool-result sizing](tool-results.md) before enabling or upgrading
  `tool_result_trimming`: oversized output is an incomplete envelope, not the old
  schema with fewer rows. Put semantic reduction in host transforms before the
  guard, and never infer that omitted output authorizes a tool retry.
- Read `adkl:last_turn:<agent>` for per-agent diagnostics. The legacy snapshot is
  the most recent writer, not a routing or authorization input. Exercise parallel
  agents with unique names and overlapping skill names.
- Preserve selector pins and verify competing confident selections against
  `skill_retrieval.pin_grace_turns` (default 5), including ambiguous follow-ups.
- Set `seed_today=False` when the host owns date/time policy. Existing unmarked
  session dates remain host-owned; review old sessions when adopting refresh.
- Review widget `adoptionWarnings`, payload types and static roles. Verify staged
  completion when the host supplies its own contract text.
- Use `final_model_transforms` for request changes after shared processing and
  `before_model_observers` for inspection afterward. Transforms return `None`;
  separately verify host Runner/plugin processing outside the binding chain.

SDK installation does not enable a UI endpoint. For a read-only page in the
host's own service, use [embedded inspection](embedded-inspection.md): explicitly
register serving roots, provide the host authorization dependency, and optionally
mount a separately built connected portal. The host publishes lifecycle changes;
this does not attach the development console or observe per-session execution.
Use [console connections](project-onboarding.md#where-the-console-connects) for setup
and limits. Include the console URL, factory and execution profile in a local
inspection handoff; do not report it as attachment to live host sessions.

For static inspection publications, see [the portal contract](static-portal.md).
`adkl.inspection.workflow_graph(workflow)` projects native topology without node
execution. Hosts with their own runtime can explicitly attach declarations using
`adkl.inspection.attach(agent, skills=[...], workflows=[workflow])`; this installs
no execution callbacks. CapabilityBindings workflows are discovered by the shared
snapshot automatically. Private closure and activation-toolset discovery are not
public export APIs; explicitly declare those graphs when appropriate.

## Credential-free ADKL checks

Run these in the ADKL checkout, not in the host repository:

```bash
uv sync --locked --extra runtime
uv run --no-sync python -m examples.sdk_smoke
uv run --no-sync python -m examples.native_binding_smoke
uv run --no-sync python -m pytest tests/runtime/test_bindings.py tests/runtime/test_callback_adoption.py tests/runtime/test_sdk_runtime.py -q
```

Installation may access package registries. The examples and native tests use
synthetic models/tools; they do not verify the host or call live model providers.
For project-file agents, also run `python -m adkl.audit /path/to/project` in the
appropriate environment. Host-factory descriptors receive only descriptor-level
static audit; review resolved dynamic instructions and tools in host tests.

## Debug by symptom

| Symptom | Check first |
| --- | --- |
| Construction rejects a feature | Chosen API and capability target; adapter supports fewer features than bindings |
| Domain tool is missing | Exact tool name in skill metadata and supplied tools; selected versus active skill; intentionally global `core_tools` |
| State injection fails | Active skill field types and preparation completion; identity/freshness belong to the provider |
| Follow-up loses its procedure | Retrieval candidates, `continue_active`, `pin_grace_turns`, pins, and `adkl:last_turn:<agent>`; retained history is not an activation policy |
| Duplicate or missing cards | One emitter, enabled `staged_widgets`, specs/registers, suppression, sink errors and host deduplication |
| Console behaves differently | Same factory/revision, worker lock/profile, Clear after dependency changes, disposable Runner versus host services |

## Required handoff record

Report the chosen API and ADKL/host revisions; mechanisms replaced and remaining
host hooks; callback order; changed files; exact checks and outcomes; unrun live
cases; deployment/rollback steps and remaining gaps. Distinguish static lint,
synthetic native execution, console inspection and actual host end-to-end results.
Never describe a lower model-call count alone as a quality improvement.

Suggested prompt from an external repository:

> Integrate ADKL from the supplied checkout/revision. Read its AGENTS.md and
> docs/agent-integration.md, then follow this repository's own instructions.
> Build the plain wheel from that exact commit, add it to this repository with
> `uv add`, and record the commit, wheel SHA-256 and lockfile change.
> Identify our constructor and callback ordering, choose the smallest supported
> API, preserve host authorization and transport, implement the integration and
> verify it with our tests. Report remaining host responsibilities and unrun
> checks. Do not infer implemented features from roadmap documents.

Reading a dependency repository does not guarantee its agent instructions load
automatically. Explicitly provide this playbook when working in a separate host.
The root [CLAUDE.md](../CLAUDE.md) imports [AGENTS.md](../AGENTS.md) using
[Claude Code's documented import mechanism](https://code.claude.com/docs/en/memory).
