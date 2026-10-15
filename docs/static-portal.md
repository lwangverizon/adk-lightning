# Central read-only inspection portal

The static portal is a separate Flutter entry point (`portal_main.dart`) and
container build context, not a multi-user mode for `adkl.server`. It displays an
agent dropdown, saved skill/tool declarations, shared Doctor findings, source
identity and check coverage. Its shell follows the editor console's layout:
top bar, library rail, read-only skill editor (token estimate, outlined fields,
tool rows, Sections/Raw), the Checks panel with category cards, and the same
width budgets with drawer and bottom navigation when narrow. It reuses the
console's navigation, tool inspector, section view and workflow diagrams. As in
the editor, the rail query and the skill/tool/workflow selections persist across
tabs, and a selected skill stays in view on Tools until a tool is chosen. Chat,
project jobs, saving, tool binding and Doctor runs are absent; the local editor
retains its editing behavior. Tool parameters are read from ADK 2.x
`parameters_json_schema`, falling back to `parameters`. It does not run Python, import host code, resolve
factories, poll agents, expose chat or save files. No production-agent changes
are required. The central container serves prebuilt files only.

## Trust and evidence boundaries

- Run export in an **isolated host job**, using the host's locked SDK and exact
  application artifact. Never run it inside a production worker. Factory imports
  and construction can have side effects: `--inspect` is explicit and requires
  restricted network access, sandbox credentials, time/memory limits and a
  reviewed factory. Merely installing the SDK starts no job or connector.
- Without `--inspect`, export reads mapped/native skill files or configured
  project skill files, and runs static Doctor checks. Tools remain unresolved;
  an empty list is not proof that the host has no tools. With `--inspect`, the
  bundle uses the **same native snapshot as Doctor**, without constructing a
  second factory. Dynamic toolsets and instructions remain outside the scope.
- Artifact/SDK identifiers are **publisher assertions**, not attestations.
  Production deployment is always `not_verified`. There is no environment picker
  claiming that an artifact is currently deployed. Profile identifies the export
  context; Doctor still checks saved base files, not environment overlays.
- Freshness is measured at scan time, not continuously. Publish a new snapshot
  after source changes. Failed/skipped checks stay visible; an export succeeding
  does not mean Doctor passed. Neither establishes live behavior or authorization.
- Skill bodies are omitted unless `--include-instructions` is supplied. Skill
  descriptions, tool schemas and finding messages can still contain sensitive
  data. **Review every bundle before publishing. There is no automatic secret or
  PII redaction guarantee.** The assembler requires a publisher acknowledgement.
- No raw agent configuration, environment file, credentials, session data or full
  worker snapshot is copied wholesale. Review also applies to finding text and
  source locations. JSON hashes detect accidental changes relative to trusted
  build evidence; they are not signatures or runtime attestation.

## Export one agent

Use the source/docs corresponding to the SDK installed in the isolated host.
The host factory and source mappings are configured in its `agent.yaml`; no
factory is supplied by a browser or accepted by the portal.

```bash
# In the isolated host environment. Identity values below are placeholders.
uv run --no-sync python -m adkl.portal export /work/host \
  --id prospect --name Prospect \
  --artifact sha256:APPLICATION_IMAGE_DIGEST --sdk-revision SDK_COMMIT \
  --out /work/review/prospect.json

# Optional native declarations: constructs trusted host code in the local worker.
# Add --inspect --profile inspection to the command above only in the isolated job.
```

The destination must not exist and its parent must already exist. Export creates
it with private permissions. Source changes during collection abort export.
Use a separate descriptor/workspace per logical factory: Customer, MVA, Prospect
and Personalization may belong to one application image but need independently
reviewed registrations. Existing application repositories are not modified by
this feature, and those agents are not claimed integrated merely by naming them.

## Registry and versioned publication

Create a private publisher registry. Paths resolve relative to this file:

```json
{
  "agents": [
    {"id": "support", "name": "Support assistant"},
    {"id": "research", "name": "Research assistant", "bundle": "research.json"}
  ]
}
```

Entries without bundles show **No inspection snapshot published**. Agent IDs must
be unique lowercase slugs. Bundles must match the registry ID/name. Publication
is versioned as a complete immutable site; there is no upload endpoint. The
assembler writes only JSON to a new output directory and never invokes a worker.

From the ADKL checkout, with locked Python dependencies and Flutter 3.44.0:

```bash
# Review ALL bundle content before running this publication-preparation command.
bash scripts/build_portal.sh /work/review/registry.json /work/artifacts/portal-VERSION
```

This builds the dedicated portal, disables Flutter's external renderer CDN, and
uses same-origin fallback fonts (bundled Roboto and Noto Sans Symbols with licenses).
Other scripts require publisher-provided licensed subsets and browser checks;
missing glyphs do not justify weakening CSP. The normal editor bootstrap is not
changed. The build records/verifies the existing unsigned web artifact manifest. The output contains
`web/`, `Dockerfile`, `nginx.conf` and `manifest.json`; it contains no host source
or Python environment. The shell script's build overwrites `console/build/web`,
so rebuild the normal console before packaging a normal editor archive.

## Container candidate and access control

