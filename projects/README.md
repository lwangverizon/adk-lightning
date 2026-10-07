# Example and user projects

ADKL is an SDK and development console. Applications own their agents, domain
skills, tools, dependencies, data and deployment in their own repositories.
They install ADKL and can open their local agent in the console without copying
the application into this repository.

Two small examples are maintained here:

| Example | Demonstrates | Model credentials |
| --- | --- | --- |
| [staged-ui](staged-ui/README.md) | Editable YAML skill, shared presentation pack, staged step card and history slicing | Google API key for interactive chat |
| [native-agent](native-agent/README.md) | Host-owned ADK constructor, all four shared patterns, staged A2UI v0.9/v1.0 rendering and console inspection | None; scripted model and embeddings |

These are teaching fixtures, not application templates with production identity,
storage or business services. The native example has one skill and synthetic
embeddings; it demonstrates integration mechanics rather than retrieval quality.
The console's `?a2ui-gallery=1` route exercises all 18 basic primitives. See the
[support boundaries](../docs/a2ui-support.md).

The console also creates user projects here by default. Those directories are
ignored by this repository's Git configuration. Give each real use case its own
repository, lockfile and release process. CI reads only [examples.json](examples.json);
it never imports or validates arbitrary user-created projects. Only explicitly
maintained examples should be added to that manifest and the Git allowlist.
Project folders, credentials and local environments are excluded from the SDK
wheel; examples are available from the source checkout.

## Existing applications

Follow the [integration playbook](../docs/agent-integration.md) and
[native bindings contract](../docs/runtime-bindings.md). Use `adkl.projects attach`
in the application checkout and launch the console with `--root` pointing there;
the factory executes in that project's own configured environment.

Application snapshots and application-specific end-to-end tests are not included
in this public candidate or its Git history. They belong to their external
repositories; SDK tests use synthetic fixtures and the two examples above.
Ignored local data from other checkouts is not migrated or deleted automatically.
