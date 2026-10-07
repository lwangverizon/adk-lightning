"""The generic guard bounds representation, never chooses business data."""
import copy
import json

import pytest

from adkl.tool_results import bounded_result
from adkl.runtime import make_result_trimmer


@pytest.mark.parametrize("value", [None, False, 0, [], {}, "plain", {"rows": [{"ok": False, "n": 0, "v": None}]}])
def test_small_values_are_identical(value):
    assert bounded_result(value) is value


@pytest.mark.parametrize("keep", ["head", "tail"])
@pytest.mark.parametrize("value", [
    "first" + "x" * 1000 + "last",
    [{"i": i, "value": "x" * 100} for i in range(30)],
    {"left": "x" * 1000, "right": "y" * 1000},
    {"nested": {"odd": [None, False, 0, "hello\n\"\\😀" * 500]}},
])
def test_budget_includes_escaping_metadata_and_all_fields(value, keep):
    original = copy.deepcopy(value)
    encoded = json.dumps(value)
    for budget in (200, 300, 500, 1000):
        result = bounded_result(value, max_chars=budget, keep=keep)
        assert len(json.dumps(result)) <= budget
        assert result["original_chars"] == len(encoded)
        assert "Incomplete" in result["_adkl_trimmed"]
        assert "side effects" in result["_adkl_trimmed"]
        preview = result["preview"]
        assert encoded.startswith(preview) if keep == "head" else encoded.endswith(preview)
        assert result["keep"] == keep
    assert value == original


def test_no_partial_business_schema_and_no_implicit_success():
    result = bounded_result({"status": "success", "rows": [{"secret": "x" * 3000}]}, max_chars=300)
    assert "status" not in result and "rows" not in result
    assert result["preview"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, {"fn": lambda: None}, (1, 2)])
def test_non_json_is_refused_not_stringified_or_passed_through(value):
    with pytest.raises(ValueError):
        bounded_result(value)


def test_cycle_is_refused_but_shared_values_are_supported():
    value = []
    value.append(value)
    with pytest.raises(ValueError, match="cycles"):
        bounded_result(value)
    child = [1, 2]
    value = [child, child]
    assert bounded_result(value) is value


@pytest.mark.parametrize("budget", [True, 199, 300.5, "400"])
def test_direct_callback_validates_budget(budget):
    with pytest.raises(ValueError):
        make_result_trimmer(budget)
