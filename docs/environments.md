# Project execution environments

ADKL separates the authoring interpreter from each project's execution interpreter.
Use the repository `uv.lock` for platform development and an independent
`pyproject.toml`, `uv.lock`, `.python-version` and `.venv` for each runnable project.
They are independent uv projects, not members of one shared dependency workspace.
Preview requests never resolve or install dependencies. The explicit Project →
Prepare runtime action starts a separate background setup job using the workspace
owner's trusted SDK wheel. It creates the initial project lock and then uses
locked installs; see [project onboarding](project-onboarding.md).

## Current behavior

The development server reads configuration and catalogs without importing project
callbacks or tools. Chat starts a local worker using the selected project's
`.venv/bin/python -I`. That worker owns its imports, current directory, model
credentials, conversation and one persistent asyncio event loop. Its JSON-line
responses include project/profile identity and hashes of the lock, profile and
agent configuration. These hashes identify inputs; they do not attest that the
installed environment matches its lock or constitute a complete release manifest.

The Test panel's execution selector switches profiles and clears the conversation.
Project switching, feature/skill saves and Clear terminate the current worker.
Lock/profile/agent changes made outside the console block subsequent requests
until Clear reloads configuration. After editing Python, dependencies or secrets,
run the relevant sync command and Clear before testing again.

| Project/profile | Execution today |
|---|---|
| Minimal `native-agent` example | Independent Python runtime; scripted model, no credentials |
| Minimal `staged-ui` example | Independent Python runtime; Google API key for Gemini |
| Host-restricted remote profiles | Remote target declarations; local execution refused before imports or secret reads |
| Project without `execution.yaml` | Authoring only through the console |

Remote declarations have no transport yet. They do not detect VPN membership,
connect to a host network, or grant access. Existing feature capability checks still
apply independently of the execution profile. The server remains a single-user
development service with a global selected project and serialized requests.

Local subprocesses prevent accidental import and environment collisions. They
are **not a security sandbox**: processes run as the same OS user, can access
that user's files and available network, and share the project content directory.
Use approved customer-hosted infrastructure for restricted workloads. A user who
can edit trusted project configuration can change its target; this is configuration
validation, not an authorization boundary or enforced network policy.

## Configure a local runtime

### Runtime content and stale conversations

When code or tracked content changes, the worker refuses subsequent requests.
HTTP requests return 409 with `code: stale_conversation`, `recoverable: true` and
`action: clear_conversation`. A stream that has already started carries those
same fields in its terminal error event. The Test panel offers **Clear conversation**,
which discards the old transcript and reloads the worker. Other execution failures
do not promise that clearing will fix them.

By default, the digest conservatively includes project Markdown, Python, YAML,
JSON, TOML and lockfiles. Factory code can read arbitrary Markdown, so excluding
it automatically would risk running stale prompts. A host that has inventoried
its runtime content can opt into explicit content tracking in `execution.yaml`:

```yaml
source:
  content: [prompts, curriculum/rules.txt]
```

These are project-relative files or directories, without globs. In this mode,
unrelated Markdown such as migration notes is ignored. Python and structured
configuration files remain tracked. Declared instruction templates, all slot
variants, file-backed prompt state, mapped skill directories, the declarative
skill tree and any discovered `SKILL.md` directory (including resources) remain
tracked regardless of `content`. Additional listed content is hashed regardless
of extension. Additions, changes and removals change the identity.

The host must list any other factory-read content, including non-Markdown files.
Unlisted arbitrary file reads cannot be discovered reliably. Omitting `source`
retains conservative Markdown tracking; `content: []` is an explicit assertion
that no additional content paths are needed. Hidden/runtime directories, vendored
artifacts and profile credential files remain excluded, as is any directory
holding a PEP 405 `pyvenv.cfg`: an in-project virtualenv is installed
dependencies under whatever name it carries, and every `uv run` rewrites it.
This identity is a development reload guard, not a complete build attestation.

A project that writes state into an ordinary-looking path — a refreshed OIDC
token at `oidc_token.json`, a warm cache directory — can declare those bytes as
state rather than source. Nothing else infers this: the file is neither
dot-prefixed nor a declared `env_file`, so the walk hashes it, and a refresh
from another terminal changes project identity mid-conversation.

```yaml
source:
  exclude: [oidc_token.json, cache_dir_named_without_a_dot]
```

`exclude` takes the same project-relative paths as `content`, without globs, and
an excluded directory is not walked at all. It is a separate question from
`content`: declaring `exclude` alone leaves conservative Markdown tracking on,
and overlapping content/exclude paths (including ancestors) are refused rather
than resolved by check order.

