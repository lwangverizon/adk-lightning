import io
import json
import pathlib
import tempfile
import unittest
from email.message import Message
from unittest.mock import patch

from adkl import server


class TestProjectApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.state = patch.multiple(server._workspace, root=self.root, projects=self.root / "projects",
                                    agents=[], chat=None, profile=None, sdk_wheel=None)
        self.state.start()
        self.addCleanup(self.state.stop)

    def request(self, path, data=None, origin=None):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = Message()
        wire = json.dumps(data or {}).encode()
        handler.headers["Content-Length"] = str(len(wire))
        if origin:
            handler.headers["Origin"] = origin
        handler.rfile = io.BytesIO(wire)
        handler._send = lambda status, result, *args: (status, result)
        return handler._route_post()

    def test_empty_workspace_create_select_and_duplicate(self):
        status, result = self.request("/api/agents", {"name": "notes", "purpose": "Help people organize their notes."})
        self.assertEqual(status, 201)
        self.assertEqual(str(server._workspace.root), result["path"])
        self.assertEqual(server._workspace.agents[0]["id"], result["selected"])
        status, _ = self.request("/api/agents", {"name": "notes", "purpose": "Replace previous purpose."})
        self.assertEqual(status, 409)
        self.assertIn("organize their notes", (server._workspace.root / "instruction.md").read_text())

    def test_cross_site_cannot_create_or_trigger_jobs(self):
        for route in ("/api/agents", "/api/project/prepare", "/api/project/release", "/api/project/credentials",
                      "/api/project/baseline", "/api/project/candidate"):
            status, _ = self.request(route, origin="https://unrelated.example")
            self.assertEqual(status, 403)
        self.assertFalse((self.root / "projects").exists())

    def test_credentials_are_project_local_not_returned_and_not_packaged(self):
        self.request("/api/agents", {"name": "notes", "purpose": "Help people organize their notes."})
        secret = "synthetic-test-value"
        status, result = self.request("/api/project/credentials", {"key": secret}, "http://127.0.0.1:8787")
        self.assertEqual((status, result), (200, {"saved": True}))
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual((server._workspace.root / ".env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((server._workspace.root / ".env").read_text(), f"GOOGLE_API_KEY={secret}\n")
        self.assertFalse((self.root / ".env").exists())
        status, _ = self.request("/api/project/credentials", {"key": "bad\nINJECT=1"})
        self.assertEqual(status, 409)

    def test_setup_requires_explicitly_configured_sdk(self):
        self.request("/api/agents", {"name": "notes", "purpose": "Help people organize their notes."})
        status, result = self.request("/api/project/prepare")
        self.assertEqual(status, 409)
        self.assertIn("--sdk-wheel", result["error"])

    def test_verification_uses_selected_project_and_rejects_payload_commands_and_overlays(self):
        with patch("adkl.project_jobs.start", return_value={"state": "running"}) as start:
            status, _ = self.request("/api/project/baseline")
            self.assertEqual(status, 202)
            start.assert_called_once_with(self.root, "baseline", None)
            start.reset_mock()
            status, _ = self.request("/api/project/candidate", {"command": "untrusted"})
            self.assertEqual(status, 409)
            status, _ = self.request("/api/project/candidate?env=uat")
            self.assertEqual(status, 409)
            start.assert_not_called()

    def test_widget_coverage_and_its_annotation_apply_to_an_unsaved_buffer(self):
        """The console lints the editor buffer, not the file on disk.

        `_lint(override=...)` is the path a save is refused on, so the annotation
        has to clear the finding and a misspelling has to block there -- not only
        in `adkl check`, where the author has already committed the mistake.
        """
        self.request("/api/agents", {"name": "notes",
                                     "purpose": "Help people organize their notes."})
        doc = {"skills": {"include": ["answer"]},
               "tools": {"catalog": "catalog.json"}}
        (server._workspace.root / "catalog.json").write_text(json.dumps({"tools": [
            {"name": "render_fact_card",
             "emits_widget": {"value": True, "basis": "configured presentation pack",
                              "inferred": False}}]}))
        directory = server._workspace.root / "skills" / "answer"
        directory.mkdir(parents=True, exist_ok=True)
        header = "---\nname: answer\ndescription: Answer a billing question.\n"
        (directory / "SKILL.md").write_text(header + "---\nAnswer briefly.\n")

        def rules(buffer):
            return {f["rule"] for f in server._lint(doc, None, {"render_fact_card"},
                                                    override=("answer", buffer))}

        self.assertIn("WIDGET_OUTPUT_MISSING", rules(header + "---\nAnswer briefly.\n"))
        annotated = header + "metadata:\n  adkl_presentation: text_only\n---\nAnswer briefly.\n"
        self.assertNotIn("WIDGET_OUTPUT_MISSING", rules(annotated))
        invalid = header + "metadata:\n  adkl_presentation: bare\n---\nAnswer briefly.\n"
        self.assertIn("PRESENTATION_METADATA_INVALID", rules(invalid))

    def test_source_identity_changes_for_skills_and_code_but_not_secrets(self):
        from adkl.execution import source_digest
        self.request("/api/agents", {"name": "notes", "purpose": "Help people organize their notes."})
        baseline = source_digest(server._workspace.root)
        (server._workspace.root / ".env").write_text("SYNTHETIC=private")
        self.assertEqual(baseline, source_digest(server._workspace.root))
        (server._workspace.root / "agent.py").write_text("# changed factory\n")
        self.assertNotEqual(baseline, source_digest(server._workspace.root))
