# Embedded agent inspection

Expose approved declarations from the native agents your application registers,
without copying a host-specific collector or running the development server.
`InspectionRegistry` owns bounded snapshots and generation lifecycle;
`mount_inspection` optionally adds authenticated read-only FastAPI routes and a
separately built connected Flutter portal. This is a Python API, not a YAML
runtime feature or a new inference callback.

**Current means the latest explicitly published generation in this process.**
It does not mean latest Git source, a final per-session model request, or every
replica in a deployment. The host asserts that the supplied root is serving.
The SDK does not find, construct, replace or watch your Runner.

## Minimal host integration

Use your existing native agent/App and authentication dependency:

```python
from adkl.embedded import InspectionRegistry
from adkl.inspection_fastapi import mount_inspection

inspection = InspectionRegistry(
    environment=host_environment,
    release=immutable_host_release,
    content_approved=True,  # Explicit approval for the actual content/audience.
)
registration = inspection.register("assistant", runner.agent, name="Assistant")

mount_inspection(
    fastapi_app,
    inspection,
    authorize=authorized_inspection_agents,
    enabled=True,
    prefix="/adkl",
    # Optional immutable connected Flutter web build, not the developer console:
    # portal_directory="/opt/adkl-connected-web",
)
```

`authorized_inspection_agents` is a required FastAPI dependency. It validates
identity using the host's existing session/SSO mechanism and returns a `set` or
`frozenset` of permitted agent IDs, or raises `HTTPException`. Empty/invalid grants
are denied. Never trust a client-supplied agent list or unvalidated identity
header. FastAPI nested dependencies and asynchronous dependencies are supported.
Authorization runs on every read, including cached declarations and all assets.
The portal sends no service credentials: use a same-origin authenticated session
or a trusted proxy that strips and replaces its identity headers.

SDK installation starts no service. Mounting defaults to `enabled=False` (no
routes or file reads); content publication separately defaults to disabled.
FastAPI is imported only when mounting; the host supplies it (also available in
the locked ADKL runtime environment). Base authoring remains stdlib + PyYAML.

Run the [credential-free example](../examples/embedded_inspection.py):

```bash
uv run --no-sync python -m examples.embedded_inspection
```

The example uses real native ADK objects and in-process FastAPI requests, not a
live model, business tools or a network server. Its fixture identity is not a
production authentication recipe.

## Lifecycle contract

- `register(id, root, name=...)` captures and activates an **already registered**
  native agent, workflow root or App. It returns an immutable `Registration`
  handle containing `agent_id` and `generation`.
- For startup before a Runner exists: `handle = prepare(id, root)` captures but
  does not publish; `activate(handle, root)` confirms the exact same object after
  host registration. Consistently pass `runner.agent` or the same App object.
  The SDK does not independently attest which Runner uses it.
- A new `prepare` immediately revokes the old snapshot before collection. Failed
  collection leaves the ID unavailable, never serving stale content as current.
- `invalidate(handle)` retires only that generation. An old runner's delayed
  shutdown cannot remove a newer generation. `clear()` retires everything on
  process shutdown, including pending preparations.
- Before mutating a registered object, invalidate its handle. Serialize mutation
  in host code, then register the resulting root again. There is no mutation
  watcher, file/Redis polling or automatic in-place refresh.
- Roots are weakly referenced. Dropping the host's last reference makes publication
  unavailable. No Runner, session or customer state is retained.
- A stale handle cannot activate or retire a newer registration. A wrong root for
  the current handle revokes its prepared data. Concurrent late preparation cannot
  resurrect content after retirement/replacement.

Registration failure raises `InspectionUnavailable` with a generic message.
If inspection is optional, the host can catch that exception during startup and
continue inference; do not retry a partly executed agent turn. Collection itself
is synchronous trusted preparation work: run it before readiness or in a
controlled host update, not inside a request/model callback. The host must
prevent simultaneous mutation of the object graph while it is being collected.

## Routes and browser

Under the chosen prefix (default `/adkl`):

| GET route | Response |
| --- | --- |
| `/api/v1/agents` | Registry filtered to the caller's grants |
| `/api/v1/agents/{id}/inspection` | Connected UI observation envelope and declaration snapshot |
| `/api/v1/agents/{id}/declarations` | Pre-encoded declaration bytes with ETag |
| `/`, published asset paths | Optional connected Flutter web build |

