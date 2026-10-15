# Unreleased — community alpha preparation

These are changes on main/candidate branches, **not a published release**. Package
metadata `0.1.0` does not identify a unique pre-release wheel; use a pinned source
commit and artifact hash until maintainers announce an immutable release.

## Current SDK capabilities

- Unified Capability Selection across native skills, workflows, single-turn
  specialists and explicit tools, with optional host-ranked shared shortlisting
  and separate dispatch authorization.
- Opt-in background launches through that shortlist, persistent task receipts,
  authorized status/cancel/result access and independent native ADK workers.
  SQLite is a single-host reference backend; interrupted work is not replayed.
- Explicit capability context/continuation and native skill-lifecycle integration
  on the locked ADK 2.11 baseline; no dynamic chat/task handoff or durable resume.
- Read-only embedded inspection, with separate offline and connected portal paths.
- Doctor declaration, instruction-layer and retained contract-evidence checks;
  observations are not authorization or model-quality certification.

## Changes since the initial community snapshot

- Opt-in per-spec staged-widget render-failure isolation; unchanged default
  behavior. See [runtime bindings](../runtime-bindings.md).
- [Answer presentation](../answer-presentation.md): Python-only extractive text
  and procedure selection with host-owned evidence, delivery and recovery.
- [Answer Explorer](../answer-explorer.md): complete answer plus inline,
  tap-to-ask topics; host-owned staging/action transport, no added model call.
- [Doctor](../doctor.md): partial skill-variant edits, unknown skill references,
  local artifact provenance, diagnostic SDK/served lock differences and opt-in
  host history-role checks.
- Doctor serving-startup import order, profile environment/lock parity, native
  skill-description limits and advisory reference-before-action findings.
- [Capability inference-call budgets](../capability-call-budgets.md): retained
  host measurements and coverage reporting; advisory, not execution limits.
- Shared coding-agent routing through [AGENTS.md](../../AGENTS.md),
  [llms.txt](../../llms.txt) and the compatibility pointer [llm.txt](../../llm.txt).
  The documentation gate checks their local link targets.

These entries describe implemented changes and their contracts, not new live
host evaluations or a new release certification. Dated reports remain historical.

## Community preparation

- Fix execution-trace expansion to paint on its own Material surface.
- Use the maintained native example in the local launcher and document Console setup.
- New schema templates explicitly use the public Gemini provider. Omitted provider
  keeps the legacy gateway default to avoid silently rerouting existing agents;
  native injected models keep their host-owned provider path.
- Portal container builds require an explicit approved `PORTAL_BASE` instead of
  an internal registry default. Pass a digest-pinned compatible image.
- Add verification-only community CI configuration and publication/security/conduct
  readiness guidance. Private-origin pipeline admission is unchanged.

No credentials, release tags, repository visibility or production deployment are
created by these changes. Public release remains gated by
[owner approvals and executable checks](../community-release.md).
