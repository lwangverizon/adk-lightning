---
name: adkl-adk-upgrade
description: Upgrade or assess a Google ADK version change in adk-lightning, including SkillToolset, callbacks, state, config, LiteLLM and export compatibility. Use for ADK dependency migrations. Not for unrelated dependency bumps or ordinary feature implementation.
---

# Upgrade Lightning's ADK contract

Read pyproject.toml, uv.lock, tests/runtime and affected imports. Read
README.md and docs/development.md. Record the old and proposed resolved versions;
compare official ADK release notes with installed source where behavior matters.
a host's private wheel can diverge from the public package used by Lightning.

1. Find affected agent/tool/skill/callback/state/model APIs with rg. Establish
   whether the proposed behavior is native ADK, a private extension or Lightning
   code. Do not infer support solely from release notes or configuration fields.
2. Update the dependency and regenerate the lock deliberately. Review transitive
   changes and preserve the base PyYAML-only authoring boundary. Keep the native
   contract tied to the locked version; do not skip it when an import breaks.
3. Adapt actual API contracts. Verify procedure loading, reference loading,
   additional tool exposure, activation state and session isolation. Exercise
   preview/export behavior through Lightning for paths that changed.
4. Run lightweight and runtime lanes plus the dependency audit from the development
   guide. Use model/provider integration tests only with available authorized
   sandbox access; report them separately from offline contracts.
5. Summarize version changes, evidence, compatibility/migration impact, and a
   rollback using the previous reviewed dependency lock and code revision.
   Never claim private-runtime parity from a green public-ADK fixture alone.
