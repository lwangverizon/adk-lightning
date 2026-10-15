"""MR-derived Doctor rules: each case reproduces a merged host regression."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zipfile

import yaml

from adkl import doctor, doctor_host, execution
from adkl.doctor_variants import collect, collect_references, references, sections


def skill(root, relative, name, body, tools=(), **metadata):
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {"name": name, "description": f"{name} help",
                   "metadata": {"adk_additional_tools": list(tools), **metadata}}
    path.write_text("---\n" + yaml.safe_dump(frontmatter) + "---\n" + body)
    return path


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


BILLING = """# Billing

## Cart Review & Explanation
Explain each charge from the cart summary.

## Gotchas
Never invent credits.
"""


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.doc = {"version": 1, "agent": {"name": "host", "factory": "agent:build"},
                    "authoring": {"skills": {"billing": "skills/billing/SKILL.md"}}}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(self.doc))
        (self.root / "agent.py").write_text('raise RuntimeError("must not import")')

    def scan(self, **kwargs):
        with patch.object(execution, "Worker", side_effect=AssertionError("no host import")):
            return doctor.diagnose(self.root, **kwargs)


class TestPartialVariantEdit(Project):
    """Editing only some served copies leaves a stale section in another variant."""

    def setUp(self):
        super().setUp()
        for relative in ("skills/billing", "skills/uat/billing", "skills/mva/billing"):
            skill(self.root, relative, "billing", BILLING)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "base")

    def edit(self, relative):
        path = self.root / relative / "SKILL.md"
        path.write_text(path.read_text().replace(
            "Explain each charge", "Explain each charge; a positive credit-named item is a charge"))

    def test_partial_edit_names_unchanged_copy_until_all_match(self):
        self.edit("skills/billing")
        self.edit("skills/uat/billing")
        raw, note = collect(self.root, self.doc, "main")
        drift = [f for f in raw if f["rule"] == "SKILL_VARIANT_PARTIAL_EDIT"]
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["source"], "skills/mva/billing/SKILL.md")
        self.assertIn('"Cart Review & Explanation"', drift[0]["message"])
        self.assertIn("Section bodies compared against main", note)
        report = self.scan(base_ref="main")
        self.assertIn("SKILL_VARIANT_PARTIAL_EDIT", {f["rule"] for f in report["findings"]})
        self.assertEqual(report["context"]["baseRef"], "main")
        self.assertIn("--base-ref main", doctor.render(report))
        self.edit("skills/mva/billing")
        self.assertEqual([f for f in collect(self.root, self.doc, "main")[0]
                          if f["rule"] == "SKILL_VARIANT_PARTIAL_EDIT"], [])

    def test_no_base_ref_keeps_previous_behavior(self):
        self.edit("skills/billing")
        raw, note = collect(self.root, self.doc)
        self.assertEqual(raw, [])
        self.assertIn("Bodies compared only with --base-ref", note)

    def test_previously_divergent_sections_and_new_copies_are_not_drift(self):
        skill(self.root, "skills/phx/billing", "billing", BILLING.replace("cart", "PHX cart"))
        self.edit("skills/billing")
        raw, _ = collect(self.root, self.doc, "main")
        drift = [f for f in raw if f["rule"] == "SKILL_VARIANT_PARTIAL_EDIT"]
        # The new PHX copy did not exist at the base and is not reported.
        self.assertEqual(len(drift), 1)
        self.assertNotIn("phx", drift[0]["message"])

    def test_invalid_base_ref_fails_the_check_not_the_scan(self):
        for ref in ("--output=/tmp/x", "main..HEAD", "missing-branch"):
            report = self.scan(base_ref=ref)
            check = next(c for c in report["checks"] if c["id"] == "skill_variants")
            self.assertEqual(check["status"], "error")
            self.assertNotIn(ref, check["detail"])

    def test_sections_ignore_fenced_headings_and_keep_repeats_apart(self):
        parsed = sections("intro\n```\n# not a heading\n```\n## Gotchas\na\n## Gotchas\nb\n")
        self.assertEqual(parsed["Gotchas"], "a")
        self.assertEqual(parsed["Gotchas (2)"], "b")
        self.assertIn("# not a heading", parsed["(preamble)"])


class TestSkillReferences(Project):
    """mva_a2ui plan-display redirected to device-shopping-and-offers, a VBG-only skill."""

    def test_unknown_redirect_is_reported_and_known_one_is_not(self):
        skill(self.root, "skills/plan-display", "plan-display",
              "For perks (use perk-display). For devices, route to `device-shopping-and-offers`.")
        skill(self.root, "skills/perk-display", "perk-display", "Plans (use plan-display).")
        raw, note = collect_references(self.root, self.doc)
        self.assertEqual([(f["rule"], f["skill"]) for f in raw],
                         [("SKILL_REFERENCE_UNKNOWN", "plan-display")])
        self.assertIn("device-shopping-and-offers", raw[0]["message"])
        self.assertNotIn("perk-display,", raw[0]["message"])
        self.assertIn("3 explicit skill redirects", note)

    def test_tools_examples_and_plain_mentions_are_not_redirects(self):
        text = textwrap.dedent("""\
            Use `render_single_selections` for the picker.
            The customer-device-upgrade skill covers this too.
            ```
            (use made-up-example)
            ```
            (see Gotchas)
            """)
        self.assertEqual(references(text), [])

    def test_description_redirects_are_checked(self):
        path = skill(self.root, "skills/plan-display", "plan-display", "Show plans.")
        text = path.read_text().replace("plan-display help", "Plans only (use cart-management-v2)")
        path.write_text(text)
        raw, _ = collect_references(self.root, self.doc)
        self.assertIn("cart-management-v2", raw[0]["message"])


def wheel(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock(root, entries):
    blocks = []
    for name, relative, digest in entries:
        blocks.append(textwrap.dedent(f'''\
            [[package]]
            name = "{name}"
            version = "0.1.0"
            source = {{ path = "{relative}" }}
            wheels = [
                {{ filename = "{Path(relative).name}", hash = "sha256:{digest}" }},
            ]
            '''))
    (root / "uv.lock").write_text("version = 1\n\n" + "\n".join(blocks))


class TestArtifacts(Project):
    """A lock upgrade must not leave the Dockerfile ARG on the old wheel."""

    NEW = "packages/adkl/3880650-background/adk_lightning-0.1.0-py3-none-any.whl"
    OLD = "packages/adkl/aca44f0-activation/adk_lightning-0.1.0-py3-none-any.whl"

    def installed(self):
        return {name: (Path(doctor_host.__file__).parent / Path(name).name).read_bytes()
                for name in doctor_host._installed_scanner()}

    def test_stale_dockerfile_arg_is_an_error(self):
        digest = wheel(self.root / self.NEW, self.installed())
        wheel(self.root / self.OLD, {"adkl/doctor.py": b"old"})
        lock(self.root, [("adk-lightning", self.NEW, digest)])
        (self.root / "Dockerfile").write_text(
            "ARG ADKL_WHL=" + self.OLD.removeprefix("packages/") + "\nCOPY packages/${ADKL_WHL} .\n")
        report = self.scan()
        rules = {f["rule"]: f for f in report["findings"]}
        self.assertIn("ARTIFACT_DOCKER_WHEEL_MISMATCH", rules)
        self.assertEqual(rules["ARTIFACT_DOCKER_WHEEL_MISMATCH"]["location"]["path"], "Dockerfile")
        self.assertFalse(report["passed"])
        self.assertNotIn("DOCTOR_SDK_DIFFERS_FROM_LOCK", rules)
        (self.root / "Dockerfile").write_text(
            "ARG ADKL_WHL=" + self.NEW.removeprefix("packages/") + "\n")
        fixed = self.scan(previous=report)
        self.assertNotIn("ARTIFACT_DOCKER_WHEEL_MISMATCH", {f["rule"] for f in fixed["findings"]})
        check = next(c for c in fixed["checks"] if c["id"] == "artifacts")
        self.assertEqual(check["status"], "passed")
        self.assertIn("matches the locked", check["detail"].replace("match the locked", "matches the locked"))

    def test_hash_missing_and_diagnostic_sdk_identity(self):
        digest = wheel(self.root / self.NEW, {"adkl/doctor.py": b"served"})
        lock(self.root, [("adk-lightning", self.NEW, digest),
                         ("google-adk", "packages/google_adk-2.9.0-py3-none-any.whl", "0" * 64)])
        raw, note, coverage = doctor_host.collect_artifacts(self.root)
        rules = {f["rule"] for f in raw}
        self.assertEqual(rules, {"ARTIFACT_WHEEL_MISSING", "DOCTOR_SDK_DIFFERS_FROM_LOCK"})
        self.assertIn("Doctor SDK differs", note)
        self.assertEqual(len(coverage), 2)
        (self.root / self.NEW).write_bytes(b"tampered")
        raw, _, _ = doctor_host.collect_artifacts(self.root)
        self.assertIn("ARTIFACT_WHEEL_HASH_MISMATCH", {f["rule"] for f in raw})

    def test_projects_without_path_wheels_are_not_checked(self):
        (self.root / "uv.lock").write_text("not toml [")
        report = self.scan()
        check = next(c for c in report["checks"] if c["id"] == "artifacts")
        self.assertEqual(check["status"], "not_checked")


HANDOFF = '''
def has_account_access_issue(context):
    for event in reversed(context.session.events):
        content = event.content
        if content.role != "user":
            continue
        return "locked" in content.parts[0].text
    return False


def fixed(context):
    for event in reversed(context.session.events):
        content = event.content
        if content.role != "user":
            continue
        if all(part.function_response for part in content.parts):
            continue
        return "locked" in content.parts[0].text
    return False


def inject(llm_request, parts):
    for content in reversed(llm_request.contents):
        if content.role == "user":  # doctor: ignore[HISTORY_ROLE_USER_SCAN]
            content.parts += parts
            break


def classify(content):
    return content.role == "user"


def prior_query(context):
    # unlock_policy_routing shape: the newest prior role=user content is a tool result.
    for event in reversed(getattr(context.session, "events", None) or []):
        content = getattr(event, "content", None)
        if content and content.role == "user":
            return content.parts[0].text
    return ""


def text_guarded(llm_request):
    for content in reversed(llm_request.contents):
        if content.role != "user" or not content.parts:
            continue
        for part in content.parts:
            if getattr(part, "text", None):
                return part.text
    return None


def negated_text(context):
    for event in reversed(context.session.events):
        if event.content.role == "user":
            text = event.content.parts[0].text
            if not text:
                return None
            return text


def agui(input_data):
    for message in reversed(input_data.messages):
        if message.role == "user":
            return message.content
'''


class TestHostCode(Project):
    """ADK tool results with role=user must not be mistaken for user turns."""

    def configure(self, include=("tools",)):
        (self.root / "doctor-host.json").write_text(
            '{"version": 1, "python": {"include": %s, "exclude": ["tools/tests"]}}'
            % str(list(include)).replace("'", '"'))

    def test_unconfigured_is_not_checked(self):
        report = self.scan()
        check = next(c for c in report["checks"] if c["id"] == "host_code")
        self.assertEqual(check["status"], "not_checked")

    def test_flags_only_unguarded_history_scans_without_importing(self):
        (self.root / "tools/tests").mkdir(parents=True)
        (self.root / "tools/handoff.py").write_text(HANDOFF)
        (self.root / "tools/tests/test_handoff.py").write_text(HANDOFF)
        (self.root / "tools/broken.py").write_text("def (:\n")
        # Python accepts a UTF-8 BOM; the scan must not skip such files.
        (self.root / "tools/bom.py").write_bytes(b"\xef\xbb\xbf" + HANDOFF.split("\n\n\ndef fixed")[0].encode())
        self.configure()
        report = self.scan()
        found = [f for f in report["findings"] if f["rule"] == "HISTORY_ROLE_USER_SCAN"]
        self.assertEqual([f["location"]["line"] for f in found
                          if f["location"]["path"] == "tools/bom.py"], [5])
        found = [f for f in found if f["location"]["path"] == "tools/handoff.py"]
        self.assertEqual(sorted((f["location"]["line"], f["message"].split("(")[0]) for f in found),
                         [(5, "has_account_access_issue"), (37, "prior_query"),
                          (54, "negated_text")])
        found = [f for f in found if f["location"]["line"] == 5]
        self.assertEqual(found[0]["location"], {"path": "tools/handoff.py", "line": 5})
        self.assertIn("has_account_access_issue()", found[0]["message"])
        self.assertIn("tools/handoff.py:5", doctor.render(report))
        check = next(c for c in report["checks"] if c["id"] == "host_code")
        self.assertIn("1 files skipped", check["detail"])
        # Shifting lines does not create a new finding identity.
        (self.root / "tools/handoff.py").write_text("\n\n" + HANDOFF)
        moved = self.scan(previous=report)
        self.assertIn(found[0]["id"], moved["comparison"]["remaining"])
        moved_finding = next(f for f in moved["findings"] if f["id"] == found[0]["id"])
        self.assertEqual(moved_finding["location"]["line"], 7)

    def test_invalid_configuration_is_a_failed_check(self):
        (self.root / "doctor-host.json").write_text('{"version": 1, "python": {"include": ["../x"]}}')
        report = self.scan()
        check = next(c for c in report["checks"] if c["id"] == "host_code")
        self.assertEqual(check["status"], "error")


if __name__ == "__main__":
    unittest.main()
