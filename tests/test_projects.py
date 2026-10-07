import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml

from adkl import audit, projects, release


class TestProjects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = pathlib.Path(self.tmp.name).resolve()

    def create(self):
        return projects.create(self.workspace, "test_agent", "Help people understand their provided notes {literally}.")

    def test_new_project_is_valid_and_preserves_authored_braces(self):
        root = self.create()
        report = audit.audit(root)
        self.assertTrue(report["passed"], report)
        from adkl.instruction import compile_config
        doc = yaml.safe_load((root / "agent.yaml").read_text())
        self.assertIn("{literally}", compile_config(root, doc).text)
        self.assertFalse((root / ".env").exists())
        self.assertIn("agent.py", yaml.safe_load((root / "release.yaml").read_text())["files"])

    def test_refuses_collisions_and_traversal_without_touching_existing_project(self):
        root = self.create()
        original = (root / "instruction.md").read_bytes()
        for name in ("test_agent", "../escape", "/escape", "bad-name", "", "a" * 65):
            with self.assertRaises(ValueError):
                projects.create(self.workspace, name, "A sufficiently specific purpose")
        self.assertEqual((root / "instruction.md").read_bytes(), original)
        self.assertEqual(list(self.workspace.iterdir()), [root])

    def test_attach_preserves_host_and_rejects_silent_config_overwrite(self):
        (self.workspace / "host_agent.py").write_text("# host code\n")
        projects.attach(self.workspace, "host_agent:build")
        self.assertEqual((self.workspace / "host_agent.py").read_text(), "# host code\n")
        self.assertEqual(audit.audit(self.workspace)["evidence"], "host_descriptor_only")
        with self.assertRaises(FileExistsError):
            projects.attach(self.workspace, "host_agent:other")

    def test_release_excludes_undeclared_inputs_and_rejects_secret_paths_and_symlinks(self):
        root = self.create()
        (root / ".env").write_text("SYNTHETIC=private")
        (root / "release.yaml").write_text(yaml.safe_dump({"version": 1, "files": ["agent.yaml"]}))
        self.assertEqual(set(release.collect(root)), {"agent.yaml"})
        for rel in (".env", "../outside", "keys/private.pem"):
            (root / "release.yaml").write_text(yaml.safe_dump({"version": 1, "files": [rel]}))
            with self.assertRaises(ValueError):
                release.collect(root)
        (root / "linked.md").symlink_to(root / "instruction.md")
        (root / "release.yaml").write_text(yaml.safe_dump({"version": 1, "files": ["linked.md"]}))
        with self.assertRaisesRegex(ValueError, "symlinks"):
            release.collect(root)

    def test_bundle_checks_packaged_tree_and_detects_tampering(self):
        root = self.create()
        (root / "vendor").mkdir()
        wheel = root / "vendor" / release.wheel_name()
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("adkl/agent_runtime.py", "# synthetic wheel, no runtime claim")
        (root / "uv.lock").write_text("# fixture lock\n")
        with patch("adkl.release.shutil.which", return_value="uv"), patch(
            "adkl.release.subprocess.run") as run:
            run.return_value.returncode = 0
            target = self.workspace / "release.zip"
            manifest = release.build(root, target)
            self.assertEqual(manifest, release.verify(target))
            self.assertFalse(manifest["productionReady"])
            with zipfile.ZipFile(target) as archive:
                self.assertIn(b"adk\", \"api_server", archive.read("Dockerfile"))
                self.assertNotIn("project/.env", archive.namelist())
                entries = {n: archive.read(n) for n in archive.namelist()}
            tampered = self.workspace / "tampered.zip"
            entries["project/instruction.md"] = b"changed after build"
            with zipfile.ZipFile(tampered, "w") as archive:
                for n, data in entries.items():
                    archive.writestr(n, data)
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.verify(tampered)
            declaration = yaml.safe_load((root / "release.yaml").read_text())
            declaration["files"].remove("instruction.md")
            (root / "release.yaml").write_text(yaml.safe_dump(declaration))
            with self.assertRaisesRegex(ValueError, "Packaged agent fails audit"):
                release.build(root, self.workspace / "missing.zip")

    def test_invalid_host_descriptor_cannot_claim_factory_features(self):
        projects.attach(self.workspace, "host_agent:build")
        doc = yaml.safe_load((self.workspace / "agent.yaml").read_text())
        doc["features"] = {"pipeline": ["history_slicing"]}
        (self.workspace / "agent.yaml").write_text(yaml.safe_dump(doc))
        self.assertFalse(audit.audit(self.workspace)["passed"])

    def test_host_descriptor_cannot_be_released_as_shared_factory(self):
        projects.attach(self.workspace, "host_agent:build")
        (self.workspace / "release.yaml").write_text(yaml.safe_dump({
            "version": 1, "files": ["agent.yaml"]}))
        with self.assertRaisesRegex(ValueError, "existing host deployment"):
            release.build(self.workspace, self.workspace / "release.zip")
