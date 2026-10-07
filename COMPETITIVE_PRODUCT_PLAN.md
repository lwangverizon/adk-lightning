# Product direction: a shared layer for the ADK community

**Shared runtime patterns. Faster agent design. Native ADK.**

ADKL should make improvements to one agent reusable across many agents, while
keeping the application in control of tools, identity and serving. Its value is
a consistent design-to-runtime workflow: express a pattern, inspect its wiring,
exercise it and compare the result.

## Product structure

- **ADKL Runtime:** shared native ADK execution patterns, adopted through selected
  callbacks, `RuntimeBindings`, `CapabilityBindings`, or the project-file factory.
  [Unified Capability Selection](docs/capability-bindings.md) provides one selection
  surface across native skills, workflows and specialist agents. Not a separate server.
- **ADKL Doctor:** deterministic diagnostics and coding-agent handoff, available
  independently of a full runtime migration. It reports evidence, not behavioral
  certification or automatic fixes.
- **ADKL Console:** the local UI for authoring, factory inspection, disposable test
  conversations and host-provided change verification.

The [overview](README.md) describes what works today; public integration contracts
remain in the [integration playbook](docs/agent-integration.md).

## Free tooling and managed services

Keep the SDK and local Console free and Apache-2.0, including local Doctor,
inspection, verification and self-hosting. Keep one portable runtime contract;
do not make application portability depend on a paid SDK fork. Applications still
own domain policies, authorization and detailed tool-result reduction.

The intended commercial offering is **managed operations**, not paid access to
basic local development:

| Free developer tooling today | Proposed paid service — not implemented |
| --- | --- |
| SDK, local Console, Doctor and local verification | Team agent/revision/environment inventory and retained run inspection |
| Host-owned execution and local diagnostics | Managed agent runs and evaluation queues with budgets and usage visibility |
| Canonical project files and host deployment | Fleet health, configuration drift, rollout/rollback and operational audit history |

Private runners/BYOC, private networking, enterprise identity integration,
regional retention and support are potential enterprise options. Baseline tenant
isolation and security must apply to every hosted tier, not be optional safety
features. No managed service, pricing, SLA or launch date is offered today.

Start by validating read-only fleet inspection for customer-hosted agents before
asking teams to move production execution. A versioned, redacted telemetry contract
and outbound collection should preserve customer network boundaries and avoid
blocking agent turns when telemetry fails. Hosted execution then needs a separate
secure control plane and isolated execution infrastructure; the current local
`adkl.server` and worker subprocesses are not that boundary.

The differentiator to validate is the connection from **agent revision and
configuration → skill activation and tool surface → actual context and widget
delivery → evaluation and rollout**, rather than another generic trace viewer.
Test demand with independent teams and design partners; measure recurring usage,
time to diagnose a bad turn, operating costs and willingness to pay before setting
prices. Managed compute and retained telemetry have costs even with customer-owned
model keys. Apache-2.0 permits commercial hosting and competing forks; service
quality and useful operations must carry the business model.

## Who it serves

**SDK developers** should be able to adopt one callback inside an existing ADK
application, preserve native behavior and inspect the integration locally. The
full factory is available when file-based configuration is useful.

**Agent authors and domain specialists** should be able to start with a use case,
create a project, write focused skills, inspect findings and test the configured
agent. Deployment bundles should remain understandable to the engineers who own
their production environment.

## What should distinguish ADKL

- **Unified Capability Selection:** one discovery surface and optional shared
  shortlist across skills, workflows and specialists, with parent-model choice,
  host policy and native ADK execution kept distinct.
- Reusable context, history and presentation mechanics instead of repeated
  application glue code.
- Shared validation and runtime construction across the SDK and console.
- Canonical text files, small diffs and explicit capability boundaries.
- Evidence about actual execution and model turns, with room to integrate
  existing ADK and host evaluation systems.
- Self-contained examples and compatibility fixtures that independent users can
  reproduce without private infrastructure.

These are product principles, not proof of better latency, quality or cost.
Compare the same model, tools, scenarios and budgets before making those claims.

## Relationship to ADK tooling

ADKL complements native ADK development. It should reuse native agents, skills,
callbacks and evaluation contracts where appropriate. A larger visual canvas is
not the immediate priority: first make existing-agent adoption, focused skill
authoring and runtime diagnosis reliable and easy to understand.

The [dated competitive assessment](docs/reports/competitive-position-2026-09-06.md)
contains historical source research. Use the [current overview](README.md) for
implemented capabilities and [roadmap](ROADMAP.md) for upcoming work.

## Evidence of progress

Track time to a first successful local run, time to diagnose an unexpected turn,
successful adoption in independent applications and regressions prevented by
shared fixtures. Measure task outcomes alongside model/tool calls and resource
use; fewer calls are not an improvement if the task fails.

The repository is Apache-2.0 ([LICENSE](LICENSE)). Before a public source release,
review the redistribution terms of bundled examples. Before production hosting, satisfy the separate
[delivery entry criteria](docs/ci-cd.md). Neither milestone follows automatically
from a clean README or a passing unit-test suite.
