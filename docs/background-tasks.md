# Background tasks with Unified Capability Selection

**Opt-in Python SDK reference implementation.** Submit independent work from a
native ADK conversation, keep chatting, and inspect/cancel/retrieve it later.
This is not a production hosting service, a new graph engine, or automatic durable
workflow recovery. No worker starts merely by importing or installing ADKL.

## Ownership and selection

`CapabilityBindings(background_capabilities=..., tasks=...)` exposes named launch
operations through the existing eligibility, `CapabilityRetrieval`, parent-model
choice and dispatch-policy path. A shortlist never launches a job. Background
launches are standalone tool descriptors with the capability's name and description;
inspection additionally reports `backgroundCapabilities` and `taskManagementTools`.
No worker implementation, internal tools, skill bodies or graph instructions enter
the parent request. Do not also register the same logical name as an inline target.

The launch operation accepts `payload`, a JSON object, and returns a **receipt**,
not a completed business result. The existing capability event `completed` means
submission returned, not that the background task succeeded. Task status is the
source of truth for execution. Inputs are limited to 64 KiB in the SQLite backend;
use authorized artifact references for large inputs. Domain validation belongs in
the worker target before any protected side effects.

Four management tools are installed outside semantic shortlisting:

- `get_background_task`: receipt/status, without result or input.
- `list_background_tasks`: up to 100 recent authorized tasks in this conversation.
- `cancel_background_task`: request cancellation; acknowledge actual state.
- `get_background_task_result`: status, projected result and sanitized error code.

They remain exposed when the launch capability leaves the shortlist. Every call
resolves trusted host identity and checks task authorization; the backend also
checks ownership. `CapabilityPolicy` governs launch discovery/dispatch, while
`BackgroundTasks.authorize` governs task access. Management is not a bypass for
business tools. Denied/inaccessible tasks raise rather than returning private data.

`CapabilityLifecycle` is unchanged: context release, TTL and topic switches do not
cancel jobs, delete results or retain all their domain instructions.

## Configure the parent

Integration sketch; identity, authorization and ranking are trusted host inputs:

```python
from adkl.background_tasks import (
    BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity,
)
from adkl.capability_bindings import CapabilityBindings
from adkl.capability_retrieval import CapabilityRetrieval

backend = SQLiteTaskBackend("/private/task-data/tasks.sqlite")
tasks = BackgroundTasks(
    backend,
    identity=host_identity,  # context -> TaskIdentity(tenant_bound_owner, session)
    authorize=host_task_authorization,  # (identity, operation, capability) -> bool
)
research = BackgroundCapability(
    name="research", description="Research documents in the background.", version="v1",
)
binding = CapabilityBindings(
    background_capabilities=[research], tasks=tasks,
    retrieval=CapabilityRetrieval(host_ranker), policy=host_capability_policy,
)
# Native LlmAgent(..., **binding.agent_kwargs())
```

Authorization must return exactly `True`. Operations are `submit`, `status`,
`result`, and `cancel`. Identity callbacks may be async. Use an opaque owner that
includes tenant scope; a model-supplied user ID is not authentication. Result
permission is distinct from status/cancellation permission. A host can permit
cancellation after revoking submission permission. List omits denied tasks and
fails closed on policy exceptions.

Native launch keys combine invocation and function-call IDs, scoped by owner and
conversation. Replaying that call returns the same receipt; reusing its key with
different target/version/input fails. Separate model calls are separate requests:
host business idempotency is still required.

## Execute on an independent worker

Run `TaskWorker` in an independently hosted process, not an HTTP request background
callback. A worker registry maps exact `(capability_name, version)` pairs to async
executors accepting `(TaskRecord, abort_event)`. Unregistered versions stay queued.
Executors return JSON-serializable projected results; use artifact references for
large outputs. They must enforce domain authorization and idempotency themselves.

`ADKTaskExecutor` adapts a fresh host-configured native Runner:

```python
from adkl.background_adk import ADKTaskExecutor
from adkl.background_tasks import TaskWorker

executor = ADKTaskExecutor(
    runner_factory=build_worker_runner,  # task -> fresh native Runner
    message=build_input_message,         # task -> native Content
    project=extract_result,              # events -> JSON result/artifact reference
)
worker = TaskWorker(
    backend, {("research", "v1"): executor},
    authorize=host_execution_authorization,  # task -> bool, checked on worker
    timeout_seconds=3600,
)
# In the host worker's async entry point:
# await worker.run(stop_event)
```

