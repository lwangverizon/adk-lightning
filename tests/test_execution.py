"""Isolation contracts use synthetic projects, credentials and local processes."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from adkl import execution, server, toolbind
from tests.support.projects import WORKER_CODE, make_project


class TestProfiles(unittest.TestCase):
    def test_explicit_content_ignores_docs_but_tracks_code_prompts_and_skill_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            doc["source"] = {"content": ["prompts"]}
            profile.write_text(yaml.safe_dump(doc))
            (root / "prompts").mkdir()
            (root / "skills/math/references").mkdir(parents=True)
            (root / "skills/math/SKILL.md").write_text("Teach math")
            (root / "agent.yaml").write_text(yaml.safe_dump({"agent": {"instruction": {
                "template": "identity.txt", "slots": {"POLICY": "policy.md"}}}}))
            (root / "identity.txt").write_text("Initial prompt")
            before = execution.source_digest(root)
            (root / "ADKL_MIGRATION.md").write_text("Documentation only")
            (root / ".env").write_text("GOOGLE_API_KEY=synthetic")
            self.assertEqual(execution.source_digest(root), before)
            for name in ("agent.py", "prompts/persona.md", "identity.txt", "policy.md",
                         "skills/math/SKILL.md", "skills/math/references/rules.txt", "data.json"):
                before = execution.source_digest(root)
                (root / name).write_text("Changed runtime input")
                self.assertNotEqual(execution.source_digest(root), before, name)
                before = execution.source_digest(root)
                (root / name).unlink()
                self.assertNotEqual(execution.source_digest(root), before, name)
                (root / name).write_text("Restored runtime input")

    def test_content_policy_is_opt_in_and_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            before = execution.source_digest(root)
            (root / "arbitrary.md").write_text("A factory might read this")
            self.assertNotEqual(execution.source_digest(root), before)
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            for content in ("prompts", ["../outside"], [".env"], ["/tmp/secret"], ["*.md"], ["build"]):
                doc["source"] = {"content": content}
                profile.write_text(yaml.safe_dump(doc))
                with self.assertRaises(execution.ExecutionError):
                    execution.profiles(root)
            doc["source"] = {"content": ["linked"]}
            profile.write_text(yaml.safe_dump(doc))
            # Declared content reached through an in-project link is tracked at
            # the file it names; the walk never enters the link itself.
            (root / "linked").symlink_to(root / "arbitrary.md")
            before = execution.source_digest(root)
            (root / "arbitrary.md").write_text("The declared, linked file changed")
            self.assertNotEqual(execution.source_digest(root), before)
            with tempfile.TemporaryDirectory() as outside:
                (pathlib.Path(outside) / "arbitrary.md").write_text("Outside this project")
                (root / "linked").unlink()
                (root / "linked").symlink_to(pathlib.Path(outside) / "arbitrary.md")
                with self.assertRaisesRegex(execution.ExecutionError, "linked does not"):
                    execution.source_digest(root)

    def test_custom_credential_files_stay_outside_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            doc["profiles"]["local"]["env_file"] = "credentials.json"
            doc["source"] = {"content": []}
            profile.write_text(yaml.safe_dump(doc))
            before = execution.source_digest(root)
            (root / "credentials.json").write_text("GOOGLE_API_KEY=synthetic")
            self.assertEqual(execution.source_digest(root), before)
            doc["source"]["content"] = ["credentials.json"]
            profile.write_text(yaml.safe_dump(doc))
            with self.assertRaisesRegex(execution.ExecutionError, "credential files"):
                execution.source_digest(root)

    def test_declared_runtime_state_churns_without_changing_project_identity(self):
        """A token the app refreshes at an ordinary path is state, not source.

        `pyvenv.cfg` exempts a churning dependency tree, and `env_file` exempts a
        dotenv, but neither reaches the case that surfaced this: a host refreshing
        an OIDC token into `oidc_token.json` at its project root. A `.json`, not
        dot-prefixed, so the walk hashed it -- and any `uv run` in another terminal
        then changed project identity mid-session, so the console refused to reuse
        its worker and showed "Project changed" on every second action.
        """
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = make_project(pathlib.Path(tmp))
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            (root / "oidc_token.json").write_text('{"access_token": "first"}')
            (root / "cache").mkdir()
            (root / "cache/entry.json").write_text("{}")

            tracked = execution.source_digest(root)
            doc["source"] = {"exclude": ["oidc_token.json", "cache"]}
            profile.write_text(yaml.safe_dump(doc))
            digest = execution.source_digest(root)
            self.assertNotEqual(digest, tracked, "declaring the exclusion must change identity once")

            # Refreshing the excluded bytes -- the whole point -- must not.
            (root / "oidc_token.json").write_text('{"access_token": "second"}')
            (root / "cache/entry.json").write_text('{"warm": true}')
            (root / "cache/nested").mkdir()
            (root / "cache/nested/more.py").write_text("x = 1\n")
            self.assertEqual(execution.source_digest(root), digest)

            # An excluded tree is not walked at all, so what a cache happens to
            # contain cannot make the project un-inspectable. A link out of the
            # project is refused by name anywhere the walk still reaches, and a
            # download cache is exactly where one shows up.
            (pathlib.Path(outside) / "payload.txt").write_text("not project source")
            (root / "cache/escape").symlink_to(outside)
            self.assertEqual(execution.source_digest(root), digest)

            # Real source next to it still decides identity.
            (root / "module.py").write_text("y = 2\n")
            self.assertNotEqual(execution.source_digest(root), digest)

            # The declaration is what earns the exemption, not the filename.
            doc["source"] = {"exclude": ["cache"]}
            profile.write_text(yaml.safe_dump(doc))
            reverted = execution.source_digest(root)
            (root / "oidc_token.json").write_text('{"access_token": "third"}')
            self.assertNotEqual(execution.source_digest(root), reverted)

    def test_excluding_alone_does_not_silently_stop_tracking_markdown(self):
        """`source.exclude` answers a different question from `source.content`.

        A project with no `source` block tracks Markdown; declaring `content` opts
        out of that. Reading an `exclude`-only block as "explicit content mode"
        would drop every prompt and skill body out of project identity as a side
        effect of naming one cache file -- a staleness hole, and invisible,
        because the digest still changes whenever code changes.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            # Deliberately not a SKILL.md, and not at the root: the walk adds any
            # directory holding a SKILL.md to `content`, which tracks it in either
            # mode -- so a SKILL.md here would pass whatever the mode was.
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text("# guide\n")
            doc["source"] = {"exclude": ["runtime.json"]}
            profile.write_text(yaml.safe_dump(doc))

            digest = execution.source_digest(root)
            (root / "docs/guide.md").write_text("# guide, edited\n")
            self.assertNotEqual(execution.source_digest(root), digest,
                                "exclude-only must leave Markdown tracking on")

    def test_contradictory_and_unsafe_exclusions_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            profile = root / "execution.yaml"
            doc = yaml.safe_load(profile.read_text())
            for exclude in (["/etc/passwd"], ["../outside"], ["logs/*.json"], [".ssh"],
                            ["build"], [""], [None], ["a\\b"], "oidc_token.json"):
                doc["source"] = {"exclude": exclude}
                profile.write_text(yaml.safe_dump(doc))
                with self.assertRaises(execution.ExecutionError, msg=repr(exclude)):
                    execution.source_digest(root)
            # Naming the same bytes twice cannot be resolved by check order.
            doc["source"] = {"content": ["prompts"], "exclude": ["prompts"]}
            profile.write_text(yaml.safe_dump(doc))
            with self.assertRaisesRegex(execution.ExecutionError, "content and exclude"):
                execution.source_digest(root)
            doc["source"] = {"unknown": []}
            profile.write_text(yaml.safe_dump(doc))
            with self.assertRaises(execution.ExecutionError):
                execution.source_digest(root)

    def test_ambient_credentials_and_python_injection_are_not_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            (root / ".env").write_text("GOOGLE_API_KEY=project-secret\nPYTHONPATH=/evil\n")
            name, entry = execution.profile(root)
            got = execution.worker_environment(root, name, entry, {
                "GOOGLE_API_KEY": "other-project", "DATABASE_URL": "other-db",
                "PYTHONPATH": "/evil", "HOME": "/another-user"})
            self.assertEqual(got["GOOGLE_API_KEY"], "project-secret")
            self.assertNotIn("DATABASE_URL", got)
            self.assertNotIn("PYTHONPATH", got)
            self.assertTrue(got["HOME"].startswith(str(root)))

    def test_inheritance_is_explicit_and_missing_secret_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            name, entry = execution.profile(root)
            parent = {"GOOGLE_API_KEY": "explicit-secret"}
            self.assertNotIn("GOOGLE_API_KEY", execution.worker_environment(root, name, entry, parent))
            entry["inherit"] = ["GOOGLE_API_KEY"]
            self.assertEqual(execution.worker_environment(root, name, entry, parent)["GOOGLE_API_KEY"], "explicit-secret")

    def test_remote_target_refuses_before_spawn_or_reading_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp), target="remote")
            with patch.object(execution.subprocess, "Popen") as spawn, patch.object(execution, "dotenv") as read:
                with self.assertRaisesRegex(execution.ExecutionError, "approved customer-hosted"):
                    execution.Worker(root).request("status")
                spawn.assert_not_called()
                read.assert_not_called()

    def test_external_tool_repo_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            (root / "agent.yaml").write_text("tools: {repo: ../other-project}\n")
            with self.assertRaises(execution.ExecutionError):
                execution.require_local(root)

    def test_invalid_profile_paths_and_env_names_are_rejected(self):
        for field, value in (("env_file", "../.env"), ("python", "../bin/python"),
                             ("env", {"PYTHONPATH": "/evil"}), ("secrets", ["HOME"])):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = make_project(pathlib.Path(tmp))
                path = root / "execution.yaml"
                doc = yaml.safe_load(path.read_text())
                doc["profiles"]["local"][field] = value
                path.write_text(yaml.safe_dump(doc))
                with self.assertRaises(execution.ExecutionError):
                    execution.profiles(root)

    def test_tool_preparation_does_not_change_parent_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".env").write_text("ADKL_TEST_SECRET=should-stay-local\n")
            before = dict(os.environ)
            try:
                toolbind.prepare(root)
                self.assertEqual(dict(os.environ), before)
            finally:
                sys.path.remove(str(root.resolve()))

    def test_changed_configuration_stops_an_existing_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            worker = execution.Worker(root)
            (root / "execution.yaml").write_text("invalid: changed\n")
            with patch.object(execution.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(execution.ExecutionError, "configuration changed"):
                    worker.request("send", text="must not run")
                spawn.assert_not_called()
            self.assertIsNone(worker.process)

    def test_malformed_profile_does_not_echo_yaml_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "execution.yaml").write_text("secret: [sensitive-fixture\n")
            with self.assertRaises(execution.ExecutionError) as error:
                execution.profiles(root)
            self.assertNotIn("sensitive-fixture", str(error.exception))