The Dockerfile requires an explicit `PORTAL_BASE`: an approved, digest-pinned
nginx-unprivileged image compatible with UID/GID 101 and `/etc/nginx/mime.types`.
There is no implicit private registry or mutable-tag default. Select and scan the
image for the target architecture before building. A public source is
`docker.io/nginxinc/nginx-unprivileged`; resolve a reviewed version to its digest
rather than relying on a moving tag. Hosts may use an approved mirror instead.
Registry credentials belong in the builder's credential helper, never build args.

```bash
# Set PORTAL_BASE to your approved registry/name@sha256:DIGEST first.
: "${PORTAL_BASE:?Set an approved digest-pinned nginx-unprivileged image}"
docker build --platform linux/amd64 --build-arg PORTAL_BASE="$PORTAL_BASE" \
  -t adkl-static-portal:VERSION /work/artifacts/portal-VERSION
# Local review only. Production ingress must be authenticated and private.
docker run --rm --platform linux/amd64 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=32m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 128m --cpus 1 --pids-limit 100 \
  -p 127.0.0.1:8788:8080 adkl-static-portal:VERSION
```

The container runs as non-root, exposes GET/HEAD static files, denies mutation
methods, has no `/api/` routes and provides `/healthz`. It does **not authenticate
users itself**. Before any shared deployment:

1. Place it behind the organization's SSO-authenticated gateway, protecting both
   assets **and every `data/` request**. Strip untrusted auth headers as appropriate
   for that gateway. TLS terminates there; prevent any direct origin access with
   network policy/private ingress. Loopback publishing is only local review.
2. Treat one portal dataset as **one authorization audience**. Everyone allowed
   into it can read every published agent. A dropdown is not an ACL. Deploy
   separate sites/datasets for different audiences; per-agent RBAC is not yet
   implemented. Do not ship restricted snapshots to a common static site.
3. Enforce request/connection limits, gateway audit logging without response bodies,
   retention, and approved network policy. No egress to production agents is needed.
4. Scan the base and final image, retain the portal source revision, locked input
   manifests and approved dataset identity. Build once, sign/publish by digest,
   test in isolated staging, promote that digest, and retain the prior image for
   rollback. Never put secrets in Docker arguments or the prepared build context.
5. Test anonymous denial, expired sessions, direct-origin denial, all data paths,
   no mutation APIs, agent switching, CSP/renderer loading, and rollback through
   the actual gateway. Container/static UI tests alone do not establish these.

This is a **deployment candidate**, not production certification. No infrastructure,
SSO configuration, registry publication or production deployment is created by
these commands. The repository's container base/scanner availability and staging
execution evidence must be recorded separately; see [delivery](ci-cd.md).

## Build prerequisites and troubleshooting

The SDK merge does not create a deployed service or upgrade any host SDK. Build
from the chosen ADKL revision, then promote a reviewed site/image separately.
The four example agent entries remain unavailable until actual snapshots are
exported and approved in their host environments.

Before building the container, `docker version` must show both Client and Server,
and `docker buildx version` must succeed on a BuildKit builder. A registry manifest
lookup works without a running Docker daemon; it does not prove a container can
build or start. If the configured Docker Desktop socket is unavailable, start or
repair the local engine, or use an approved CI builder. Do not modify an existing
production service to prepare this portal. Provision access to the explicitly
selected base image's registry.

Container execution, image scanning and authenticated-gateway validation remain
separate operator checks.

## API/data contract

The read-only UI fetches `data/registry.json`, then the selected same-origin JSON
filename. It cannot load arbitrary host URLs. Selection is client-local and
clears prior data; late responses from another agent are discarded. There is no
process-global selected workspace or shared chat session.

Bundle v1 contains `agent`, `identity`, `exportedAt`, `report` (the shared Doctor
contract), `inventory` (scope, agents, skills, tools, inclusion flags and limits),
and disabled execution capabilities. With `--include-instructions`, skill entries
include both `instructions` and their parsed `doc`; otherwise both are null.
Optional `inventory.workflows` contains native capability declarations and
`workflowGraphs` from the shared inspection snapshot, including agent/binding
owners, nodes and routed edges. Older snapshots without topology remain readable
and explicitly show topology unavailable. No node execution or closure discovery
is performed. Host-owned graphs can be declared with
`adkl.inspection.attach(agent, skills=[...], workflows=[workflow])`; that API does
not install execution callbacks. Activation workflows not explicitly attached
remain outside this inventory.

The build script emits the same source digest as compiled `ADKL_PORTAL_BUILD`
and registry `uiBuild`. It is visible in the UI; a mismatch requests manual reload.
A snapshot HTTP 404 triggers exactly one registry refresh and one same-agent
retry. A removed agent, persistent 404, 403 or 500 never silently substitutes
another agent. Mutable requests revalidate, and nginx retains `no-store` for
all files. This is bounded rollover recovery, not atomic multi-instance hosting;
retain compatible publications during rollouts where possible. Already-loaded
older JavaScript cannot acquire this recovery behavior without reload.

Native entries retain agent/binding owners
rather than flattening duplicate tool names. The publication manifest hashes
all site files; validate it before image construction. Serving is read-only and
does not re-audit bundles or assert publisher-supplied findings are trustworthy.
