# Generic tool-result sizing and application-owned reduction

ADKL must not guess which tool-result fields, rows, prices, statuses or qualifiers
matter. Applications own tool schemas, semantic reduction, pagination, redaction
and recovery. A generic size guard cannot guarantee that omitted data is irrelevant.

## The opt-in safeguard

```yaml
features:
  pipeline: [tool_result_trimming]
  tool_result_trimming:
    max_chars: 4000
    keep: head
```

`tool_result_trimming` remains experimental and off by default. Activating it
requires explicitly declaring `max_chars`; 4000 is the registry default, not an
implicit consent to data loss. The minimum is 200. `keep` accepts `head` (default)
or `tail`, selecting an end of the **serialized preview**, not business rows.

The dependency-free helper is also available directly:

```python
from adkl.tool_results import bounded_result

model_result = bounded_result(tool_result, max_chars=4000, keep="head")
```

- A fitting JSON value is returned unchanged, including false, zero and null.
- An oversized value becomes a new envelope with `_adkl_trimmed`,
  `original_chars`, `keep` and `preview`. The marker explicitly states that the
  result is incomplete, execution may have succeeded, missing facts must not be
  inferred, and side effects must not be repeated.
- Original field names/statuses are **not** retained as authoritative envelope
  fields. ADKL does not know which fields are critical or whether a tool succeeded.
- The preview is an untrusted excerpt of serialized data. It may cut across a
  JSON token; it is not a parseable original document, summary or complete answer.
  The enclosing result is valid JSON, and the source object is never mutated.
- The entire envelope, including marker, metadata and escaped preview, fits
  `max_chars` under `json.dumps(result)` with Python's default ASCII serialization.
  This is **not a model token limit**, an entire-request limit, a provider-wire
  limit or a peak-memory bound. Later callbacks (including widget guidance) and
  provider framing can add overhead. A huge individual string can still cause
  encoder allocation; tools need their own resource limits.
- Plain JSON values are supported: dicts with string keys, lists, strings, finite
  numbers, booleans and null. Nonfinite numbers, cycles, tuples/custom objects and
  non-string object keys raise rather than being coerced or silently escaping an
  enabled guard. Normalize them explicitly in the application if appropriate.
- There is no implicit model call, tool retry, artifact storage or retrieval
  endpoint. A marker cannot enforce model behavior or authorization. Applications
  must own safe recovery and idempotency for consequential operations.

## Where application logic runs

For native `RuntimeBindings`, raw `after_tool_observers` run first, host
`after_tool_callback` transforms next, followed by shared field projection (when
explicitly configured), this generic guard, and current widget guidance.
Host transforms receive the actual tool name, arguments, context and response:

```python
# Integration sketch: reduce_domain_result is supplied and tested by the host.
def reduce_for_model(*, tool, args, tool_context, tool_response):
    if tool.name == "search_catalog":
        return reduce_domain_result(tool_response)
    return None  # leave other tools unchanged

kwargs = binding.merge_into({
    "name": "assistant",
    "model": host_model,
    "after_tool_callback": [reduce_for_model],
})
```

The host decides which facts must survive, whether to paginate, and how to retain
or retrieve the full result. Observers must not mutate results. The existing
`tool_result_projection` helper remains an explicit host-declared field policy;
ADKL does not infer such rules and does not require it for generic sizing.

Native after-tool replacement can enter session history. This safeguard does not
just modify a transient outgoing request, nor does it delete the original from
application logs, caches, widgets or observers. Redact sensitive data before those
surfaces; this is not a confidentiality filter.

## TOON research and decision

Reviewed upstream on September 10, 2026:

- [TOON reference documentation, pinned revision](https://github.com/toon-format/toon/blob/f151a5d830d001bc244395b891183cba37e0d935/packages/toon/README.md)
- [TOON specification v4.1 working draft, pinned revision](https://github.com/toon-format/spec/blob/d6db4b04303bdea132351ce45aed612311c850b2/SPEC.md)

TOON is an encoding of the JSON data model, not a lossy trimming policy. Uniform
record arrays/maps can avoid repeated keys; irregular or deeply nested data may
be smaller as compact JSON. Upstream benchmark results are workload/model/tokenizer
specific, and its prominent savings compare with formatted JSON. They do not
establish savings, accuracy or latency for ADKL's native provider serialization.
The specification also has normalization rules for non-JSON types; those must not
be mistaken for preservation of arbitrary Python objects.

**Decision:** do not add a partial TOON codec, new base dependency, or automatic
TOON conversion to ADKL in this change. A lossless codec cannot guarantee that
large data fits a finite budget. Truncating TOON can also violate declared row
counts and structural validity. Applications may encode a suitable result through
a vetted TOON implementation in their host transform, label its encoding/version,
and evaluate actual provider token counts and task outcomes. The generic guard
can bound the resulting JSON-wrapped text, but that preview is then explicitly
incomplete, not valid full TOON.

A future optional ADKL codec would need pinned conformance fixtures, preservation
of false/zero/null and numeric precision, real native tool-response tests, a JSON
fallback, and representative provider/tokenizer evaluation before claiming benefit.

## Migration from the former trimmer

This is an intentional experimental behavior change. Previously ADKL guessed the
largest field, shortened its content/rows, retained selected surrounding fields,
and could exceed its stated budget. `keep: tail` did not affect list results.

The new safeguard removes that schema-dependent algorithm rather than extending
it. Oversized results now have an incomplete envelope instead of the original
shape with some rows missing. Existing code consuming transformed results must
handle the envelope. Move any required field/status preservation into a host
transform that returns a fitting result, or omit `tool_result_trimming` until the
host has a safe policy. Do not automatically re-run a side-effecting tool to
recover omitted output. Review prompts and test downstream callbacks before
adopting the change.
