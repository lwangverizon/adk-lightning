# Contributing to ADKL

Help make reusable ADK patterns easier to adopt, inspect and improve. Useful
contributions include native runtime fixes, focused authoring improvements,
reproducible agent fixtures and clearer documentation.

## Report a problem or propose a change

Use the issue tracker of the repository you have access to. A canonical public
destination is not yet designated; see [community release gates](docs/community-release.md).
For suspected vulnerabilities, follow [private reporting](SECURITY.md), not a
public issue. Please follow the [conduct policy](CODE_OF_CONDUCT.md) and
[maintenance process](GOVERNANCE.md).

For a bug, include the ADKL commit or package
version, Python/ADK versions, the command or workflow, expected and actual behavior,
and a minimal synthetic reproduction. For console issues, include the Flutter
version, browser and relevant viewport size. Redact logs and omit credentials,
private prompts and customer data.

For a feature, describe the application need, the native ADK integration point,
and an observable acceptance case. Discuss public API changes before implementing
them; prefer a small reusable contract over application-specific behavior.

## Before you start

Read the [overview](README.md), [architecture map](docs/architecture.md),
[development guide](docs/development.md) and
matching workflow instructions in `AGENTS.md`. Describe the user-visible behavior
and its smallest complete acceptance case. Keep application-specific business
logic in host projects and reusable mechanics in the library.

## Changes and evidence

1. Work on a focused branch and preserve unrelated changes.
2. Use locked dependencies. Keep base authoring dependencies lightweight.
3. Add behavioral tests where regressions matter; use synthetic data and models
   so routine checks do not require private accounts or systems.
4. Keep configuration, runtime, preview and export claims aligned. Document
   partial support and compatibility changes explicitly.
5. Submit a merge request with the outcome, checks and material limitations.
   Ordinary pipelines on the private origin are currently off by default; follow
   the [pipeline policy](docs/ci-cd.md#current-pipeline-policy). A verification-only
   [community configuration](docs/community-release.md#community-ci-candidate)
   is available for a separately provisioned project; its presence does not mean
   runners or merge controls are configured. Record local checks and
   owner-approved exceptions explicitly; an absent pipeline is not a CI pass.

For UI changes, run analyzer/widget tests, build the release console and inspect
the changed workflow. For ADK changes, test real native objects and callback
ordering. See [delivery](docs/ci-cd.md) for release review.
For documentation, follow the [maintenance checklist](docs/development.md#documentation-maintenance)
and verify both human navigation and the [coding-agent integration path](docs/agent-integration.md).

Do not contribute credentials, private integration reports or material whose
redistribution terms are unknown. Contributions are accepted under the
repository's [Apache-2.0 license](LICENSE); contributing does not change
third-party ownership of anything you bring with you.
