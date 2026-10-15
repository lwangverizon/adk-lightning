# Maintenance and project decisions

ADKL is a pre-release, maintainer-led engineering alpha with public source at
[github.com/lwangverizon/adk-lightning](https://github.com/lwangverizon/adk-lightning).
Repository administrators manage collaboration; named maintenance commitments,
private security/conduct contacts and an escalation owner still need confirmation.
See [release gates](docs/community-release.md).
This document does not assign someone a role or establish ownership of contributed
or inherited material.

## Change process

- Propose public API or architectural changes with a user need and executable
  acceptance case. Prefer native ADK contracts and incremental adoption.
- Submit focused MRs using [CONTRIBUTING.md](CONTRIBUTING.md). Preserve compatibility
  or document migration explicitly; keep tests synthetic and credential-free.
- Runtime authorization, dependency, release and CI changes need risk-appropriate
  review. Independent review must be recorded honestly; an author's self-check is
  not independent approval. Add CODEOWNERS only after actual owners agree.
- Disagreements should be documented in the MR with alternatives and evidence.
  A maintainer records the resolution; involved reviewers may request another
  maintainer's review where one is available.

## Support and releases

No public stable release or long-term-support promise exists yet. The tested
baseline is Python 3.13, Flutter 3.44.0 and the committed runtime lock, not every
version permitted by dependency ranges. Model/customer-environment acceptance is
host-owned. Before 1.0, minor releases may require migration; patches should
preserve documented supported behavior.

Install from a pinned commit using the documented immutable-wheel workflow until
a versioned channel is announced. The metadata version on main is not a unique
artifact identity. Maintainers must not reuse published versions/tags or silently
replace their bytes. Release notes distinguish executed checks from declarations,
local evidence from remote CI, and supported behavior from roadmap work.

Public availability does not make the development server production-ready or
transfer host identity, business authorization and deployment responsibilities
to the SDK. See [security](SECURITY.md) and [conduct](CODE_OF_CONDUCT.md).
