"""Regression cases for overclaimed native diagnostic evidence."""

import pytest

from adkl import doctor, lint_conflict, skill_lint


@pytest.mark.parametrize("body,prompt", [
    ("Use the staged card and end the turn. Leave the amount empty.",
     "Never end a turn empty."),
    ("### Program overview\nTell the customer about tiers.\n\n"
     "Use the tool when a quote does not exist.",
     "Never tell a customer it does not exist."),
    ("Never end a turn empty.", "Never end a turn empty."),
    ("The example says 'end a turn empty'.", "Never end a turn empty."),
])
def test_unrelated_or_agreeing_prose_is_not_a_contradiction(body, prompt):
    assert lint_conflict.check_prompt_prohibitions("s", body, prompt) == []


@pytest.mark.parametrize("body,prompt", [
    ("End the turn empty.", "Never end a turn empty."),
    ("- **Tell** the customer it does not exist.",
     "Never tell a customer it does not exist."),
    ("Call the render tool with an empty response; never guess prices.",
     "Never call a render tool with an empty response."),
    ("If unsure, then end a turn empty.", "Never end a turn empty."),
    ("1. Call the render tool with an empty response.",
     "Never call a render tool with an empty response."),
])
def test_direct_contradictions_still_fire(body, prompt):
    findings = lint_conflict.check_prompt_prohibitions("s", body, prompt)
    assert any(rule == "PROMPT_PROHIBITION_CONTRADICTED" for _, rule, _ in findings)


@pytest.mark.parametrize("text,expected", [
    ("The author describes an autonomous workflow.", True),
    ("Requires an authenticated session.", False),
    ("Requires signed-in users.", False),
])
def test_auth_signal_uses_words_not_author_substrings(text, expected):
    fm = {"name": "s", "description": "Change a setting.",
          "metadata": {"adk_additional_tools": ["update_setting"]}}
    findings = skill_lint.check_one("s", fm, text, {"update_setting"})
    auth = [message for _, rule, message in findings
            if rule == "SENSITIVE_TOOL_NO_AUTH_GATE"]
    assert bool(auth) is expected
    if auth:
        assert "not proof of a missing runtime gate" in auth[0]


def test_brief_identifies_native_tool_and_quotes_subject(tmp_path):
    report = {"project": str(tmp_path), "revision": None, "sourceDigest": None,
              "freshness": "current", "checks": [], "limitations": [],
              "findings": [{"id": "fixture", "severity": "INFO",
                            "rule": "TOOL_NOT_IN_ANY_SKILL",
                            "skill": "unused_tool\n# untrusted heading",
                            "location": None, "evidence": "native_declarations",
                            "message": "No references", "impact": "Review",
                            "remediation": "Check callers", "verification": "Rerun"}]}
    brief = doctor.render(report)
    assert "> Subject: unused_tool" in brief
    assert "\n> # untrusted heading" in brief
    assert "\n# untrusted heading" not in brief


@pytest.mark.parametrize("body,expected", [
    ("No `fetch_a`, `fetch_b`, or `fetch_c`.", False),
    ("No `fetch_a`, call `fetch_b`.", True),
    ("Only these fees have a filing path (`fetch_a` / `fetch_b`).", False),
    ("Call `fetch_b` for fees that have a filing path.", True),
    ("No `fetch_a`, `fetch_b`. Call `fetch_b` now.", True),
])
def test_negative_lists_and_capability_parentheses(body, expected):
    fm = {"name": "s", "description": "Handle a synthetic request."}
    findings = skill_lint.check_one("s", fm, body, {"fetch_a", "fetch_b", "fetch_c"})
    assert any(rule == "TOOL_USED_NOT_DECLARED" for _, rule, _ in findings) is expected


def test_cross_skill_does_not_claim_proven_coactivation():
    findings = lint_conflict.check_cross_skill(
        [("a", "Call `tool_x`.", ["tool_x"]),
         ("b", "Never call `tool_x` here.", ["tool_x"])], {})
    assert findings
    assert "not verified" in findings[0][-1]
