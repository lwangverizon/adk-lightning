# Developing Lightning

Use small, reviewable branches from `main`. Keep the authoring package lightweight
(stdlib + PyYAML), and put runtime-only imports behind the runtime boundary --
`tests/test_import_layering.py` enforces this by importing every `adkl` module
with ADK, `google.genai` and LiteLLM made unimportable. All but the three modules
in its `RUNTIME_ONLY_MODULES` set must still import. Defer a new ADK import into
the function that needs it rather than widening that set.
Use locked dependencies; commit intentional lock changes alongside configuration.
Declare test fixture dependencies in the dev group; the base authoring install
remains PyYAML-only. The runtime
extra installs ADK and LiteLLM directly instead of all ADK extensions.
Do not copy a host's private wheel, registry credentials, deployment settings, or
large toolchain wholesale into Lightning.

## Environment boundaries

Read [environments.md](environments.md) before adding runtime dependencies, secret
settings or execution targets. Keep platform and project uv locks independent;
project code executes in a worker, with explicit credentials and profile state.
Never make a customer-restricted runtime depend on the authoring machine's VPN.
Add process-isolation regression coverage when changing this boundary.

## Commands

Ordinary push/MR pipelines are off by default. Run relevant checks locally and
record results in the MR; use an explicit web pipeline on main when remote
verification is needed. Protected release tags retain their full gates. See
[the current pipeline policy](ci-cd.md#current-pipeline-policy).

From the repository root:

```bash
# Normal runtime development, including pytest and the native ADK contract.
uv sync --locked --extra runtime
bash run_tests.sh --junit reports/components.xml
uv run --no-sync python -m pytest tests --ignore=tests/tools --junitxml=reports/runtime.xml -q

# CI tools. This includes dev/runtime here so local sync retains those packages.
uv sync --locked --extra runtime --group ci
uv run --no-sync ruff check adkl tests scripts
uv run --no-sync python scripts/ci/check_docs.py
bash scripts/ci/audit.sh

# Frontend, using Flutter 3.44.0.
cd console
flutter pub get --enforce-lockfile
flutter analyze
flutter test
flutter build web --release
```

Without runtime dependencies, `uv sync --locked` and `bash run_tests.sh` exercise
the lightweight lane. Both plain and `--junit` shell-runner modes use pytest,
including function-style and parameterized tests. Missing ADK is a failure in the native lane. A skipped
external host test is not evidence of integration compatibility. Do not make
routine tests depend on a developer's home directory, `.env`, or a live model.
`scripts/ci/audit.sh` queries public package advisories and fails on findings or
service errors. CI's isolated tool environment uses `--only-group ci`; do not use
that option in a working runtime environment unless you intend to remove other
groups/extras.

The [static multi-agent portal](static-portal.md) has a separate Flutter entry
point and no live agent transport. Run `flutter test test/portal_test.dart` and
`flutter build web --release --target lib/portal_main.dart --no-web-resources-cdn`
for its UI; `tests/test_portal.py` and `tests/runtime/test_portal_native.py` cover
offline export/publication. `scripts/build_portal.sh` prepares the static container
context; image execution, authenticated ingress and staging remain separate checks.

## Choose evidence by behavior changed

For the public entry points and source/test ownership, use the
[architecture map](architecture.md). Shared test helpers belong in
`tests/support/`, not in other `test_*.py` modules: importing a test class can
make pytest collect its cases twice. Import native helpers explicitly rather
than loading ADK from the support package initializer.

For shared behavior, start with [feature maintenance](feature-maintenance.md).
It maps each feature family to its declarations, validation, runtime and tests,
and explains how to add a feature without duplicating support claims.

For host-owned agent runs, the [evaluation comparison guide](evaluation.md)
documents result inputs and regression gates. The comparator's synthetic tests
run in the component lane; actual customer evaluations run in their host.
The [verification workflow](verification.md) also has component tests for worker
failures, complete observations and stale input identities, plus a native fixture
in `tests/runtime/test_verification_native.py`. Customer evaluator correctness
and live-model outcomes remain host checks.

| Change | Required reasoning and checks |
|---|---|
| Runtime feature or callback | Trace config → shared capability manifest → construction → execution; test actual behavior and failure paths. Keep preview/export support truthful. Run components and native runtime lane. |
| API/config persistence | Verify validation, save/reload, error status, conversation invalidation and byte-preserving edits where promised. Include cross-project/session tests when the behavior touches them. |
| Console | Run analyzer/widget tests, check loading/error/unsupported states, and inspect the changed workflow in a browser. Build the release-mode web artifact. |
| Agent prompt/skill/tool/widget | Identify activation boundary, tool contract and side effects; lint affected config/content. Include positive/near-neighbor/negative and recovery cases. Do not claim runtime or business outcomes from static lint alone. |
| ADK/dependency update | Record old/new resolved versions, review official release notes and actual imported API, inspect lock diff, run native contracts plus components with runtime installed. |
| Delivery/security | Exercise success and failure behavior, trust boundaries and artifact identity. Run project CI Lint and an MR pipeline when GitLab access exists. |
| Documentation only | Verify facts, links and commands relevant to the edit. No unrelated test expansion. |

Use existing tests as conventions. Add tests for behavioral risks and regressions,
not for wording, implementation trivia, or every reversible edit. Keep tests
hermetic by default; use synthetic fixtures. Separate model and customer connector
integration tests so they require explicit environment setup and sandbox tools.

## Definition of done

Describe the user-visible outcome and observable acceptance criteria. Read the
implementation before describing capabilities. Preserve unrelated work in the
checkout. Implement the smallest complete vertical slice; avoid a UI switch
whose runtime path is absent. Unknown or unsupported capabilities must fail
closed through the shared manifest, including direct API paths.

Attach relevant verification evidence and disclose skipped, partial, or unrun
checks. For defects, include the failing trigger and changed behavior. For schema,
API, persistence or dependency changes, describe compatibility and migration.
For consequential tool operations, implement authorization and idempotency in
code; prompt instructions and tool visibility are not authorization boundaries.
Never relabel scaffolds or local review artifacts as deployable releases.

Use the [merge request template](../.gitlab/merge_request_templates/Default.md).
A local passing result is not a passing remote pipeline. Resolve new warnings
where caused by the change; existing sample warnings need a documented release
policy rather than silent suppression. No global reformat or new type-check
baseline is imposed in this first CI slice; expand those rules in a focused MR.

## Documentation maintenance

Keep one source of integration guidance: [AGENTS.md](../AGENTS.md) routes tasks,
[CLAUDE.md](../CLAUDE.md) imports it, and the
[integration playbook](agent-integration.md) links exact contracts and examples.
[llms.txt](../llms.txt) is the concise machine-readable documentation index;
[llm.txt](../llm.txt) points to it. Update the index when supported workflows or
canonical contract routes change, without copying API details into it.
Do not duplicate the playbook into tool-specific instruction files. External
hosts must explicitly point their coding assistant at the matching ADKL docs;
installing the wheel alone does not install repository instructions.

When changing a public API, default, callback order, CLI, capability or packaging:

1. Update its canonical guide alongside source and native tests. Trace declarations
   through construction and execution; label integration sketches with host inputs.
2. Update README/doc-index routing only when the user workflow changes. Keep API
   details in the contract page; update runnable examples when signatures change.
3. Verify linked local paths with `python scripts/ci/check_docs.py` (also required
   by `python-lint`). This stdlib check covers root Markdown, `docs/` and workflow
   skills, including Claude imports, Console Markdown, and `llms.txt`/`llm.txt`. It ignores fenced examples and external URLs;
   it does not validate anchors, prose semantics, commands or remote availability.
4. Run the affected example/CLI and relevant native contracts. Use fixtures without
   credentials, and report live/host checks separately. For doc-only edits, avoid
   unrelated runtime or Flutter suite expansion.
5. Keep dated reviews and release evidence explicitly historical. Do not update
   old test totals to imply those checks ran again. Point historical gaps to current
   contracts rather than copying their stale status into active guides.

Review agent instructions using [agent design](agent-design.md). Neither static
link checks nor natural-language audit heuristics establish semantic consistency.

## Reusable development skills

These are **coding workflow skills**, separate from the customer-facing ADK
skills in `projects/*/skills`. Canonical files live under `.agents/skills`:

- `adkl-feature-delivery`: implement/review runtime, API and console changes with
  capability and contract evidence.
- `adkl-agent-content`: create/revise agent prompts, skill resources and tool
  declarations with activation and execution checks.
- `adkl-adk-upgrade`: upgrade ADK without confusing native behavior with private
  external application extensions or new configuration declarations.
- `adkl-release-review`: maintain CI and prepare/review release evidence, promotion
  and rollback within the current deployment capabilities.

Codex discovers repository `.agents/skills` from the working directory up to the
repository root. Invoke a named skill explicitly if needed; restart if new files
are not visible. Other assistants can read the same files through `AGENTS.md`;
there is no duplicated `.github/skills` copy to drift.
[Official skill discovery documentation](https://learn.chatgpt.com/docs/build-skills)
describes the supported repository location.

Expected routing examples for review:

| Request | Skill |
|---|---|
| “Implement complete retrieval preview with export parity” | feature-delivery |
| “Design a plan-change skill and its failure cases” | agent-content |
| “Upgrade google-adk and check SkillToolset compatibility” | adk-upgrade |
| “Prepare an RC, diagnose a CI gate, or design rollback” | release-review |
| “Fix a README typo” | No workflow skill needed |

The skills reference these maintained guides instead of repeating tool versions
and CI commands. They add no separate approval barrier and do not authorize an
external deployment by themselves. Follow the user's established task scope.
