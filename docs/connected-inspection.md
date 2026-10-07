# Connected declaration portal (candidate)

A separate read-only gateway and Flutter entry point browse approved loaded
agent declarations. Neither uses `adkl.server` or a host checkout. Offline
[static portal](static-portal.md) behavior is unchanged. This candidate is not a
verified deployed UAT service and does not include corporate SSO provisioning.

For a portal embedded directly in a host FastAPI application, see
[embedded inspection](embedded-inspection.md). It reuses this declaration/UI
protocol with explicit root registration and host authorization, without a
network gateway or host-specific collector. The separate gateway below retains
its UAT-only target and trusted-proxy requirements.

## Topology and trust

```text
browser -> corporate SSO proxy -> static Flutter assets
                              -> /api/v1/* inspection gateway
                                    -> private TLS host adapter
```

The SSO proxy must authenticate the user, strip incoming `X-ADKL-Subject` and
`X-ADKL-Proxy-Token`, then inject the validated stable subject and a dedicated
server-to-server secret. These are NOT browser headers or a substitute for SSO.
Restrict direct gateway ingress to this proxy; possession of its token permits
subject assertion. Protect assets too. This is an explicit trusted-proxy contract,
not built-in OIDC/JWT validation. Proxy session expiry/logout must deny API access.

The gateway authorizes a subject before registry enumeration, upstream fetch or
cache lookup. Each subject has an explicit agent allowlist. The candidate allows
UAT only and one rich-content disclosure policy per target. Do not mix metadata
and privileged content audiences under one target/cache. Target URLs and tokens
never appear in frontend assets or registry responses.

GET-only routes:

- `/api/v1/agents`: authorized registry
- `/api/v1/agents/{id}/inspection`: observation envelope + host snapshot

No browser URL, module path, factory, environment override, customer/session ID,
edit or chat API is accepted. HTTPS targets are fixed in trusted configuration;
redirects and environment proxies are disabled. Provide corporate trust through
standard SSL certificate configuration, never disable TLS verification. Deployment
must restrict egress to approved adapter services (including DNS resolution).

## Running a private candidate

Gateway uses stdlib networking and an ASGI interface. CLI serving requires
`uvicorn` in the deployment environment (already available with the optional
runtime extra); this does not add runtime dependencies to base authoring.

Example configuration (placeholders, NOT operational deployment):

```json
{
  "proxyTokenEnv": "INSPECTION_PROXY_TOKEN",
  "grants": {"SSO_SUBJECT": ["prospect", "classic-search"]},
  "targets": [
    {"id": "prospect", "name": "Prospect", "environment": "uat",
     "url": "https://APPROVED_HOST/internal/adkl/v1/agents/prospect/declarations",
     "tokenEnv": "HOST_INSPECTION_TOKEN"},
    {"id": "classic-search", "name": "Classic Search", "environment": "uat",
     "url": "https://APPROVED_HOST/internal/adkl/v1/agents/classic-search/declarations",
     "tokenEnv": "HOST_INSPECTION_TOKEN"}
  ]
}
```

Both secrets need at least 32 characters of secret-manager-generated entropy.
They must differ and never enter git, image build arguments or browser storage.
Configuration changes currently require a gateway restart; UI builds do not.

```bash
uv run --no-sync python -m adkl.connected --config /private/registry.json
cd console
flutter pub get --enforce-lockfile
flutter build web --release --target lib/connected_main.dart --no-web-resources-cdn
```

Serve that web directory through the authenticated static host, routing `/api/v1/`
to the gateway on the same origin. The existing `deploy/portal/nginx.conf` denies
API requests and is **not yet a connected deployment configuration**. Build from
immutable revisions with locked dependencies, approved image bases and scanning.
Do not overwrite a running local console or host serving SDK to test this portal.

The gateway bounds upstream bodies to 4 MiB, connects/reads with a three-second
socket timeout and checks a ten-second body deadline between reads. It permits
four simultaneous fetch/cache operations per process and 32 ASGI connections in
its CLI. Ingress must add overall deadlines/rate limits; timed-out clients cannot
preempt blocking Python network work. No retries or background collectors.
Caches are per-process and expire after 15 seconds; expired fetch failures clear
old content instead of serving stale success. Shared users can reuse only the
same authorized target/policy; authorization is checked every time.

## Evidence and UI

Host schema v1 `adkl_loaded_declarations` identifies agent/environment, process,
release assertion, generation, capture time and bounded owner-qualified nodes.
Static agent/global instructions, skill bodies, explicit tool schemas and
allowlisted configuration are declarations, not final model inputs.