A symlink inside the project is content sharing, which hosts use to share one
skill's assets across variants. The digest records the link path and its target
without following it, since the walk hashes that target at its own path in the
same pass; re-pointing the link therefore still changes identity. A tracked link
whose target this walk never reads — including explicitly excluded paths,
credential files, virtualenvs and untracked extensions, as well as one leaving the
project or resolving into a hidden, vendored or build directory — is refused by
name, because content the digest cannot see must not change underneath it.

From the ADKL repository root:

```bash
uv sync --locked
uv run --no-sync python scripts/build_sdk.py
uv run --no-sync python -m adkl.projects prepare projects/native-agent --wheel dist/adk_lightning-0.1.0-py3-none-any.whl
uv run --no-sync python -m adkl.server --root projects/native-agent --web console/build/web
```

Build the console using [authoring setup](authoring.md) before serving it.
The native example needs no credentials. For a model-backed project, an ignored
`.env` can supply the Gemini key names declared by its profile, as below.
Provision that file separately; keep it mode 0600. Do not copy an entire customer
`.env`, credentials or private package setup into ADKL. The platform root `.env`
and arbitrary ambient shell variables no longer supply project credentials.

```yaml
version: 1
default: local
profiles:
  local:
    target: local
    environment: development
    python: .venv/bin/python
    env_file: .env
    env:
      GEMINI_MODEL: gemini-3.6-flash
    secrets: [GOOGLE_API_KEY, GEMINI_API_KEY, GOOGLE_AI_API_KEY]
    inherit: []
```

`env` contains nonsecret string configuration. Only names listed in `secrets`
are read from `env_file`; other dotenv entries are ignored. Dotenv values are
literal, with optional matching quotes, without shell expansion. `inherit`
explicitly opts named secrets or approved transport settings into shell
inheritance; those values override the file when present. Optional transport
names include `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`,
`SSL_CERT_DIR` and `REQUESTS_CA_BUNDLE`. Do not place proxy credentials inline in
configuration. Python injection and loader environment variables are rejected.
The launcher constructs a minimal PATH and project/profile-specific HOME and
TMPDIR under ignored `.adkl/<profile>/`; `ADKL_STATE_DIR` exposes that directory
for integrations that need local storage. Secrets are read transiently by the
launcher and passed only to the child environment, without mutating `os.environ`.
Known declared secret values are redacted from worker responses; this does not
protect against malicious code or transformed/encoded secret disclosure.

Project paths for dotenv/state and local `tools.repo` must remain inside the
project. A Python launcher must live under `.venv/` (ordinary venv interpreter
symlinks are supported). Pin and install private dependencies only in their
approved execution environment. Lockfiles are not copied between projects.

The examples have no persistent business database. Applications own storage,
identity and schemas. If an integration needs persistence, declare its database
secret and provision a dedicated database; relative file paths should point to
that profile's state directory. Rendering an A2UI control does not implement a
business submission endpoint.

## Migration and verification

Existing custom runnable projects need an execution profile and their own locked
runtime before console chat can run. Nonsecret settings previously stored in
`.env` must move to `env`; declare each required secret explicitly. Projects that
use an external `tools.repo` must package tools with their worker/project instead
of importing another checkout into the authoring process. Low-level Python
ChatSession callers remain responsible for their own process isolation; remote
profiles are also rejected by ChatSession and live embedding commands.

```bash
# Native example under its own installed SDK; no model calls.
projects/native-agent/.venv/bin/python -I projects/native-agent/verify.py
# Platform process isolation and capability regressions.
uv run --no-sync python -m pytest tests/test_execution.py tests/test_capabilities.py -q
```

CI validates only the maintained example manifest. Installed-wheel onboarding
copies the native example to an external temporary directory, prepares its own
lock and checks actual tool turns and staged A2UI rendering. Application-specific
end-to-end suites belong to the consuming repositories. Live Gemini smoke tests require
explicit local credentials and are separate from deterministic CI.

## Next delivery boundary

Customer-hosted execution needs an authenticated worker protocol, per-project
workload identity and secret-manager bindings, enforced egress policy, private
registry access confined to that environment, and immutable runtime images.
Bind requests and results to platform/agent/tool revisions, capability policy and
image digest. Keep student/customer state in separate stores by environment.
Add readiness probes, cancellation, resource limits, redacted observability and
integration tests on approved runners. Only then enable remote targets and
promotion through the protected environment process in [ci-cd.md](ci-cd.md).
