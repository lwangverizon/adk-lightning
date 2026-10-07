# Streaming and callback contracts

The development API streams progress from isolated project workers and releases
filtered reply text at completion. JSON and SSE share one execution path.

## Delivery behavior

`POST /api/chat` retains its JSON request/result contract. Sending
`Accept: text/event-stream` selects version 1 SSE. The same `ChatSession.stream`
produces both modes; `send` collects its terminal result. Each SSE frame is one
JSON object encoded on a `data:` line, followed by a blank line:

- `progress`: preparing, running (with retrieval matches), tool_call or tool_result
  (tool name only). The UI replaces its current progress label rather than adding
  a line for every event.
- `ping`: transport heartbeat for detecting disconnected clients.
- `text`: at most one complete, filtered, trimmed reply. No raw token delta is
  released, including when output filters are disabled.
- `done`: canonical `{reply, trace, execution}` result. Its reply equals the text
  frame, or null when no text was produced.
- `error`: terminal transport/worker failure. Configuration failures before
  headers still use the existing JSON HTTP error response.

Worker RPC also declares version 1 for streaming. Unknown frames, EOF before
done, errors, timeout and cancellation discard the worker and its pending buffer.
The Flutter client sends an explicit AbortableRequest signal: closing its HTTP
client alone cannot abort the browser body after headers have arrived.
The worker retains one asyncio loop for successive successful JSON/stream turns.
The development server still serializes requests; it is not a concurrent tenant
service. A disconnected client is detected on a heartbeat/write, not guaranteed
instantaneously. Stopping does not undo a tool side effect that already occurred.

Reply collection is bounded to 2 MiB of characters, individual worker responses
to 8 MiB and a stream to 32 MiB of received bytes. The worker's existing overall
timeout is retained; heartbeats do not extend it. HTTP writes have a five-second
stall timeout. Remote profiles still refuse before process creation or secret
loading; no remote transport is introduced.

## Filtering boundary

A fixed carry window cannot safely redact unbounded regex candidates such as
email addresses. `StreamRedactor` now buffers until flush, with a size cap.
Console chat likewise publishes only complete reply text. The native
post-model filter joins visible text parts before masking so identifiers cannot
escape across part boundaries; raw partials never enter console reply assembly.
Each completed model response contributes once, including multiple model/tool
cycles. A final full-reply filter runs before both the console history entry and
text/done frames are created. Declared secrets are independently masked across
every worker frame by the host, preserving the execution-profile boundary.

These are configured identifier patterns for response text, not a complete PII
classifier or a claim that arbitrary widget/tool data is sanitized. Customer
callbacks and tools remain trusted project code inside their worker. Existing
output-cleanup rules intentionally remove fenced blocks and whole-reply JSON
when enabled. Export does not claim these runtime features are wired.

## Callback composition

ADK stops an after-tool callback list at its first truthy replacement. Lightning
composes declared and platform after-tool callbacks into one callback that passes
the latest replacement to every subsequent transform. Required callback failures
propagate rather than silently releasing the untransformed result.

Platform ordering is public-help observation, explicit field projection, the
[generic result-size guard](tool-results.md), then current widget-contract
injection. The guard counts only its own envelope; later contract guidance adds
overhead. Host-specific result reduction belongs in the preceding host transforms.
Contract instructions survive the size guard. Contract selection is recomputed from
current staged state for each result; there is no turn-wide flag suppressing a
later contract. When a tool skips summarization, result guidance cannot reshape
an already completed reply. Widget emission retains its separate native callback.

`progressive_auth` is a public-help marker for an external gate to consult; its
state key must be turn-scoped. It is partial and does not authenticate a user or
enforce the order of sign-in prompts. `short_circuit` requires non-empty exact
phrases and replies, returns native ADK Content, and uses the native UiWidget
import for optional widgets. It is a list-shaped before-agent callback so it
composes with the declared callback configuration.

`prompt_state_injection` appends JSON context labelled as data. It supports one
state key or one project-relative file per context name, retains false, zero and
empty values, reports missing sources, and rejects traversal/absolute paths and
symlink escapes. It does not implement prompt-placeholder substitution and is
marked partial. File containment is checked at construction and every read.

## Verification

Focused tests cover all redaction split positions, native multi-call model
aggregation and history, native callback composition/replacements/widgets,
state/file boundaries, actual persistent workers, secret masking, remote refusal,
cancellation, timeouts, frame limits, truncated/error responses, UTF-8 boundaries,
final/text equality, Stop, and UI recovery. Fixtures use synthetic tools, text,
credentials and models; customer integrations and live provider evaluations are
separate required evidence for customer deployment.

Historical streaming-change evidence (not current suite totals): 427 Python tests passed, four optional/external tests skipped,
and 87 Flutter tests passed. The ClawTutor project interpreter independently
passed all nine native-lane tests. Analyzer, Ruff, component/project checks and
release web build passed. An isolated Chrome browser exercised a synthetic
30-second callback: Stop and Clear recovered within six seconds, followed by a
fresh completed reply. Visual inspection confirmed the filtered phone placeholder;
no model network call or customer integration was used. MR CI provides separate remote evidence. This remains a
development console/review artifact, not a production deployment.
