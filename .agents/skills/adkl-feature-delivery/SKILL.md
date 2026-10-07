---
name: adkl-feature-delivery
description: Implement or review substantive adk-lightning runtime, API, configuration, export, or Flutter console behavior. Use for feature delivery and behavioral fixes that cross capability or execution contracts. Not for prompt-only agent content, ADK version upgrades, CI-only work, or simple documentation edits.
---

# Deliver a Lightning feature

Read README.md for execution limits and docs/development.md for current commands.
Read the affected code and tests; preserve unrelated checkout changes. Use
COMPETITIVE_PRODUCT_PLAN.md to understand direction, not as capability evidence.

1. State the user outcome and observable acceptance criteria. Identify the config,
   API, runtime and UI surfaces involved; choose the smallest complete slice.
2. Trace configuration through adkl/capabilities.py, construction and actual
   execution. Prefer native ADK contracts over a duplicate framework. Keep optional
   runtime dependencies out of lightweight authoring paths.
3. Implement behavior and relevant failure paths together. Keep unsupported/partial
   states truthful in API, UI, explain and export. Feature saves must not leave a
   stale conversation executing previous settings. Tool exposure is not permission.
4. Verify the behavioral risks with existing tests and focused additions. For
   execution changes, exercise real native ADK objects where practical without
   a model call. For UI changes, inspect the workflow as well as running widget
   tests; for serialization, preserve the promised round-trip behavior.
5. Report the resulting behavior, evidence, compatibility and material limits.
   Distinguish a scaffold, diagnostic preview and completed runtime implementation.

Apply the relevant evidence row from docs/development.md. Do not add a second
approval process; continue within the user's established scope.
