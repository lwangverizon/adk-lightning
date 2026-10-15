# Create, inspect and package agents

After attaching an external factory, run `python -m adkl.projects doctor /path/to/host`.
It checks the descriptor, execution profile, interpreter and dependency files
without importing host code or reading secrets. A ready result means local
inspection prerequisites exist; it does not verify dependency compatibility or
execute a model. Native agents use the original Skills/Tools sidebar, skill
editor and dedicated Findings panel. Choose Selected item or All agent issues.
Explicitly mapped skill files support editing; unmapped native skills and Python
tool implementations remain read-only. Project inspection
also exposes callback order. Test exposes shared widget diagnostics and
the last locally composed root-agent request. See [native bindings](runtime-bindings.md).

Use **Set test context** in chat to supply a JSON object of synthetic scenario
state, such as a page or prepared domain context. Applying it clears the current
conversation; the object is applied to every subsequent turn. Agent and execution
profile switches clear it. This is local test input, not an authenticated host
session. The inspector uses a disposable ADK runner; separately test the host's
transport, identity checks, persistence and production entry point.

## Map native skills for editing

After attaching the factory, add source mappings to the host's descriptor:

```yaml
version: 1
agent:
  name: tutor
  factory: agent:build
authoring:
  skills:
    math: skills/math/SKILL.md
```

