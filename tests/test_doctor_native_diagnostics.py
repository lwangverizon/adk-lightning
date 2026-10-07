"""Native inspection must retain routing evidence and qualify unknown costs."""
from types import SimpleNamespace

import pytest

from adkl.inspection import audit_snapshot, snapshot
from adkl.skill_lint import check_one


@pytest.mark.parametrize("body", [
    "This skill has no `fetch_x` access to quote offers.",
    "`fetch_x` is not used in this flow.",
    "Ask in text (do **not** use `fetch_x`).",
    "Use the wrapper, not `fetch_x`.",
    "`fetch_x` returns an empty summary for this reference.",
    "`fetch_x` answers a reference question with unrelated articles.",
])
def test_negative_mentions_do_not_recommend_broadening_tools(body):
    findings = check_one("s", {"name": "s", "description": "Skill"}, body, {"fetch_x"})
    assert not any(rule == "TOOL_USED_NOT_DECLARED" for _, rule, _ in findings)


@pytest.mark.parametrize("body", [
    "Call `fetch_x` now.",
    "Do **not** call `other_x`. Call `fetch_x` now.",
    "`fetch_x` is not used here.\n\nCall `fetch_x` for the next task.",
    "Use `fetch_x` even when it returns an empty summary.",
])
def test_real_missing_tool_calls_remain_visible(body):
    findings = check_one("s", {"name": "s", "description": "Skill"}, body, {"fetch_x"})
    assert any(rule == "TOOL_USED_NOT_DECLARED" for _, rule, _ in findings)


def native_snapshot(excluded):
    skills = {
        name: SimpleNamespace(
            name=name, instructions="Explain payments.",
            frontmatter=SimpleNamespace(
                description="Saved wallet payment management scheduling customer card bank",
                metadata={"adk_exclude_from_matching": excluded and name == "twin"},
            ),
        ) for name in ("base", "twin")
    }
    return snapshot(SimpleNamespace(
        doc={"features": []}, _skills=skills, _widget_cfg={}, _widgets_active=False,
        _tool_fns={}, _widget_specs=[],
    ))


@pytest.mark.parametrize("excluded", [False, True])
def test_overlap_uses_preserved_native_matching_metadata(excluded):
    view = native_snapshot(excluded)
    assert view["skills"][1]["metadata"]["adk_exclude_from_matching"] is excluded
    report = audit_snapshot(view, "")
    assert any(f["rule"] == "DESCRIPTION_OVERLAP" for f in report["findings"]) is not excluded
    assert len(view["skills"]) == 2  # Exclusion is not removal from the declared inventory.


def test_native_unknown_cost_is_lower_bound_not_zero_cost():
    view = native_snapshot(False)
    view["skills"] = [dict(view["skills"][0], instructions="x" * 40000, tools=["fetch_x"])]
    view["tools"] = [{"name": "fetch_x", "scope": "skill", "description": "Read"}]
    report = audit_snapshot(view, "")
    message = next(f["message"] for f in report["findings"] if f["rule"] == "TURN_COST_HIGH")
    assert "lower bound" in message
    assert "cost unknown for 1 tools" in message
    assert "charged" not in message
