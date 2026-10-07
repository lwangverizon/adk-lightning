# Native-host integration migration

Native agents can now use `RuntimeBindings(native_toolset=...)` for shared
presentation and callback composition without replacing their skill lifecycle.
For declaration-only inspection use `adkl.inspection.attach`. See
[runtime bindings](runtime-bindings.md#preserve-a-host-owned-skill-lifecycle).

## Retired declarations

`analytics`, `identity_gate`, and `semantic_cache` are removed from the selectable
registry. They never had supported runtime implementations. Old declarations are
rejected with migration guidance even when disabled; delete those blocks instead
of expecting them to be silently ignored. Host telemetry, authorization and cache
policy remain host-owned. Existing observation and diagnostic utilities remain.

## Deprecated policies, not silent behavior changes

`progressive_auth` remains readable for migration, but warns: it is an attempt
marker, not authorization. Replace it with an explicit host observer and request
scope. `output_cleanup` warns because blanket regex removal is not a universal
output policy. `short_circuit` warns because it is an exact-response recipe, not a
host routing/transport abstraction. Their behavior is retained for compatibility;
no application is silently switched onto different output or sign-in rules.

Use `after_model_transforms` on binding composition for format-aware host filters.
When shared output filters or explicit transforms are installed, host response
replacements feed subsequent transforms, rather than hiding them through ADK's
first-response short circuit. Order is host callbacks, shared filters, explicit
transforms. With neither installed the original host chain is unchanged. Errors
propagate; filters decide their own partial-stream behavior. Completed-response
redaction alone cannot protect already-delivered streaming deltas.

## Adoption and compatibility

Use `skill_retrieval.lexical` rather than the deprecated `bm25` alias. Both name
the existing lexical-overlap algorithm, not BM25. Conflicting values fail rather
than silently choose one. Remove `history_slicing.preserve_tool_pairs`: it is
inert and now warns when configured; user-turn boundaries remain unchanged.

Adopted widget builders/expected-empty predicates preserve host register shapes.
No host dictionary-to-list repair wrapper is necessary. Native skill observations
are explicitly host-owned, and Doctor reports unmapped skill sources as INFO.
The SDK does not infer whether declared tools executed.

Do not stack built-in EMA reachability history with weighted ranking history.
Do not enable generic result sizing as a substitute for business-aware reduction.
Columnar encoding, prices, routing thresholds, session concurrency and client
transport remain application policy.

The incomplete generated-code export remains a legacy path pending a separate
consumer/migration decision; SDK-backed bundles and native Python are preferred.
No export or streaming compatibility is silently removed in this migration.
