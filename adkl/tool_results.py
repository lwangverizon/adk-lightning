"""Schema-independent, explicitly lossy tool-result size guard.

Application callbacks own semantic reduction. This guard never selects business
fields or returns a shortened collection masquerading as a complete result.
The budget measures Python's default ASCII JSON serialization, not model tokens,
provider framing, later callbacks, or peak memory used by the tool/JSON encoder.
"""
from __future__ import annotations

import json
import math


def validate_budget(max_chars: int, keep: str) -> None:
    if type(max_chars) is not int or max_chars < 200:
        raise ValueError("max_chars must be an integer of at least 200 for the incomplete-result envelope")
    if keep not in ("head", "tail"):
        raise ValueError("keep must be head or tail")


def _validate_json(value, ancestors=None):
    """Refuse coercion of object keys, tuples, custom objects and nonfinite data."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list):
        raise ValueError("Tool result must contain only finite JSON data")
    ancestors = set() if ancestors is None else ancestors
    identity = id(value)
    if identity in ancestors:
        raise ValueError("Tool result must not contain cycles")
    ancestors.add(identity)
    try:
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("Tool result object keys must be strings")
            values = value.values()
        else:
            values = value
        for child in values:
            _validate_json(child, ancestors)
    finally:
        ancestors.remove(identity)


def bounded_result(value, *, max_chars: int = 4000, keep: str = "head"):
    """Return the original JSON value, or a new bounded incomplete envelope.

    No mutation, schema inference, summarization, implicit retrieval or retry.
    The preview is an untrusted serialized excerpt, not parseable/full tool data.
    Non-JSON input raises rather than bypassing an explicitly enabled size guard.
    """
    validate_budget(max_chars, keep)
    try:
        _validate_json(value)
        size, preview = 0, ""
        for chunk in json.JSONEncoder(allow_nan=False).iterencode(value):
            size += len(chunk)
            if keep == "head":
                preview += chunk[:max(0, max_chars - len(preview))]
            else:
                preview = (preview + chunk)[-max_chars:]
    except (RecursionError, TypeError, OverflowError) as exc:
        raise ValueError("Tool result cannot be safely encoded as JSON") from exc
    if size <= max_chars:
        return value
    envelope = {
        "_adkl_trimmed": "Incomplete result; execution may have succeeded. Do not infer missing data or repeat side effects.",
        "original_chars": size,
        "keep": keep,
        "preview": "",
    }
    # Include marker/metadata and JSON escaping of the excerpt in the budget.
    if len(json.dumps(envelope)) > max_chars:
        raise ValueError("max_chars cannot hold the incomplete-result envelope")
    low, high = 0, len(preview)
    while low < high:
        middle = (low + high + 1) // 2
        envelope["preview"] = preview[:middle] if keep == "head" else preview[-middle:]
        if len(json.dumps(envelope)) <= max_chars:
            low = middle
        else:
            high = middle - 1
    envelope["preview"] = (preview[:low] if keep == "head" else preview[-low:]) if low else ""
    return envelope
