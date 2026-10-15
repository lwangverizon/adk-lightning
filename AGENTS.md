# Coding agents: start here

[llms.txt](llms.txt) is the concise documentation index ([llm.txt](llm.txt) is a
pointer). Use instructions, contracts and source from the same revision as the
installed SDK; the development package version alone is not enough. These files
are repository entry points, not automatically installed host instructions.

For **integrating ADKL into another application**, read
[the integration playbook](docs/agent-integration.md) first. It identifies public
APIs, ownership boundaries, migration steps, tests and handoff requirements.
During the pre-release checkout phase, build an immutable local wheel from the
specified ADKL commit and add that wheel to the host's lockfile; do not import
ADKL from a moving checkout or treat the package version on `main` as a release.
The host repository's instructions govern edits there; this repository's
development commands apply to this checkout, not automatically to the host.

For **creating an agent**, follow [project onboarding](docs/project-onboarding.md)
and [agent design](docs/agent-design.md). ADK product `SKILL.md` files are agent
content, not instructions to the coding assistant.

Applications remain in their own repositories. Use the two maintained
[minimal examples](projects/README.md) for runnable SDK/UI references, including
staged widgets and A2UI. Other `projects/` folders are user-owned local work;
do not import, delete or add them to CI merely because they exist. Application
end-to-end tests belong to the host repository, not the SDK's routine checks.

For **changing ADKL itself**, follow the contributor rules below. Read only the
guides relevant to the task; do not load every roadmap, review or project skill.

## Working on adk-lightning

Lightning is an ADK-native authoring/runtime prototype with a Flutter console.
Read README.md for current execution limits, docs/development.md for commands,
and docs/ci-cd.md when changing delivery. COMPETITIVE_PRODUCT_PLAN.md describes
the intended product direction; roadmap items are not implemented capabilities.

- Preserve unrelated working-tree edits. Use focused changes and existing style.
- Keep base authoring dependencies stdlib + PyYAML; optional ADK/model dependencies
  belong to the runtime extra. A module-level ADK import outside the declared
  runtime modules fails `tests/test_import_layering.py`; defer it instead. Use uv with the lockfile and Flutter's locked pub
  dependencies. Do not regenerate locks incidentally.
- Keep configuration, preview and export claims aligned with adkl/capabilities.py.
  A declared feature, visible tool, passing static lint or generated scaffold is
  not proof of executed behavior or enforced authorization.
- Use **Unified Capability Selection** as the feature name for the shared native
  skills/workflows/specialists selection surface. `CapabilityBindings` composes
  it; `CapabilityRetrieval` optionally shortlists; the parent model chooses
  execution. Keep these responsibilities distinct and follow the
  [canonical terminology](docs/capability-bindings.md#names-and-responsibilities).
- Keep integration instructions aligned with public signatures and native tests.
  Follow docs/development.md#documentation-maintenance when changing a contract.
- For model-visible instruction changes, follow [instruction layering](docs/instruction-layering.md).
  Review the final request after callbacks/tool preparation, not just prompt files.
  Attribute system, tool and active skill/workflow guidance; test served variants,
  owner-gated exposure, follow-ups and failures. A passing structural audit does
  not establish semantic consistency, model optimization or authorization.
- Run checks relevant to the change using docs/development.md. Add meaningful
  behavioral regression tests for risky changes; distinguish local checks,
  remote CI, skipped integrations and live evaluation in the result.
- Keep routine checks independent of credentials and external customer repos.
  Never write credentials into source, artifacts or build arguments.
- The current server is for development. Production deployment requires the
  runtime and infrastructure entry criteria in docs/ci-cd.md.

Workflow skills in .agents/skills are separate from ADK product skills under
projects/. Read the matching SKILL.md for substantial work in that scope:
feature/API/console delivery → adkl-feature-delivery; agent prompt/skill content →
adkl-agent-content; ADK version change → adkl-adk-upgrade; CI/release work →
adkl-release-review. Simple edits do not need an elaborate workflow.
