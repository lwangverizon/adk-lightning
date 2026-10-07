"""Release trust boundaries and retry behavior, without registry credentials."""
import importlib.util
import json
import io
from pathlib import Path
import tempfile
import tarfile
import unittest
import zipfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("sdk_release", Path(__file__).resolve().parents[1] / "scripts/ci/sdk_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class Registry:
    base = "https://gitlab.example/api/v4/projects/1"

    def __init__(self):
        self.files = {}
        self.writes = []

    def request(self, method, path, data=None, json_body=False):
        if method == "GET":
            return self.files.get(path)
        self.writes.append(path)
        if path == "/releases":
            record = json.loads(data)
            record["commit"] = {"id": "abc"}
            self.files["/releases/v0.1.0"] = json.dumps(record).encode()
        else:
            self.files[path] = data


class TestSdkRelease(unittest.TestCase):
    def test_release_requires_slim_distributions_and_safe_separate_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = root / "adk_lightning-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("adkl/agent_runtime.py", "")
                archive.writestr("adk_lightning-0.1.0.dist-info/METADATA", "Name: adk-lightning\nVersion: 0.1.0\n")
            source = root / "adk_lightning-0.1.0.tar.gz"
            with tarfile.open(source, "w:gz") as archive:
                member = tarfile.TarInfo("adk_lightning-0.1.0/adkl/agent_runtime.py")
                archive.addfile(member, io.BytesIO())
            console = root / "adkl_console-0.1.0.zip"
            with self.assertRaisesRegex(ValueError, "console archive"):
                release.artifacts(root, "0.1.0")
            with zipfile.ZipFile(console, "w") as archive:
                archive.writestr("web/index.html", "<html></html>")
            self.assertEqual(release.artifacts(root, "0.1.0"), (wheel, source, console))
            with zipfile.ZipFile(console, "a") as archive:
                archive.writestr("web/../escape", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe console"):
                release.artifacts(root, "0.1.0")
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("adkl/web/index.html", "unwanted assets")
            with self.assertRaisesRegex(ValueError, "slim SDK"):
                release.artifacts(root, "0.1.0")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "RELEASE_NOTES.md").write_text("Fixture notes")
        self.manifest = {"schemaVersion": 1, "kind": "adkl-sdk-release", "version": "0.1.0",
            "tag": "v0.1.0", "sourceCommit": "abc", "pipeline": "https://ci/1",
            "workingTreeDirty": False, "files": {"RELEASE_NOTES.md": release.digest(b"Fixture notes")}}
        self.save()
        self.env = {"CI_COMMIT_REF_PROTECTED": "true", "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_TAG": "v0.1.0", "CI_COMMIT_SHA": "abc", "CI_PIPELINE_URL": "https://ci/1"}
        self.registry = Registry()

    def save(self):
        (self.root / "release-manifest.json").write_text(json.dumps(self.manifest))

    def test_tag_normalization_rejects_ambiguous_versions(self):
        self.assertEqual(release.tag_version("v1.2.3-rc.4"), "1.2.3rc4")
        for tag in ("main", "v01.2.3", "v1.2", "v1.2.3-rc.0", "v1.2.3+build"):
            with self.assertRaises(ValueError):
                release.tag_version(tag)

    def test_download_redirects_strip_auth_and_writes_never_redirect(self):
        handler = release.SafeDownloadRedirect()
        request = release.urllib.request.Request("https://gitlab.example/file", headers={"JOB-TOKEN": "synthetic"})
        redirected = handler.redirect_request(request, None, 302, "", {}, "https://storage.example/file")
        self.assertEqual(redirected.headers, {})
        for method, destination in (("PUT", "https://storage.example/file"), ("GET", "http://storage.example/file")):
            request = release.urllib.request.Request("https://gitlab.example/file", method=method)
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "", {}, destination)

    @patch.object(release, "artifacts")
    def test_publish_is_idempotent_and_rejects_changed_existing_bytes(self, _):
        release.publish(self.root, self.env, self.registry)
        writes = list(self.registry.writes)
        release.publish(self.root, self.env, self.registry)
        self.assertEqual(writes, self.registry.writes)
        self.registry.files["/packages/generic/adkl-sdk/0.1.0/RELEASE_NOTES.md"] = b"different"
        with self.assertRaisesRegex(ValueError, "different bytes"):
            release.publish(self.root, self.env, self.registry)

    @patch.object(release, "artifacts")
    def test_untrusted_or_wrong_candidate_never_writes(self, _):
        for key, value in (("CI_COMMIT_REF_PROTECTED", "false"), ("CI_PIPELINE_SOURCE", "web"),
                           ("CI_COMMIT_TAG", "v0.2.0"), ("CI_COMMIT_SHA", "other"),
                           ("CI_PIPELINE_URL", "https://ci/2")):
            with self.assertRaises(ValueError):
                release.publish(self.root, {**self.env, key: value}, self.registry)
        self.assertEqual(self.registry.writes, [])

    @patch.object(release, "artifacts")
    def test_tampering_and_extra_files_fail_before_publication(self, _):
        (self.root / "RELEASE_NOTES.md").write_text("Tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.publish(self.root, self.env, self.registry)
        (self.root / "RELEASE_NOTES.md").write_text("Fixture notes")
        (self.root / "unexpected.env").write_text("synthetic")
        with self.assertRaisesRegex(ValueError, "inventory"):
            release.publish(self.root, self.env, self.registry)
        self.assertEqual(self.registry.writes, [])

    def test_version_bump_propagates_to_new_projects(self):
        from adkl import projects
        with patch("adkl.projects.sdk_version", return_value="0.2.0rc1"), patch(
                "adkl.projects.wheel_name", return_value="adk_lightning-0.2.0rc1-py3-none-any.whl"):
            root = projects.create(self.root, "version_test", "Help summarize the supplied user notes.")
        project = (root / "pyproject.toml").read_text()
        self.assertIn("adk-lightning[runtime]==0.2.0rc1", project)
        self.assertIn("adk_lightning-0.2.0rc1-py3-none-any.whl", project)

    @patch.object(release, "artifacts")
    def test_partial_upload_resumes_without_replacing_completed_files(self, _):
        original = self.registry.request
        def fail_manifest(method, path, data=None, json_body=False):
            if method == "PUT" and path.endswith("release-manifest.json"):
                raise OSError("simulated interrupted upload")
            return original(method, path, data, json_body)
        with patch.object(self.registry, "request", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                release.publish(self.root, self.env, self.registry)
        release.publish(self.root, self.env, self.registry)
        notes = "/packages/generic/adkl-sdk/0.1.0/RELEASE_NOTES.md"
        self.assertEqual(self.registry.writes.count(notes), 1)
        self.assertIn("/releases/v0.1.0", self.registry.files)


if __name__ == "__main__":
    unittest.main()
