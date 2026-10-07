# ADKL roadmap

ADKL's direction is a reusable runtime and productive agent-design workflow for
the Google ADK community. This roadmap describes intended work, not support claims.
See [the overview](README.md) and [SDK contract](docs/sdk.md) for current behavior.

## Strengthen the shared runtime

- Preserve native ADK invocation semantics as dependencies evolve.
- Expand meaningful fixtures for selection, context, callbacks and presentation.
- Keep preview, SDK configuration and packaged execution aligned.
- Make unsupported behavior visible at configuration and execution boundaries.

## Shorten the design and feedback cycle

- Turn representative conversations into repeatable evaluation cases.
- Extend the local saved-change verification workflow with reviewable text diffs,
  richer host evaluation integrations and author usability evidence.
- Help authors detect instruction conflicts, overly broad skills and unnecessary
  model/tool turns without claiming static lint can certify agent quality.
- Improve existing-agent inspection and new-project onboarding using the same
  native runtime rather than introducing a second execution model.

## Deepen presentation compatibility

- Maintain the [A2UI version matrix](docs/a2ui-support.md) as the candidate evolves.
- Add catalog functions and explicit, host-authorized action transport.
- Broaden real-browser and accessibility checks beyond primitive rendering.

## Make community adoption sustainable

- Review redistribution terms for any third-party material bundled in a release.
  Library code and examples are Apache-2.0 ([LICENSE](LICENSE)).
- Keep documentation self-contained and examples independent of private systems.
- Publish reproducible SDK releases with clear compatibility and migration notes.
- Measure onboarding friction and prioritize contributions from independent hosts.

## Validate managed inspection and fleet operations

Keep the SDK, Doctor and local Console free and portable. The
[managed-service direction](COMPETITIVE_PRODUCT_PLAN.md#free-tooling-and-managed-services)
is proposed work, not an available product or pricing commitment.

1. Define versioned run/revision identities and redacted, bounded telemetry export;
   telemetry delivery failure must not break customer turns.
2. Pilot read-only inventory and retained inspection for customer-hosted agents,
   using authenticated outbound collection before requiring execution migration.
3. Add managed run/evaluation queues with immutable runtime artifacts, isolated
   workloads, durable state, cancellation, quotas and safe retry semantics.
4. Build fleet health, desired-versus-running revision checks, drift detection,
   rollout/rollback, budgets and team governance against actual operator needs.

Validate demand and cost with independent design partners. Hosted identity,
tenant isolation, secrets, data retention/deletion and operational response require
a separate control plane; do not expose the development server as SaaS.

Production multi-tenant hosting is a separate milestone with the security and
infrastructure entry criteria in [delivery](docs/ci-cd.md). Deployment scaffolds
and passing local tests do not substitute for those controls.