class TestSourceIdentityWalk(unittest.TestCase):
    """The directory-symlink and virtualenv rules, which decide whether a real
    host can be inspected at all.

    `test_content_policy_is_opt_in_and_rejects_unsafe_paths` above covers a
    *file* symlink named in `source.content`, which is a different code path.
    Nothing covered the `os.walk` pass, and the two behaviours below were lost
    from this repo once already -- they survived only in a host's vendored wheel,
    so the tree could not inspect that host while the wheel could. These tests
    exist so the next rebuild cannot drop them silently.
    """

    def test_an_in_project_directory_symlink_is_content_sharing_not_an_escape(self):
        """Refusing this makes a legitimate layout un-inspectable with no recovery.

        Hosts share one skill's assets across channel variants by linking
        directories inside the project (six committed instances in the host that
        found this). The guard exists to stop content *outside* the project from
        deciding the digest; a link that stays inside decides nothing, because
        `os.walk` does not follow it and hashes the target at its own canonical
        path in the same pass.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            (root / "skills/base/assets").mkdir(parents=True)
            (root / "skills/base/assets/logo.txt").write_text("shared")
            (root / "skills/variant").mkdir(parents=True)
            (root / "skills/variant/assets").symlink_to(root / "skills/base/assets")

            digest = execution.source_digest(root)          # must not raise
            self.assertEqual(execution.source_digest(root), digest)

            # Re-pointing the link is a content change even though no file moved,
            # so identity must follow it -- otherwise a swapped variant reuses a
            # stale worker.
            (root / "skills/other").mkdir()
            (root / "skills/other/logo.txt").write_text("different")
            (root / "skills/variant/assets").unlink()
            (root / "skills/variant/assets").symlink_to(root / "skills/other")
            self.assertNotEqual(execution.source_digest(root), digest)

    def test_a_directory_symlink_leaving_the_project_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = make_project(pathlib.Path(tmp))
            (pathlib.Path(outside) / "payload.txt").write_text("not project source")
            (root / "escape").symlink_to(outside)
            with self.assertRaisesRegex(execution.ExecutionError,
                                        "must resolve to tracked project files: escape does not"):
                execution.source_digest(root)

    def test_a_dangling_or_looping_link_does_not_make_a_project_uninspectable(self):
        """Neither reaches the directory guard, and neither may break the digest.

        `os.walk` classifies by `is_dir()`, which *follows* links -- so a dangling
        or self-referential link is False and lands in `files`, never `dirs`.
        `_escapes` is therefore never consulted for one, and its `OSError` branch
        is unreachable from this walk (defensive only; `resolve()` does not even
        raise on a loop here). What is worth pinning is the outcome: a stray link
        of either shape is common in a working tree and must not cost the host
        its ability to be inspected.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            (root / "dangling").symlink_to(root / "never_created")
            (root / "loop").symlink_to(root / "loop")
            digest = execution.source_digest(root)          # must not raise
            self.assertEqual(execution.source_digest(root), digest)

    def test_a_virtualenv_inside_the_project_is_not_project_source(self):
        """Installed dependencies are neither source nor stable.

        Detect the PEP 405 `pyvenv.cfg` marker rather than a directory name.
        Environment files change independently of source and must not invalidate
        the worker's project identity.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            digest = execution.source_digest(root)

            env = root / "not_dot_prefixed_env"
            (env / "lib").mkdir(parents=True)
            (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
            (env / "lib/installed_dependency.py").write_text("x = 1\n")
            self.assertEqual(execution.source_digest(root), digest)

            # Rewriting the env -- what `uv run` does -- must not change identity.
            (env / "lib/installed_dependency.py").write_text("x = 2\n")
            self.assertEqual(execution.source_digest(root), digest)

            # The marker is what earns the exemption, not the directory name.
            (env / "pyvenv.cfg").unlink()
            self.assertNotEqual(execution.source_digest(root), digest)

    def test_a_code_file_link_is_tracked_inside_the_project_and_refused_outside(self):
        """The same rule as directories: only a link the walk can already read."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            (root / "shared").mkdir()
            (root / "shared/rules.py").write_text("LIMIT = 1\n")
            (root / "alias.py").symlink_to(root / "shared/rules.py")
            digest = execution.source_digest(root)
            (root / "shared/rules.py").write_text("LIMIT = 2\n")
            self.assertNotEqual(execution.source_digest(root), digest)
            with tempfile.TemporaryDirectory() as outside:
                unowned = pathlib.Path(outside) / "rules.py"
                unowned.write_text("LIMIT = 99\n")
                (root / "alias.py").unlink()
                (root / "alias.py").symlink_to(unowned)
                with self.assertRaisesRegex(execution.ExecutionError, "alias.py does not"):
                    execution.source_digest(root)
            # A tracked path whose bytes this walk never reads is refused too:
            # the content could change without changing project identity.
            (root / "alias.py").unlink()
            (root / "node_modules/installed").mkdir(parents=True)
            (root / "node_modules/installed/rules.py").write_text("LIMIT = 3\n")
            (root / "alias.py").symlink_to(root / "node_modules/installed/rules.py")
            with self.assertRaisesRegex(execution.ExecutionError, "alias.py does not"):
                execution.source_digest(root)


