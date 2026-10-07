# SDK CI and releases

Research and implementation: 2026-09-06. ADKL uses its existing private GitLab
project as the release destination. This distributes the SDK and console; it does
not deploy a hosted service or approve an authored agent for production.
Public-alpha preparation is tracked in [community release gates](community-release.md);
[unreleased notes](releases/unreleased.md) describe current functionality without
asserting a published version. The community CI entry point never publishes.

## Research and decisions

| Primary reference | Observed practice | ADKL decision |
|---|---|---|
| [Google ADK release workflow](https://github.com/google/adk-python/blob/main/.github/workflows/release-publish.yml) | Validate the release ref and require green checks on the published commit | Protected tags, full gates, tag/package/commit checks and main ancestry |
| [Pydantic AI CI](https://github.com/pydantic/pydantic-ai/blob/main/.github/workflows/ci.yml) | Separate release builds from publishing; test installed wheels outside the workspace; gate dependency resolutions | Retain isolated onboarding execution; publish exact artifacts in a separate job; expand version canaries later |
| [uv packaging guide](https://docs.astral.sh/uv/guides/package/) | Build wheel and sdist, use `--no-sources`, test the installed package | Build the slim wheel from its sdist in a clean staging tree with a pinned backend |
| [GitLab generic packages](https://docs.gitlab.com/user/packages/generic_packages/) | Versioned files, CI job tokens, manifests and checksums | Durable private release files, release links and short-lived job-token authentication |
| [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) | Short-lived publishing credentials through workload identity | Use if public PyPI is later authorized; no stored PyPI token now |
| [SLSA provenance](https://slsa.dev/spec/v1.2/build-provenance) | Identify artifact, source and builder; stronger levels require additional trust guarantees | Retain commit, pipeline, input hashes, SBOMs and checksums; label evidence unsigned without a SLSA-level claim |

These are adaptations to a small pre-1.0 SDK. The verified Python baseline is
3.13, not every version allowed by metadata. Runtime ranges are broader than the
tested lock. Onboarding resolves and executes a fresh downstream environment;
the native lane retains the platform's locked baseline. Installed-wheel onboarding
also copies the minimal native example into an independent host directory and
checks staged A2UI v0.9/v1.0 execution there.

The package description comes from the repository README. The build script
stages it with package metadata, LICENSE and NOTICE so the source distribution
is self-describing and wheel metadata includes the Markdown overview. Application
projects and the documentation tree remain outside the SDK distributions.

## Current external-checkout workflow

Until a stable ADKL package is published, external repositories consume a wheel
built locally from a pinned ADKL `main` commit. They record the commit, wheel
SHA-256 and their own dependency lock update. They do not commit artifacts to
Git, use editable dependencies, or point a runtime at a mutable checkout. The
[integration playbook](agent-integration.md#consume-a-pinned-main-checkout-today)
is the canonical procedure.

This is intentionally a development-integration workflow, not a release channel.
The static metadata version on `main` can describe multiple commits. A stable
release will replace local acquisition with one immutable, versioned private
registry artifact; public PyPI can be added later without changing consumer code.

## Contributor and maintainer flow

1. Open an MR from a short-lived branch and run the relevant locked local checks.
   Ordinary push/MR pipelines are currently off by default; see
   [pipeline policy](ci-cd.md#current-pipeline-policy). Explicit web runs on main
   and protected release tags retain lint, secrets, components, native ADK,
   Flutter, dependency audit, review builds and SDK onboarding. Lint also validates
   release metadata and documentation links. No customer credentials are required.
2. Record local results, unrun checks and any owner-approved merge exceptions.
   A skipped or absent pipeline is not success. Protect main from direct and
   force pushes. Independent review is a team responsibility where the GitLab
   tier cannot enforce approvals.
3. Update `[project].version` in `pyproject.toml`, deliberately update the lock with
   `uv lock`, and add `docs/releases/<version>.md` covering behavior, compatibility,
   migrations and limitations. Generated projects derive SDK pins and wheel names
   from this metadata. Before 1.0, minor versions may require migration; patches
   should preserve supported behavior.
4. After that MR and main CI pass, a Maintainer creates an annotated tag on the
   reviewed main commit: `vX.Y.Z` or candidate `vX.Y.Z-rc.N`. Python metadata must
   match `X.Y.Z` or `X.Y.ZrcN`. Never move or reuse a published tag.
5. The tag repeats all gates. Onboarding builds the slim wheel, sdist and separate console ZIP once, executes the
   installed wheel, and prepares `dist/release/`: distributions, release notes,
   tested-lock SBOMs/advisory reports, and a SHA-256 manifest. Normal job artifacts
   expire after 14 days.
6. Only a protected tag push starts `sdk-publish`. It checks main ancestry and
   exact artifact/commit/pipeline identity, uploads `adkl-sdk/<version>` to the
   generic registry, reads back every file, and creates GitLab release links.
   Publication is serialized and non-interruptible. It does not rebuild or
   install build dependencies. Packages inherit project privacy.

Example for a future reviewed candidate:

```bash
git fetch origin
git tag -a v0.2.0-rc.1 <reviewed-main-sha> -m "ADKL 0.2.0rc1"
git push origin v0.2.0-rc.1
```

Creating the protected tag is the publication decision; no redundant manual job
follows. Scripts reject mismatched versions, dirty artifacts, other pipeline/commit
identities, unsafe contents and changed existing registry files. Administrators
can still delete data: application checks are not WORM storage or signatures.

## Consume, retry and recover

Download release files through normal GitLab project access. With all files in
an otherwise empty directory, run `python scripts/ci/sdk_release.py verify
/path/to/release` from a checkout. Install the exact slim wheel or supply it to the
console's explicit runtime preparation. Extract the matching console ZIP and
pass its `web/` directory to `adkl.server --web`; the wheel contains no UI assets. Keep each project lock with its vendored
wheel. See [onboarding](project-onboarding.md).

For partial network failure, retry publication in the **same pipeline** while its
original artifacts remain available. Identical files are reused; differing bytes
or an existing differing release fail. Do not rebuild under the same version.
If evidence is lost, investigate and issue a new version instead of replacing
history. Published SDK artifacts stay in the registry rather than expiring with
job reports; exclude them from administrative cleanup policies.

For a defective release, stop adoption, mark the release withdrawn through the
maintainer incident process, and publish a corrective version. Restore the prior
exact wheel and compatible project lock/config, then rerun smoke/evaluations.
Release notes must identify migrations that make binary downgrade unsafe.

## Current checkout controls

Ordinary pipelines are disabled by the version-controlled
[workflow policy](ci-cd.md#current-pipeline-policy), at the owner's request.
Explicit web runs on main and protected release-tag pipelines remain available;
release publication still requires all jobs to succeed. Existing origin shell
runners fail before tests because `uv` and `gitleaks` are unavailable. This
policy does not repair those runners or change merge checks. Local results are
not remote pipeline evidence. The older setup record below is historical and
must not be used to certify current protections.

## Controls and verification limits

Historical settings read back after saving on 2026-09-06:

| Control | Verified setting |
|---|---|
| Main | Maintainer merges; direct pushes prohibited; force pushes disabled |
| Version tags | `v*` creation limited to Maintainers |
| Merge checks | Successful pipelines and resolved threads required; skipped pipelines not accepted |
| Package protection | Generic package `adkl-sdk` requires Maintainer access to push |
| Pipeline variables | No role may supply pipeline-variable overrides |
| Job tokens | Allowlist contains this project only; repository Git pushes disabled |
| Schedule | Active daily main checks, configured for 06:17 UTC; schedule ID 4427900; GitLab worker may delay execution |

The available project UI did not expose a generic duplicate-upload deny switch.
The publisher enforces exact-byte reuse, and the package push restriction limits
writers; do not describe this as registry-enforced immutability. Package assets
are currently retained without a duplicate cleanup limit. Release jobs install
Git from the pinned base distribution's repositories for source checks; those
OS packages are not a fully frozen or independently scanned toolchain snapshot.
GitLab supplies job tokens to other jobs too, with permissions inherited from
the triggering user. A separate publisher job is not exclusive credential
isolation against malicious CI code. Keep changes reviewed and do not run
untrusted fork code in privileged parent pipelines. Stronger isolation requires
a separate release trust domain or identity policy bound to the protected tag.

Configure Maintainer-only `v*`, no direct/force pushes to main, required successful
pipelines and resolved threads, and do not treat skipped pipelines as successful.
Keep protected caches separate, restrict pipeline-variable overrides and job-token
access and deny duplicate generic files. Daily main advisory schedules require
restoring schedule admission in the current opt-in workflow first; the historical
schedule above does not currently cause a pipeline. Admitted advisory checks
must not publish or call live models.

The publisher has hermetic failure/retry tests. The first protected-tag release
is a separate release action; registry publication is not end-to-end verified
until exercised. Admitted verification pipelines test source distributions and
installed native execution; ordinary MRs currently rely on recorded local checks.
Docker execution, signed attestations, Dart/container advisory coverage, broader
version canaries and hosted staging/rollback remain explicit next steps.
