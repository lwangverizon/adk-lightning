# Security policy

ADKL is an engineering alpha. No supported security-release series or response
SLA has been established. Use a pinned, reviewed revision and keep dependencies
current. The local Console server is for a trusted single user on loopback, not
public hosting. Background workers require host authentication, authorization,
storage controls and supervision; inspection endpoints require explicit host
access controls.

## Report a suspected vulnerability privately

**A monitored private security intake has not yet been designated. This remains
an unresolved readiness gap despite public source availability.** If you already have
repository access, contact a project Maintainer using your existing private
project communication channel. Ask for a secure reporting route before sending
sensitive details. Do not open a public issue or MR containing an exploit,
credential, customer record, private prompt or production endpoint. If you have
no established private contact, request contact information without disclosing
the vulnerability; this repository does not promise an unconfigured inbox.

Once a private route is established, provide the affected commit/version, minimal
synthetic reproduction, impact, relevant trust boundary and suggested mitigation.
Maintainers should acknowledge receipt, coordinate disclosure and record a fix
and affected versions. Never ask a reporter to test against customer systems.
Rotate exposed credentials independently of code/history cleanup.

## Verification limits

Local tests and advisory/secret scans do not certify authorization, production
isolation or the absence of vulnerabilities. Current CI scans checkout content;
publication requires scanning the exact history/refs and artifacts being shared.
The runtime dependency audit does not cover all Dart, OS or container packages.

See [community release gates](docs/community-release.md) for the outstanding
security-contact, runner-isolation and publication decisions.