class TestWorkerProcesses(unittest.TestCase):
    def test_projects_with_identical_modules_use_separate_processes_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            platform = base / "platform/adkl"
            platform.mkdir(parents=True)
            (platform / "worker.py").write_text(WORKER_CODE)
            one, two = make_project(base / "one"), make_project(base / "two")
            for root in (one, two):
                (root / "collision.py").write_text(f"VALUE = {root.name!r}\n")
                (root / ".env").write_text(f"GOOGLE_API_KEY={root.name}-private-key\n")
            workers = [execution.Worker(one), execution.Worker(two)]
            before = dict(os.environ)
            try:
                with patch.object(execution, "PLATFORM", platform.parent), patch.dict(os.environ, {"DATABASE_URL": "parent-db"}):
                    a = workers[0].request("status")
                    b = workers[1].request("status")
                    again = workers[0].request("status")
                self.assertEqual((a["value"], b["value"], again["value"]), ("one", "two", "one"))
                self.assertNotEqual(a["execution"]["pid"], b["execution"]["pid"])
                self.assertEqual(a["secret"], "[REDACTED]")
                self.assertIsNone(b["ambient"])
                self.assertEqual(a["cwd"], str(one.resolve()))
                self.assertEqual(dict(os.environ), before)
            finally:
                processes = [w.process for w in workers]
                for worker in workers:
                    worker.close()
                self.assertTrue(all(p.poll() is not None for p in processes if p))

    def test_timeout_terminates_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project(pathlib.Path(tmp))
            platform = root / "platform/adkl"
            platform.mkdir(parents=True)
            (platform / "worker.py").write_text(WORKER_CODE)
            (root / "collision.py").write_text("VALUE = 'x'\n")
            worker = execution.Worker(root, timeout=0.1)
            with patch.object(execution, "PLATFORM", platform.parent):
                with self.assertRaisesRegex(execution.ExecutionError, "timed out"):
                    worker.request("hang")
            self.assertIsNone(worker.process)

    def test_switching_agents_closes_existing_worker(self):
        old = (server._workspace.root, server._workspace.chat, server._workspace.agents, server._workspace.profile)
        try:
            from unittest.mock import Mock
            current = Mock()
            server._workspace.chat = current
            server._workspace.profile = "staging"
            server._workspace.agents = [{"id": "next", "path": "/next"}]
            server.select_agent("next")
            current.close.assert_called_once()
            self.assertIsNone(server._workspace.chat)
            self.assertIsNone(server._workspace.profile)
        finally:
            server._workspace.root, server._workspace.chat, server._workspace.agents, server._workspace.profile = old