Unknown/ungranted content is not disclosed. Granted but unpublished roots return
503. No API accepts factories, module paths, session IDs or refresh commands.
Queries/ranges are refused. There are no chat, editing, execution or upload routes.
Data/assets use `no-store`; the host must also prevent caches of error responses.
Mount before any catch-all host route and reserve the prefix for inspection.

Build the existing connected UI from the matching SDK revision:

```bash
cd console
flutter pub get --enforce-lockfile
flutter build web --release --target lib/connected_main.dart \
  --base-href /adkl/ --no-web-resources-cdn --pwa-strategy=none
cd ..
uv run --no-sync python scripts/prepare_connected_web.py \
  --web console/build/web \
  --font-directory "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts"
```

Set `FLUTTER_ROOT` to the installed Flutter SDK directory. The preparation step
bundles Flutter's Roboto fonts/license and the repository's licensed Noto Sans
Symbols subset, and directs fallback to same-origin assets. It performs no
network downloads. Run it after each build, before hashing/publishing. Flutter
currently deprecates `--pwa-strategy`; the adapter also refuses service-worker
files. Clear any old service-worker registrations on a reused origin.

Copy the **entire** `build/web` directory into an immutable, reviewed artifact and
pass that directory to `portal_directory`. Use the externally visible prefix in
`--base-href` when behind a reverse proxy. The UI resolves API paths relative to
that base. A root-hosted connected gateway continues to use `/api/v1/...`.
The SDK wheel does not bundle Flutter assets. A static offline portal build or
local developer console build is not interchangeable with this connected build.

The adapter bounds/allowlists files at mounting, rejects symlinks and excludes
source maps, hidden files and service workers. It does not certify artifact
integrity or content: verify artifacts in your release pipeline and mount them
read-only. Do not put private JSON or unrelated application files in that directory.
All authorized viewers of that mount can read its static assets, regardless of
their individual agent grants. Avoid remote fonts/assets and check actual browser
CSP/rendering in the host deployment.

The UI uses manual refresh. Observation receipt time advances on reads;
`capturedAt`, generation and digest change only on explicit republication.
Already displayed content cannot be remotely revoked without another request.
No browser persistence is introduced.

## Disclosure and execution boundaries

The collector reads stored native agent/Workflow children, static instructions,
native skills and declared tools, and supported RuntimeBindings/CapabilityBindings
inventories. Workflow nodes are reachable declarations, not a full route graph.
Dynamic instructions, callbacks, toolset enumeration and tools are **not invoked**.
Only exact native `FunctionTool` schema generation is allowed during preparation;
subclass/custom providers remain `not_collected`. Native schema generation can
resolve trusted Python annotations. Unsupported schema extensions fail closed.

The v1 `adkl_loaded_declarations` payload reuses the closed
[connected protocol](connected-inspection.md). Skills/tool schemas can contain
sensitive information; explicit host disclosure approval is not automated
redaction. Source resources/spans, private model/binding configuration, execution
traces and per-session tool availability are omitted. Doctor and behavioral
checks are explicitly not run. Declaration visibility does not authorize tools.

The existing separate gateway remains UAT-only with its fixed upstream URL
contract; this feature does not relax it or automatically configure a gateway.
The embedded host API accepts its explicit environment label, but supplies no
production identity system or deployment approval.

## Limits and adoption

- At most 32 registrations per process and 4 MiB encoded bytes per registration.
- Up to 128 graph nodes, depth 16, 256 items per container, 8,192 collection items,
  and 512 KiB per text field; schema validation adds document-depth/count limits.
- Raw declaration GETs return prepared bytes. UI envelopes deserialize and encode
  those bytes but never traverse agents or rebuild schemas on reads.
- Memory, preparation CPU and HTTP/static traffic share the application's process.
  These are not hard CPU/RSS isolation guarantees or a zero-overhead claim.
- Host ingress owns TLS, SSO/session expiry, direct-origin denial, rate/connection
  limits, logging redaction and any stronger isolation. There is no default public
  or VPC-wide access mode. Multi-process/fleet aggregation is not implemented.

Host adoption is a **separate migration**: replace existing collector/lifecycle
routes incrementally using a pinned SDK wheel while retaining host-owned agent
selection, authentication and serving. Static snapshots are not live registrations.
Installing the SDK does not upgrade or deploy a host application.

Regression coverage: [native/lifecycle/HTTP tests](../tests/runtime/test_embedded_inspection.py),
[connected protocol tests](../tests/test_connected.py), import-layering checks and
connected Flutter tests. Synthetic checks do not establish host rollout, browser
visual acceptance, production load impact or private-ADK compatibility.
