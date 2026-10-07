## Outcome

Describe the concrete user/problem trigger and resulting behavior.

## Evidence

List relevant commands and outcomes. Link the pipeline; distinguish local tests,
skipped integrations and live evaluations. For agent behavior, name the candidate,
model/runtime and dataset revisions. For console changes, include the inspected
workflow or screenshot when useful.

## Compatibility and delivery

Note affected config/API/tool contracts, migrations and material limitations.
For deployment changes, identify the immutable candidate, staging evidence and
rollback target. Mark this section not applicable for changes that do not affect it.

## Review

- [ ] Capability claims match runtime and export behavior.
- [ ] Relevant checks pass; failures/skips are explained without hiding gates.
- [ ] Secrets and private customer data are absent from code and evidence.
- [ ] Risk-appropriate independent review completed.
