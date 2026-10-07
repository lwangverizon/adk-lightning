---
name: adkl-release-review
description: Design, change or troubleshoot adk-lightning CI/CD, prepare a release candidate, review artifact/evaluation evidence, or plan promotion and rollback. Use for delivery-control and release-readiness tasks. Not for ordinary feature implementation or a request that only edits agent prose.
---

# Review Lightning delivery

Read docs/ci-cd.md, docs/development.md and .gitlab-ci.yml. The guide distinguishes
implemented review builds, required GitLab setup, and future production delivery.
Inspect actual capability/runtime state before claiming readiness.

1. Identify whether the candidate is the platform, an authored agent, or a console
   review artifact. Record its exact revision/digest and relevant locks, config,
   model/provider and eval dataset revisions. Surface a dirty working tree.
2. Run relevant checks and inspect reports, not just job presence. Missing tooling,
   scanner service failures and unrun integrations are not passing checks. Validate
   CI source rules and failure behavior; use project CI Lint and a real pipeline
   when access exists. Keep required tests automatic and secrets out of MR jobs.
3. Verify artifact identity and limits. Use scripts/ci/artifact_manifest.py for
   console review assets. Its hashes are unsigned; it is not a release signature
   or production authorization. Bind future signed provenance/evals to the exact
   promoted artifact; never rebuild between staging and production.
4. For an actual deployment, inspect the guide's production entry criteria and
   existing infrastructure. Confirm environment scope, staging evidence, canary
   observations, rollback target and migration compatibility. The development
   HTTP server and partial retrieval/export paths cannot satisfy these criteria.
5. Complete all authorized preparation. Perform external publication/deployment
   only within the user's established scope; the skill grants no extra authority
   and introduces no redundant confirmation. Report the concrete candidate,
   verification, remaining setup or capability blockers, and next executable step.