If the server imports other modules before the factory (for example `api`),
list them as `agent.startup: [api]` so inspection follows the serving import
order; see [Doctor serving import order](doctor.md#serving-import-order).

Each path must identify the actual file loaded by the factory, stay inside the
host root and avoid symlinks. The console checks its name, description, procedure
and tool bindings against the runtime declaration. The mapping grants editing of
that skill's description, procedure and tool bindings; it does not authorize
rewriting arbitrary Python or installed SDK files. Register each intended skill.

Saves require the source revision returned when the editor opened it. Conflicting
edits are refused even with a lint override. Metadata is preserved; body-only edits
retain the original frontmatter. Saving resets the disposable worker and chat,
then reloads native declarations and findings. Regenerate the host's embedding
index after retrieval text changes, and run its behavioral tests before deployment.
The [native example](../projects/native-agent/README.md) includes this mapping.

This release targets local authoring and customer-owned native ADK deployment.
It supports an empty-workspace creation flow and inspection of existing ADK
factories. The generated bundle is a runnable handoff to a host; it does not
provision cloud infrastructure or certify production access controls and outcomes.

ADKL is consumed by applications in their own repositories. Its `projects/`
directory holds [minimal maintained examples](../projects/README.md) and ignored
projects created locally by business users. Creation defaults to that workspace;
choose your own workspace with the server's `--projects` option. Start a separate
Git repository for a real use case. SDK CI never scans user-created project
directories. Existing applications can attach and open a native factory using
the workflow below; copying their source into ADKL is unnecessary.

For factory agents, `/api/agent` reports `mode: factory` and null skill/tool
counts. The configuration view directs authors to explicit Project inspection;
it does not start a factory just to populate a summary. Inspection describes
declared inventory rather than claiming complete dynamic tool availability.
It also reports orphaned or mismatched `authoring.skills` source mappings.
Unmapped skills remain valid, read-only native content.


## Install the SDK and console

Maintainers build the slim SDK and a separate console archive from this checkout:

```bash
cd console
flutter pub get --enforce-lockfile
flutter build web --release
cd ..
uv run --no-sync python scripts/build_sdk.py --console-archive --sdist
```

The required `sdk-onboarding` CI job provides the slim wheel, source distribution
and `adkl_console-<version>.zip` under `dist/release/`. Protected tags retain
these files through the [SDK release process](sdk-releases.md). Neither Python
distribution contains console assets, project content or credentials. The console
ZIP contains a `web/` directory and is covered by the release checksum manifest.

For an existing external ADK application that only needs the SDK, build a plain
wheel from its pinned ADKL checkout and add it to the host lockfile using the
[checkout wheel workflow](agent-integration.md#consume-a-pinned-main-checkout-today).
`python scripts/build_sdk.py` remains the slim-wheel-only build command.

In a separate environment, install the wheel, extract the console and launch:

```bash
uv venv .venv
uv pip install --python .venv/bin/python /path/to/adk_lightning-0.1.0-py3-none-any.whl
unzip /path/to/adkl_console-0.1.0.zip -d ./console-assets
.venv/bin/python -m adkl.server --root ./workspace \
  --web ./console-assets/web \
  --sdk-wheel /path/to/adk_lightning-0.1.0-py3-none-any.whl
```

The console is served at `http://127.0.0.1:8787`.
Use `--projects /path/to/projects` to choose where new folders are created.
The workspace owner supplies uv on PATH and a trusted wheel; no Flutter or
customer source repository is required on the author's machine.

The legacy `--with-console` build option still embeds assets for local use and
serves them automatically. Release validation requires the slim SDK and separate
ZIP; it rejects combined wheels. Existing combined installations keep working.

## Business-author workflow

1. Choose **Create agent**, enter a name, purpose and Gemini model.
2. The UI creates and selects a new folder containing `agent.yaml`, an instruction,
   one `assist` skill, an empty domain-tool catalog, common presentation tools,
   shared history/output settings, an independent dependency definition and local
   profile, a native ADK `agent.py` entry point and explicit release inputs.
3. In **Project**, choose **Prepare runtime**. This explicit background job vendors
   the supplied wheel, creates the project's initial uv lock, then installs with
   `--locked`. Later preparation preserves that lock and refuses silent SDK replacement.
4. Provision the local Google API key using **Save local credential** or the
   project's declared environment. The UI stores it in the local `.env` with
   owner-only permissions; it never returns the key. Other providers/secrets use
   the project's execution profile. Credentials are excluded from bundle inputs.
5. Edit the skill, test the agent, and use **Run audit** / **Inspect runtime**.
   Inspection constructs the actual agent in its worker and reports its model,
   callbacks, declared tools/toolsets, hierarchy and source identity. It does not
   execute tools or enumerate state-dependent toolsets outside a turn.
6. Choose **Build deployment bundle**. The Project panel gives the local ZIP path
   and the selected project's `/api/project/artifact` download URL.

Project creation is deterministic from the authored purpose. It does not invent
business connectors, generate verified domain knowledge, or turn an arbitrary
description into tested business logic. The single-skill starter needs no vector
index. Additional specialized skills, embedding retrieval and typed state bindings
use the existing SDK configuration and embedding lifecycle.

CLI equivalents:

```bash
python -m adkl.projects create ./projects meeting_helper --purpose "Summarize supplied meeting notes."
python -m adkl.projects prepare ./projects/meeting_helper --wheel /path/to/adk_lightning-0.1.0-py3-none-any.whl
python -m adkl.audit ./projects/meeting_helper
python -m adkl.release build ./projects/meeting_helper --output ./meeting-helper-release.zip
python -m adkl.release verify ./meeting-helper-release.zip
```

## Where the console connects

Installing the SDK does not start a server, mount routes in the host application,
or install console assets. Agent owners launch the separately packaged console
using the [installation steps](#install-the-sdk-and-console); its default address
is `http://127.0.0.1:8787`.

| Task | Current connection | Evidence boundary |
| --- | --- | --- |
| Inspect a host agent | Local worker imports the registered factory using the host profile | Constructor declarations, not a live server snapshot |
| Test a conversation | Disposable local Runner and in-memory sessions | Local execution, not host authentication, persistence or traffic |
| Edit a native skill | Explicit source mappings in the host checkout | Revision-checked source changes; deployment remains host-owned |
| Examine a deployed run | Host's existing monitoring and session tooling | Remote live console attachment is not implemented |

### Central static inspection

Use the [static portal](static-portal.md) for an agent dropdown over offline
inspection snapshots and Doctor reports. It imports no host code while serving,
requires no production connector and does not support chat or editing. Export
runs separately in an isolated host job; publication requires content review and
an authenticated gateway for a single authorized audience.

### In-service declarations and future live-run inspection

For implemented read-only declarations in an existing service, use
[embedded inspection](embedded-inspection.md), or the separate
[connected gateway](connected-inspection.md) when that topology is needed.
Neither attaches the Console to production conversations.

A future extension could expose permitted live run records under the host's
identity and tenant controls. The host would retain its Runner, sessions,
authentication and authorization; editing and test execution would require
separate approval. This live-run/admin surface is not implemented.

Do not deploy the development server as that future service. Remote profile
declarations do not implement a transport. Continue using local factory
attachment below for disposable testing and host-owned monitoring for production.

## Existing ADK applications

Adopt [shared callbacks or runtime bindings](adoption.md) in the host's normal Python factory.
To inspect that exact construction, expose a synchronous zero-argument factory
returning a native ADK `BaseAgent` or `App`:

```python
def build_for_inspection():
    return build_existing_agent()  # same constructor and callback adapters as the host
```

Register it explicitly in the host project (refuses to overwrite `agent.yaml`):

```bash
python -m adkl.projects attach /path/to/existing-project --factory my_agent:build_for_inspection
python -m adkl.server --root /path/to/existing-project
```

The host must already have its independent `pyproject.toml`, `uv.lock`, `.venv`
and an [execution.yaml profile](environments.md) declaring its interpreter and
secrets. Host imports occur only in the project worker. Inspect the actual native
objects in **Project**, then run local conversations in **Test**. Dynamic prompts,
callbacks and tools remain owned by that factory. Static skill audits explicitly
report descriptor-only evidence, and the console refuses feature edits that
would be ignored by the host's Python constructor.

This uses a disposable local Runner/session service, not the host's production
Runner, database, authentication or traffic. It does not attach to remote live
services or auto-convert arbitrary Python into editable YAML. Factory code is
trusted local code and may have its own import/construction side effects. Configure
approved test credentials and sandbox tools in that project's profile.

Workers invalidate conversations when project Python, Markdown, YAML, JSON,
TOML or lock inputs change, including edits outside the UI. Their `sourceDigest`
excludes secrets, runtime state, vendor and build output; it is diagnostic identity,
not a complete signed release manifest. Clear reloads new source. SDK-installed
code/dependency upgrades still require environment synchronization and Clear.

## Bundle and deployment boundary

`release.yaml` explicitly lists files/directories to include. Packaging rejects
secret/runtime paths, symlinks, missing inputs and oversized files. It audits the
packaged tree and checks its dependency lock offline, so validation cannot succeed
by finding an omitted prompt in the original checkout. Arbitrary secrets embedded
in otherwise allowed source files still require source review and scanning;
filename filtering cannot detect them all.

The ZIP contains the project, vendored SDK wheel, pinned Python/uv base-image
Dockerfile, native ADK API-server command, deployment notes and SHA-256 inventory.
Verification rejects altered, extra, missing and duplicate members. The hashes
are unsigned integrity evidence; retain the manifest through a trusted channel.

Build the image in your host environment and provide runtime credentials there.
The native ADK API serves on port 8080 as a non-root user. Its endpoints are
unauthenticated by default: the host must supply its authenticated gateway, user
authorization, durable sessions where needed, resource limits, observability,
staging evaluation and rollback. Override the server command to configure native
ADK's session-service URI when integrating persistent storage. The artifact does
not claim those controls are present: `productionReady` is false and behavioral
evaluation remains `not_run` until separately established by the host.

Existing host-factory inspection descriptors are deployed by their existing
application workflow. The bundle path serves ADKL project factories; it is separate
from the older generated-code `eject` path, whose parity guards remain intact.
