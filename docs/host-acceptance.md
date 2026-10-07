# External host acceptance

Use this contract with the [integration playbook](agent-integration.md). Opening
an agent in the inspector is the first check, not proof that it adopted the shared
runtime. Run host checks against the immutable wheel that will be locked there.

| Outcome | Required integration | Acceptance evidence |
|---|---|---|
| Inspect the actual agent | Native factory, host interpreter and execution profile | Skills, tools, callback order and shared feature declarations match the host constructor |
| Edit domain procedures | Explicit `authoring.skills` source mappings | Edit, lint, save, reload and verify the next request uses the changed skill; stale saves are rejected |
| Investigate findings | Native inspection and shared static audit | Selected-item and whole-agent Findings include a deliberately invalid fixture; dynamic instructions are reviewed separately |
| Test in chat | Host model setup or an explicit synthetic fixture | Send a turn, inspect activation, final request and tool/card output; repeat after saving |
| Test prepared context | Chat test-context JSON or host session adapter | Only the intended active skill receives its declared context; switching agents clears the test input |
| Share selection | `RuntimeBindings` with a selector adapter when needed | Preserve thresholds, exclusions, explicit routes and ranking policy; compare activation on host cases |
| Share history and state | Binding history settings and state projections | Assert the actual final model request after host callbacks, including a conversation beyond the configured window |
| Share staged widgets | Presentation pack or `StagedResultTool` around a domain tool | One terminal card, preserved domain payload and identifiers, no stale card on the next turn, isolated concurrent sessions |
| Preserve parallel-agent isolation | Unique agent names and per-agent diagnostics | Overlapping skill names do not mix selection/preparation; read `adkl:last_turn:<agent>` |
| Preserve date and pin policy | Explicit host date ownership and configured pin grace | Cross-date invocations, existing session dates, competing routes and ambiguous follow-ups behave as intended |
| Preserve application behavior | Existing host transport and identity/data services | Host E2E checks for authorization, persistence, event delivery, cancellation and relevant workflows |
| Receive shared diagnostics | `on_warning`, `on_finding` and `root` on the binding | A misconfigured feature reaches the host log, redactions reach the host audit trail, and a `from_file` context slot resolves; without the callbacks these are computed and discarded |

ADKL owns shared skill lifecycle, request processing, state projection, staging,
inspection and source editing. Hosts own domain skills and tools, model/provider
configuration, identity, data access, sessions, transport and business policy.
A small adapter may be necessary for calibrated retrieval, prepared context and
existing UI events. Review widget `adoptionWarnings` and payload/role preservation,
wire the binding's diagnostic callbacks, and verify final model transforms before
observers using the [runtime binding contract](runtime-bindings.md). Do not replace host callbacks wholesale: split shared mechanics
from policy, preserve their ordering and test short-circuit returns.

The maintained native and configurable examples exercise SDK contracts without
external application dependencies. Passing those tests does not certify a host's
live model behavior. For a migration handoff record the host commit, SDK commit and
wheel hash, dependency lock, source mappings, executed tests, synthetic dependencies,
unrun live evaluations and rollback switch. Refresh embedding indexes when source
changes affect retrieval. Python tool implementation edits remain host code changes;
the console edits skill bindings and instructions, not arbitrary Python.
