"""Review evidence must detect modified, added, missing, and linked content."""
import pathlib
import tempfile
import unittest

from scripts.ci.artifact_manifest import inventory, verify


class ArtifactManifestTests(unittest.TestCase):
    def test_detects_tree_changes_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            index = root / "index.html"
            index.write_text("review", encoding="utf-8")
            manifest = {"schemaVersion": 1, "kind": "lightning-console-review",
                        "productionReady": False, "files": inventory(root)}
            verify(root, manifest)
            index.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify(root, manifest)
            index.write_text("review", encoding="utf-8")
            extra = root / "extra.js"
            extra.write_text("new", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify(root, manifest)
            extra.unlink()
            index.unlink()
            with self.assertRaises(ValueError):
                verify(root, manifest)
            index.write_text("review", encoding="utf-8")
            extra.symlink_to(index)
            with self.assertRaises(ValueError):
                inventory(root)

    def test_cannot_relabel_review_as_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "index.html").write_text("review", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify(root, {"schemaVersion": 1, "kind": "lightning-console-review",
                              "productionReady": True, "files": inventory(root)})
