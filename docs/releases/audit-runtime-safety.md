# Unreleased: audit runtime safety

Based on main `302bc83`; not a published SDK release. GitLab CI/settings remain
unchanged at the owner's request. Local verification is not remote CI evidence.

## Behavior and migration

- `tool_result_trimming` now provides a schema-independent bounded incomplete
  envelope, not a guessed field/list reduction. **Oversized output shape changes.**
  Application teams own semantic reduction, full-result retention and safe recovery.
  `max_chars` includes the envelope's default JSON serialization; it is not a
  token/provider-wire/memory limit. Non-JSON values fail the enabled guard instead
  of passing through. See [tool-result contract and TOON research](../tool-results.md).
- Widget sink errors cannot disappear behind host completion content. With no
  successful diagnostic reporter, delivery errors are raised after the native
  completion chain, even if a callback returned content. A failing reporter cannot
  skip that chain. A type-only delivery diagnostic avoids copying exception text.
- Duplicate selector matches preserve pin evidence without mutating host results.
- Retrieval numeric policy is validated in both declarations and direct SDK config;
  nonfinite scores, invalid weights/caps/dimensions are rejected earlier. `top_k: 0`
  remains valid to disable embedding candidates while retaining backfills/pins.
- Worker source digests refuse symlink targets that were not actually tracked,
  including explicit exclusions, credentials and virtualenvs. Content/exclude
  ancestor conflicts now fail; adjust conflicting `execution.yaml` declarations.
- Verification accepts safe project-contained symlinks but rejects directory cycles.
- Both shell-runner modes use pytest, including parameterized weighted-history tests.
- HTTP JSON bodies are limited to 1 MiB and a ten-second read deadline outside the
  workspace lock. Malformed/framing errors return 400, oversized bodies 413, and
  timeouts 408. Clients must send JSON objects. This remains a local development API.

No dependency or lock changes, TOON codec, hosted-service functionality, model
calls, or CI re-enablement are included. Existing explicit field projection remains
available as an application-declared policy; no business-specific rules are inferred.
