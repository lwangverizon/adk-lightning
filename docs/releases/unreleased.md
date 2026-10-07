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