The runner owns its model, callbacks, plugins, tools, credentials and session
service. Nothing is implicitly inherited from the parent. Each task creates its
own session using task ID, under the worker app name. A target may be an ADK 2.x
`Workflow` or an agent. The adapter awaits event-stream cleanup before closing its
runner. It rejects empty streams, native errors/interruption, and unanswered tool
calls (including native human-input requests) before result projection. The host
projector must additionally validate domain completion; a natural-language request
for clarification is not automatically distinguishable from a final answer.
The first slice does not support waiting-for-input or resuming that session.
The adapter collects events in memory; configure bounded model/tool budgets and
small outputs for this reference implementation.

ADK 2.11's native `abort_signal` is forwarded when available. Older versions use
cooperative event-boundary stopping and worker task cancellation after the grace
period; they do **not** promise 2.11's history sealing. Python cancellation cannot
undo committed business effects, stop arbitrary threads, or sandbox blocking code.
The timeout includes authorization and execution; authorization is monitored for
cancellation and lease renewal before any target starts. It is cooperative too:
callbacks and executors must yield and honor Python cancellation. Enforce hard
resource limits in worker hosting. `run(stop_event)` stops accepting work after
the current job; cancel the worker's asyncio task for immediate shutdown.

## Persistence and failure semantics

`SQLiteTaskBackend` uses short transactions off the async event loop. Submission
and queue insertion are one database transaction. Claiming is exclusive, and a
lease token fences stale result publication. Other local processes can open the
same file. Claims select the oldest queued task across all registered target
versions, not the first target in registry order. Running cancellations are
observed via heartbeat polling, independent of the customer connection. Queued
cancellation is immediate. Authorization and cooperative cancellation cleanup
retain the lease; a grace period longer than one lease does not itself mark the
worker lost. A custom backend's `heartbeat` must renew a still-owned
`cancel_requested` claim while returning False to stop execution; expired or
replaced claims must never be revived.

States: `queued`, `running`, `cancel_requested`, `cancelled`, `succeeded`, `failed`.
Cancellation wins a race with result publication and suppresses that result.
Internal exceptions persist only `execution_failed`, not raw private text.
Worker authorization denial records `execution_denied`.

**Queued work survives restart; in-flight work is not automatically replayed.**
Expired leases become `failed` / `worker_lost` on the next backend operation. Worker
shutdown records `worker_stopped`. A lost worker cannot overwrite the terminal
record, but its external side effects cannot be revoked by fencing the task table.
Do not automatically retry side-effecting jobs. Durable native graph recovery needs
a separately designed version/authorization/idempotency contract.

`TaskBackend` is the pluggable backend protocol. This SQLite reference is for
single-host local disk, not network filesystems, high concurrency, or distributed
production deployment. The host owns database permissions, encryption, retention,
backup, authorization, monitoring and worker supervision. There is no HTTP API,
notification/outbox service, steering/update operation, Console switch, YAML
configuration or generated export support in this increment. Do not use ADKL's
single-user development server as a production job host.

## Verification

Run the [credential-free example](../examples/background_tasks.py):

```bash
uv run --no-sync python -m examples.background_tasks
```

Credential-free tests cover persisted/idempotent submission, exclusive claims,
owner isolation, fresh authorization, queued/running cancellation, stale-worker
fencing, version mismatch, retrieval filtering, management after topic switching,
dispatch denial and execution of native ADK agents/graphs in separate sessions.
Acceptance tests also cover the same parent session continuing an unrelated turn
while work runs, later result retrieval, submission/worker/result in separate
processes, authorization timeout/cancellation, fair claims, cancellation cleanup
and native human-input rejection:

```bash
uv run --no-sync python -m pytest tests/test_background_tasks.py \
  tests/test_background_worker_lifecycle.py \
  tests/runtime/test_background_capabilities.py \
  tests/runtime/test_background_acceptance.py -q
```

These fixtures prove runtime mechanics, not semantic routing, provider behavior,
production deployment, native crash recovery or customer transaction correctness.
See [Unified Capability Selection](capability-bindings.md) and
[capability policy](capability-policy.md) for the existing boundaries.