Envelope adds gateway receipt time, digest and observation lifetime. A recent
GET does not reset declaration age, prove in-place updates or establish fleet
convergence. UI distinguishes observation expiry from capture time and offers
manual refresh only (no polling). Switching agents immediately clears content;
late responses cannot overwrite the selection. Failed/denied refreshes clear
prior content. Expired observations remain labeled historical until refreshed.
No browser persistence is used. Already rendered content cannot be revoked without
another request; protect the SSO session and workstation accordingly.

Overview shows explicit Doctor/behavioral **not run**. Agents, Skills and Tools
show owner-qualified declarations, instructions and schema JSON. Dynamic prompt
and toolset evaluation, resources/source spans and unknown providers are omitted,
not inferred. The UI does not render host HTML/Markdown as executable content.

Full source inspection still requires a separately approved, release-matched
build artifact and a provenance-aware join; full source/resource browsing is not
implemented here. Optional retained verification receipts can now be mounted on
the gateway as described below. Do not present offline findings as a live scan.

## Optional retained Doctor evidence

Set `evidenceDirectory` in the private gateway config to an operator-controlled
read-only directory. This does not add collection work inside the host application.
Each `<agentId>.json` file is an approved publisher receipt, at most 256 KiB:

```json
{
  "schemaVersion": 1,
  "kind": "adkl_retained_verification_receipt",
  "binding": {
    "agentId": "prospect", "environment": "uat",
    "release": "EXACT_HOST_RELEASE", "instanceId": "EXACT_PROCESS_ID",
    "generation": "EXACT_GENERATION", "snapshotDigest": "EXACT_GATEWAY_DIGEST"
  },
  "publishedAt": "2026-01-01T00:00:00Z",
  "expiresAt": "2026-01-01T01:00:00Z",
  "doctor": {
    "sourceDigest": "DOCTOR_SOURCE_DIGEST",
    "verificationEvidence": {
      "state": "not_run", "evidence": "retained_host_report", "contracts": []
    }
  }
}
```

The times/identities above are placeholders, not a usable passing receipt. The
publisher runs Doctor separately against the reviewed release's source and copies
its `sourceDigest` and sanitized `verificationEvidence` fields; do not fabricate
passing state or attach a whole report/transcript. Current completed evidence
requires run, subject, suite, evaluator and coverage identities. The publisher
must verify source-to-release correspondence using release provenance before
binding the report to an observed snapshot. The gateway does not independently
attest that correspondence. Mounting the directory grants publishing authority.

Publish regular files atomically, never symlinks. Unknown fields, malformed
summaries and vacuous passing contract rows are refused. The gateway reads the
receipt on each authorized inspection response (even cached host responses), so
replacement/removal is visible without fetching/recollecting the host. Exact
agent/environment/release/process/generation/digest must match; release `unknown`
cannot match. Expired, absent, malformed and mismatched receipts show explicit
nonpassing states; they do not block declaration browsing or reuse old evidence.
Case IDs are reduced to counts in browser responses.

UI distinguishes retained publisher-asserted results from checks executed here
(none), and hides current-result claims when the observation or receipt expires.
A process replacement requires a new approved binding. This conservative pilot
supports one receipt per target: load-balanced mixed-process responses may show
identity mismatch rather than falsely implying fleet-wide coverage. Automatic
publishing, signed provenance and multi-process receipt indexing remain future
work. Full Doctor findings/source resources are not transported by this receipt.

CI now builds `connected_main.dart` in `connected-portal-review-build`, after
required validation/test stages, and records/verifies a separate web manifest.
It does not create a gateway image, SSO configuration or deployment.

The gateway rejects unknown envelope/declaration fields, duplicate JSON keys,
nonfinite values, duplicate IDs across declaration kinds, wrong container owners
and dangling agent references. Schema providers use a closed GenAI/JSON Schema
subset; unknown extensions and external `$ref` values fail closed. Property names
and literal defaults/examples are approved schema content, not secrets detected
by the gateway. Publisher content review remains mandatory. A host schema change
may require coordinated gateway support instead of silently forwarding fields.

## Verification and rollout gates

- Protocol/ACL tests cover cache authorization, identity/version mismatch,
  redirects, invalid targets, unavailability, mutation and duplicate-header denial.
- Flutter tests cover delayed selection, approved instruction display, expired
  observations, failure clearing, no polling, mobile layout and same-origin GETs.
- Host repository owns actual native adapter/lifecycle and performance tests.
- Before enablement: real TLS/proxy and direct-origin denial; expired SSO and
  unauthorized-agent tests; concurrent users; host load comparison; release and
  mixed-pod evidence; browser PNG review at desktop/mobile; scan/publish immutable
  images and exercise rollback. No successful deployment/scanner claim from
  local tests. Follow [delivery gates](ci-cd.md).
