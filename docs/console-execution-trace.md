# Mixed-capability Console execution trace

The local chat panel renders `trace.execution` schema version 1 independently of
legacy skill matches. This is diagnostic observation, not execution policy.

The chat panel groups correlated operations into skill/workflow cards. Workflow
outputs appear as a vertical step list; skill loading/resource/tool operations
appear separately within their skill card. Failures remain visible after a retry.
Retrieval candidates and raw technical details are collapsed by default. Raw data
retains exact identifiers and lifecycle events. Concurrent ambiguous node outputs
are left in technical details rather than assigned to the wrong workflow call.
Missing terminal events are labelled unconfirmed, never inferred successful.

- **Shared shortlist:** typed skill/workflow candidates returned by the existing
  shared retrieval call. Repeated identical snapshots in a turn are coalesced.
  Selection is not loading, authorization or execution. No scores are invented.
- **Capability lifecycle:** requested, started, completed, denied, failed or
  cancelled, with the operation and adapter outcome reason. Skill loading and
  skill tools are separate operations; workflows retain their native dispatch.
- **Workflow node outputs:** public ADK `node_info` output events, when available.
  Output observed is not an inferred start time, duration or business success.
  Run IDs and output payloads are not retained. ADK versions without this
  metadata simply omit these events.
- **Prepared model requests:** shared binding requests, not provider-call or token
  accounting. Private workflow model calls may not pass through this hook.
- **Tool calls:** existing Console calls remain visible even with no legacy skill
  match diagnostics. Native guards can reject before an adapter records an event.

A task-local collector is active only during a disposable Console turn. It does
not replace host callbacks, rerank candidates or alter tools/results. The trace
is bounded to 256 events and closes after completion/cancellation; concurrent
sessions and subsequent turns do not share observations. New observation events
exclude prompts, user arguments and result bodies. Existing tool-call trace data
retains its existing Console behavior and must still be treated as diagnostic data.

Older servers remain usable through the existing tool-call trace; unknown
execution schema versions show an explicit unsupported message. Customer adapters
using native skill ownership need not emit capability lifecycle events: their
actual load/tool traffic remains visible without claiming unified retrieval.

Verification: `tests/test_console_trace.py`,
`tests/runtime/test_console_capabilities.py`,
`tests/runtime/test_console_mixed_trace.py`, and
`console/test/chat_stream_test.dart` and
`console/test/execution_trace_card_test.dart`. Run the host's own integration checks with
its pinned/private ADK as well. Console observations do not certify production
Runner, client rendering, or backend business behavior.
