import unittest
from adkl.inspection import audit_snapshot


class TestNativeAudit(unittest.TestCase):
    def test_checks_every_declared_skill_and_preserves_logical_names(self):
        report = audit_snapshot({"skills": [{"name": "math", "description": "",
            "instructions": "Use the tool.", "tools": ["missing_tool"]}],
            "tools": [{"name": "available_tool", "scope": "core", "description": "Read facts"}]}, "")
        self.assertFalse(report["passed"])
        findings = report["findings"]
        self.assertTrue(any(f["rule"] == "TOOL_UNRESOLVED" for f in findings), findings)
        self.assertTrue(all(f["skill"] == "math" for f in findings if f["severity"] == "ERROR"), findings)
        self.assertFalse(any(f["rule"] == "NAME_DIR_MISMATCH" for f in findings))

    def test_global_tool_usage_does_not_require_per_skill_binding(self):
        report = audit_snapshot({"skills": [{"name": "math", "description": "Teach math",
            "instructions": "Call `available_tool`.", "tools": []}],
            "tools": [{"name": "available_tool", "scope": "core", "description": "Read facts"}]}, "")
        self.assertFalse(any(f["rule"] == "TOOL_USED_NOT_DECLARED" for f in report["findings"]))

    def test_global_tools_are_not_dead_skill_bindings(self):
        report = audit_snapshot({"skills": [{"name": "math", "description": "Teach math",
            "instructions": "Explain addition.", "tools": []}],
            "tools": [{"name": "available_tool", "scope": "core", "description": "Read facts"}]}, "")
        self.assertFalse(any(f["rule"] == "TOOL_NOT_IN_ANY_SKILL" for f in report["findings"]))
