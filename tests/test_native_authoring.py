import pathlib
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

from adkl.agent_schema import validate
from adkl.native_authoring import Workspace, source_path
from adkl.skill_lint import parse_frontmatter_text


class TestNativeAuthoring(unittest.TestCase):
    def test_markdown_horizontal_rules_do_not_truncate_skill_body(self):
        body = "First section.\n\n---\n\nSecond section.\n"
        fm, parsed = parse_frontmatter_text("---\nname: math\n---\n" + body)
        self.assertEqual(fm["name"], "math")
        self.assertEqual(parsed, body)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.path = self.root / "skills/math/SKILL.md"
        self.path.parent.mkdir(parents=True)
        self.path.write_text('---\nname: math\ndescription: Teach math\nlicense: MIT\nmetadata:\n  custom: retained\n  adk_additional_tools: [lookup]\n---\nAsk one question.\n')
        self.doc = {"version": 1, "agent": {"name": "tutor", "factory": "agent:build"},
                    "authoring": {"skills": {"math": "skills/math/SKILL.md"}}}
        self.skill = {"name": "math", "description": "Teach math", "instructions": "Ask one question.\n", "tools": ["lookup"]}
        self.report = {"agent": {"name": "tutor", "staticInstruction": "Help students.", "adkl": [{
            "skills": [self.skill], "tools": [{"name": "lookup", "scope": "skill", "description": "Read facts", "declaration": {}}],
            "audit": {"findings": []}}]}}

    def workspace(self):
        return Workspace(self.root, self.doc, self.report)

    def payload(self):
        return {"description": "Teach math", "tools": ["lookup"], "body": "Ask one question.\n",
                "revision": self.workspace().detail("math")["revision"]}

    def test_workflow_inventory_follows_agent_tree_and_handles_older_snapshots(self):
        self.report["agent"]["adkl"][0]["capabilities"] = [
            {"name": "flow", "description": "First flow", "kind": "workflow", "operation": "flow"}]
        self.report["agent"]["adkl"][0]["workflowGraphs"] = [
            {"name": "flow", "available": True, "nodes": [], "edges": []}]
        self.report["agent"]["children"] = [{"name": "child", "adkl": [{
            "skills": [], "tools": [], "capabilities": [
                {"name": "flow", "description": "Child flow", "kind": "workflow", "operation": "flow"}]
        }], "children": []}]
        inventory = self.workspace().workflows
        self.assertEqual([(item["agent"], item["name"], item["graph"] is not None)
                          for item in inventory], [("tutor", "flow", True), ("child", "flow", False)])

    def test_workflow_route_is_read_only_and_empty_for_declarative_agents(self):
        from adkl import server
        handler = object.__new__(server.Handler)
        handler._native_workspace = lambda browse=False: self.workspace()
        handler._send = lambda status, data: (status, data)
        status, data = handler._workflow_list()
        self.assertEqual((status, data), (200, {"workflows": []}))
        handler._native_workspace = lambda browse=False: None
        self.assertEqual(handler._workflow_list(), (200, {"workflows": []}))

    def test_catalog_schema_formats_and_missing_schema(self):
        tool = self.report["agent"]["adkl"][0]["tools"][0]
        for key in ("parameters", "parameters_json_schema"):
            with self.subTest(key=key):
                tool["declaration"] = {key: {"type": "object", "properties": {
                    "question": {"type": "string", "description": "Prompt"}},
                    "required": ["question"]}}
                result = self.workspace().catalog()["tools"][0]
                self.assertEqual(result["params"], [{"name": "question", "type": "string",
                    "description": "Prompt", "required": True}])
                self.assertTrue(result["schemaKnown"])
                self.assertEqual(result["scope"], "skill")
        tool["declaration"] = {"parameters_json_schema": {"properties": {
            "optional": {"type": ["string", "null"]}}}}
        self.assertEqual(self.workspace().catalog()["tools"][0]["params"][0]["type"], "string | null")
        tool["declaration"] = {}
        self.assertFalse(self.workspace().catalog()["tools"][0]["schemaKnown"])
        tool["declaration"] = {"parameters_json_schema": {"type": "object", "properties": {}}}
        self.assertTrue(self.workspace().catalog()["tools"][0]["schemaKnown"])

    def test_mapped_edit_retains_metadata_and_reloads_source(self):
        self.assertEqual(validate(self.doc, self.root)[0], [])
        payload = self.payload()
        payload["body"] = "Ask one question, then wait.\n"
        prefix = self.path.read_text().split('---\n', 2)[1]
        result = self.workspace().edit("math", payload, save=True)
        self.assertTrue(result["saved"])
        self.assertIn(prefix, self.path.read_text())
        updated = deepcopy(self.report)
        updated["agent"]["adkl"][0]["skills"][0]["instructions"] = payload["body"]
        detail = Workspace(self.root, self.doc, updated).detail("math")
        self.assertEqual(detail["body"], payload["body"])
        self.assertNotEqual(detail["revision"], payload["revision"])

    def test_skill_token_count_is_its_instruction_not_its_tool_schema(self):
        detail = self.workspace().detail("math")
        self.assertEqual(detail["estTokens"], len(self.skill["instructions"]) // 4)
        payload = self.payload()
        payload["body"] = "A longer procedure for this specific skill."
        result = self.workspace().edit("math", payload)
        self.assertEqual(result["estTokens"], len(payload["body"]) // 4)

    def test_noop_preserves_bytes_and_stale_revision_cannot_be_forced(self):
        before = self.path.read_bytes()
        payload = self.payload()
        self.workspace().edit("math", payload, save=True)
        self.assertEqual(self.path.read_bytes(), before)
        payload.update(revision="stale", force=True)
        with self.assertRaisesRegex(ValueError, "Source changed"):
            self.workspace().edit("math", payload, save=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unmapped_and_escaping_sources_cannot_be_written(self):
        payload = self.payload()
        del self.doc["authoring"]
        self.assertFalse(self.workspace().detail("math")["editable"])
        with self.assertRaisesRegex(ValueError, "Register"):
            self.workspace().edit("math", payload, save=True)
        for path in ("../SKILL.md", "/tmp/SKILL.md", "skills/math/agent.py"):
            with self.assertRaises(ValueError):
                source_path(self.root, path)
        (self.root / "linked").symlink_to(self.path.parent)
        with self.assertRaises(ValueError):
            source_path(self.root, "linked/SKILL.md")

    def test_mapping_reconciliation_reports_orphans_and_content_drift(self):
        self.doc["authoring"]["skills"]["old-name"] = "skills/old/SKILL.md"
        findings = self.workspace().mapping_findings()
        self.assertEqual([f["skill"] for f in findings], ["old-name"])
        self.assertIn("absent", findings[0]["message"])
        self.path.write_text(self.path.read_text().replace("Teach math", "Different"))
        findings = self.workspace().mapping_findings()
        self.assertEqual({f["skill"] for f in findings}, {"math", "old-name"})
        del self.doc["authoring"]
        self.assertEqual([(f["skill"], f["rule"], f["severity"])
                          for f in self.workspace().mapping_findings()],
                         [("math", "AUTHORING_SOURCE_UNMAPPED", "INFO")])

    def test_lint_error_and_missing_tool_do_not_modify_source(self):
        payload = self.payload()
        before = self.path.read_bytes()
        payload["description"] = ""
        result = self.workspace().edit("math", payload, save=True)
        self.assertFalse(result["saved"])
        self.assertTrue(result["findings"])
        self.assertEqual(self.path.read_bytes(), before)
        payload["tools"] = ["unavailable"]
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.workspace().edit("math", payload, save=True)

    def test_http_save_resets_worker_and_conflict_is_not_saved(self):
        from adkl import server
        handler = object.__new__(server.Handler)
        handler._native_workspace = self.workspace
        handler._send = lambda status, data: (status, data)
        payload = self.payload()
        payload["body"] = "Changed procedure."
        handler._body = lambda: payload
        with patch.object(server, "reset_chat") as reset:
            status, result = handler._skill_put("math", None)
            self.assertEqual(status, 200)
            self.assertTrue(result["saved"])
            reset.assert_called_once()
        with patch.object(server, "reset_chat") as reset:
            status, result = handler._skill_put("math", None)
            self.assertEqual(status, 409)
            self.assertFalse(result["saved"])
            reset.assert_not_called()
