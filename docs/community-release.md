# Community alpha: publication gates

This is a clean-history public candidate, not an approved public release.
It contains a reviewed source snapshot without inherited application history,
private integration reports, remote refs or corporate commit identities.
The original repository and restricted provenance evidence are retained separately.
Copyright and third-party license notices are preserved. Removing identifiers
or starting a new Git root does **not** establish redistribution rights.

## Owner decisions required before public availability

| Gate | Required evidence | Current state |
| --- | --- | --- |
| Rights and provenance | Owner/employer/third-party permission for code, examples, assets and derivative work; correct notices | Not established by source cleanup or Apache license declaration |
| Publication scope | Review the exact snapshot, all refs, metadata and distributable artifacts; scan secrets and restricted identifiers | Local candidate only; recheck after any change |
| Public destination | Canonical repository and issue URLs tested without internal access | Not designated |
| Security and conduct | Named maintainers, monitored private contacts and escalation route | Pending; see SECURITY.md, CODE_OF_CONDUCT.md and GOVERNANCE.md |
| Contribution CI | Isolated runners, safe fork execution, required checks and a deliberate failing-change drill | Configuration available; remote execution/controls not established |
| Release | Immutable version, updated notes, wheel/sdist/Console archives, checksums and outsider onboarding | No public release/tag created |

Publish only the approved snapshot's branch to a **new, empty repository**.
Do not mirror the original repository, its refs, tags, bundles, stashes, reflogs,
issues, attachments or build logs. Force-pushing a cleaned branch to an existing
server does not purge server-retained objects, merge-request refs, forks or caches.
Existing repositories must remain restricted unless their complete retained data
has separately been cleared and the hosting administrator confirms cleanup.

## Community CI candidate

[`.gitlab/ci/community.yml`](../.gitlab/ci/community.yml) reuses locked checks
while enabling branch pushes, same-project MRs, default-branch schedules and
explicit web runs. It suppresses duplicate branch pipelines with open MRs,
rejects parent-project pipelines for cross-project MRs and disables publication.
Forks may run branch pipelines with their own resources.

On the chosen project, select this CI configuration path only after:

1. Provisioning ephemeral Linux/amd64 container runners without customer network
   access, host Docker sockets or trusted shell workspaces.
2. Reviewing all project/group variables, job-token permissions and artifact/cache
   access. Expose no production/provider/publication secrets. YAML is not a sandbox
   against malicious contributions; do not run unreviewed fork code in the parent.
3. Protecting main, requiring successful pipelines and resolved discussions, and
   assigning reviewers. Keep publication disabled.
4. Running a passing MR and an intentionally failing MR; verify merge blocking,
   fork isolation, schedule admission and absence of publication jobs.
5. Recording pipeline links and exact artifact identity. Local tests and CI Lint
   do not establish runner execution or administrative controls.

## Reproduce technical checks

Follow [development](development.md) using locked Python dependencies and Flutter
3.44.0. Run lightweight/native lanes, Ruff, documentation checks, Flutter analyzer,
widget tests and release build, dependency audit and installed-wheel onboarding.
Browser-check changed UI behavior separately; compilation is not UX acceptance.

Scan the exact files, commit metadata, filenames and every Git object proposed
for publication, including binary assets and built archives. Keep restricted
scan terms and findings outside the candidate. A scanner finding requires review;
no broad secret-rule exception is installed here. A keyword scan cannot prove
absence of all sensitive material, ownership or license compatibility.

## Honest alpha scope

Offer native callback adoption, Unified Capability Selection, host-managed
background tasks, Doctor and local Console with their documented limits. Do not
claim production hosting, protocol conformance, routing quality or durable
in-flight recovery from static checks. Final handoff records the exact revision,
artifact hashes, tests, unrun checks, known limitations and owner approvals.
