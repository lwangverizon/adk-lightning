# Architecture and contributor map

ADKL is one Python SDK plus a local Flutter console. It reuses native ADK agents,
skills, workflows and execution; it does not replace the host's Runner, sessions,
authorization or business tools. Start with the public entry point for your task,
then follow its implementation and behavioral tests.

## Choose an entry point

These are alternative adoption paths, not layers to stack on one agent.
Import from the documented modules; private helpers are not extension APIs.

| Task | Public entry point | Contract and executable reference |
| --- | --- | --- |
| Publish read-only registered agent declarations in a host | `adkl.embedded.InspectionRegistry`, `adkl.inspection_fastapi.mount_inspection` | [Embedded inspection](embedded-inspection.md), [example](../examples/embedded_inspection.py) |
| Inspect without adopting execution | `adkl.inspection.attach` | [Native attachment](runtime-bindings.md#preserve-a-host-owned-skill-lifecycle), [tests](../tests/runtime/test_native_attachment.py) |
| Add selected request/tool callbacks | `adkl.adapters.build_callback_adapter` | [Adoption](adoption.md), [example](../examples/sdk_smoke.py) |
| Share skill selection, context and presentation, or preserve a native toolset | `adkl.bindings.RuntimeBindings` | [Runtime bindings](runtime-bindings.md), [example](../examples/native_binding_smoke.py) |
| Select across skills, workflows and specialists | `adkl.capability_bindings.CapabilityBindings` | [Unified Capability Selection](capability-bindings.md), [shortlist example](../examples/capability_selection.py) |
| Launch long-running work through shared retrieval | `CapabilityBindings(background_capabilities=..., tasks=...)`, `BackgroundTasks`, `TaskWorker`, `ADKTaskExecutor` | [Background tasks](background-tasks.md), [example](../examples/background_tasks.py) |
| Build a file-configured native agent | `adkl.agent_runtime.build_agent`, `AgentRuntime` | [SDK](sdk.md), [native tests](../tests/runtime/test_sdk_runtime.py) |
| Diagnose or verify changes | `python -m adkl.doctor`, `python -m adkl.verification`, `python -m adkl.instruction_audit` | [Doctor](doctor.md), [host verification](verification.md), [final instruction layers](instruction-layering.md) |

**Unified Capability Selection** is implemented by `CapabilityBindings`, which
reuses runtime composition internally. With optional
`CapabilityRetrieval`, eligibility precedes one mixed shortlist; the parent model
chooses whether to load a skill, invoke a workflow, call a single-turn specialist,
answer directly or clarify. Dispatch checks remain separate from retrieval.
Optional background launch tools return receipts for independent host-managed
workers; authorized task management stays available outside the shortlist.
Dynamic chat/task handoff and durable resume are not covered. This path is
Python-only, not a YAML feature or console workflow editor.

## Choose an inspection surface

| Need | Preferred surface | Limits |
| --- | --- | --- |
| Edit skills and run disposable local tests | Local Console with a host factory | Trusted single-user development only |
| Register read-only declarations inside an existing service | [Embedded inspection](embedded-inspection.md) | Host owns authentication, grants and generation publication |
| Publish reviewed snapshots without a live host | [Offline portal](static-portal.md) | Static snapshots, explicit disclosure approval and protected hosting |
| Read host declarations through a separate gateway | [Connected inspection](connected-inspection.md) | Additional gateway/authentication contract and deployment |

Embedded inspection is the simplest in-service adoption path; offline publication
is appropriate when no live connection is wanted. Connected inspection remains a
separate supported candidate, not an automatic requirement for embedding. None of
these surfaces provides a production session debugger or background-task dashboard.
No existing API is deprecated merely by this preference table.

## Follow a change through the source

| Area | Implementation | Tests |
| --- | --- | --- |
| Configuration and execution support | `features.py`, `feature_definitions/`, `agent_schema.py`, `capabilities.py` | `test_features.py`, `test_feature_extension.py`, `test_capabilities.py` |
| Native lifecycle and callbacks | `bindings.py`, `agent_runtime.py`, `callbacks/`, `runtime.py` | `runtime/test_bindings.py`, `runtime/test_callback_adoption.py`, `runtime/test_output_composition.py` |
| Unified Capability Selection | `capability_bindings.py`, `capability_contracts.py`, `capability_retrieval.py`, `capability_policy.py`, `capability_execution.py` | `runtime/test_capability_*.py` |
| Background tasks | `background_tasks.py`, `background_adk.py`, `capability_bindings.py` | `test_background_tasks.py`, `test_background_worker_lifecycle.py`, `runtime/test_background_*.py` |
| Skill selection and context | `retrieval.py`, `query_history.py`, `skill_runtime.py`, `runtime_state.py` | `test_retrieval.py`, `test_weighted_query_history.py`, `runtime/test_skill_ownership_seams.py` |
| Presentation | `widgets.py`, `presentation*.py`, `a2ui*.py` | `test_widgets.py`, `test_presentation*.py`, `runtime/test_native_presentation.py` |
| Authoring and diagnostics | `skill_doc.py`, `skill_lint.py`, `lint_*.py`, `audit.py`, `doctor*.py`, `inspection.py` | Component audit/Doctor/editor tests; native inspection tests |
| Local execution and API | `server.py`, `_server_workspace.py`, `_server_http.py`, `_server_routes.py`, `execution.py`, `worker.py`, `host.py`, `chat.py` | `test_server_workspace.py`, `test_server_routing.py`, `test_execution.py`, `test_browse_inspection.py`, `runtime/test_worker.py` |
| Embedded host inspection | `embedded.py`, `_declarations.py`, `inspection_fastapi.py` | `runtime/test_embedded_inspection.py`, connected protocol and UI tests |
| Console and read-only portals | `console/lib/`, `connected*.py`, `portal.py` | `console/test/`, `test_connected*.py`, `test_portal.py` |
| Packaging and delivery | `projects.py`, `release.py`, `scripts/build_sdk.py`, `scripts/ci/` | `test_projects.py`, `test_sdk_release.py`, installed-wheel onboarding |

Python implementation paths above are relative to `adkl/`; test paths are
relative to `tests/`. The [feature maintenance guide](feature-maintenance.md)
provides the detailed configuration-to-execution map.

### Two meanings of capability

- **Execution-support manifest** (`adkl.capabilities`): reports whether declared
  YAML features are implemented, partial or unsupported for SDK/preview/export.
- **Unified Capability Selection** (`adkl.capability_*`): one discovery and
  selection surface for native skills, workflows, agents and explicitly registered
  tools, with separate retrieval, authorization and execution phases.

The feature manifest is not a complete SDK API inventory. Do not add an inert
YAML switch just to make a Python-only API appear there.

## Boundaries to preserve during cleanup

- Public imports stay stable. `NativeLifecycle`, `SelectedSkillToolset` and
  `capability_execution` are internal; do not subclass them in applications.
  Prefer the public dependency-light protocols in `capability_contracts.py`.
- Base authoring imports require only stdlib and PyYAML. Runtime imports are
  deferred except for the three native wrappers checked by
  [import-layering tests](../tests/test_import_layering.py).
- `callbacks/` contains focused mechanisms; `bindings.py` places their native
  phases. Internal `callbacks/_lifecycle.py` handles callback normalization,
  output/tool chains and emission/completion error precedence; it is not a public
  extension API. Preserve raw observers, sequential transforms, short circuits
  and emission failure behavior when extracting code.
- The local server owns disposable development conversations, not production
  identity or tenant isolation. `server.Handler` retains workspace endpoints,
  origin/error handling; `_server_workspace.ServerWorkspace` owns selected paths,
  agent inventory, profile, disposable worker and their reentrant lock.
  `_server_http.py` provides bounded body/response transport and
  `_server_routes.py` only selects endpoint methods. Body reads remain outside
  the workspace lock. State transitions and lazy worker creation use that same
  lock, including direct lifecycle calls. The server still has one process-wide
  workspace; this is not multi-server or tenant isolation.
  Former module state globals (`ROOT`, `CHAT`, `PROFILE`, etc.) were internal,
  not SDK APIs. Tests now replace `server._workspace` or its fields; the CLI,
  HTTP endpoints, `Handler`, `reset_chat` and `select_agent` entry points remain.
- `examples/` contains Python SDK snippets; `projects/` contains two maintained
  runnable SDK/UI projects plus ignored user work. CI uses
  [the explicit example list](../projects/examples.json), never discovery of
  arbitrary user projects.
- `tests/support/` holds reusable helpers, not test cases. Native helpers import
  ADK only when their module is imported; the package initializer stays light.
  Keep behavioral assertions in tests rather than reusable fixture modules.
- The SDK wheel excludes application projects and documentation. Console assets
  normally ship separately. Verify installed-wheel imports when moving modules.

## Current contracts versus historical evidence

Use [the documentation index](README.md) for current behavior. Dated
[reports](reports/) record what was checked at a particular revision; they are
not current support matrices or proof of deployment. Keep findings and provenance
when reorganizing them. Host-specific infrastructure is not a community runtime
prerequisite.

Legacy generated-code `eject`, combined console wheels and deprecated policies
remain compatibility surfaces. Prefer SDK-backed bundles and explicit host
policies, but do not remove existing paths without a consumer/migration decision.

See [development](development.md) for commands and evidence requirements. A
reviewable cleanup preserves observable contracts; folder moves alone do not
establish a better architecture.
