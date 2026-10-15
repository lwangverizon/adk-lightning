"""Doctor serving and environment regression cases (lightweight lane, no ADK).

Each case reproduces the shape of a merged host fix; the runtime lane covers the
serving import order with real ADK objects (tests/runtime/test_startup_order_native.py).
"""
import json
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import yaml

from adkl import agent_schema, doctor, doctor_environment, execution, lint_cost, skill_lint
from adkl.doctor_variants import collect


def skill(root, relative, name, description, body="Use the tools.\n"):
    path = root / relative / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {"name": name, "description": description,
                   "metadata": {"adk_additional_tools": []}}
    path.write_text("---\n" + yaml.safe_dump(frontmatter, width=10_000) + "---\n" + body)
    return path


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.doc = {"version": 1, "agent": {"name": "host", "factory": "agent:build"},
                    "authoring": {"skills": {"account": "skills/account/SKILL.md"}}}
        (self.root / "agent.yaml").write_text(yaml.safe_dump(self.doc))
        (self.root / "agent.py").write_text('raise RuntimeError("must not import")')

    def scan(self, **kwargs):
        with patch.object(execution, "Worker", side_effect=AssertionError("no host import")):
            return doctor.diagnose(self.root, **kwargs)


class TestDescriptionLimit(Project):
    """An alternate served copy exceeds ADK's description-length limit."""

    def test_check_one_mirrors_adk_limit(self):
        at_limit = skill_lint.check_one("account", {"name": "account", "description": "d" * 1024}, "", set())
        over = skill_lint.check_one("account", {"name": "account", "description": "d" * 1025}, "", set())
        self.assertNotIn("DESCRIPTION_TOO_LONG", [rule for _s, rule, _m in at_limit])
        self.assertIn(("ERROR", "DESCRIPTION_TOO_LONG",
                       "description is 1025 characters; ADK rejects more than 1024, "
                       "so loading this skill raises"), over)

    def test_served_copy_is_error_and_unmapped_twin_is_warning(self):
        skill(self.root, "skills/account", "account", "a" * 1030)
        skill(self.root, "skills/mva/account", "account", "m" * 1027)
        skill(self.root, "skills/uat/account", "account", "short and fine")
        report = self.scan()
        found = sorted((f["severity"], f["location"]["path"], f["check"]) for f in report["findings"]
                       if f["rule"] == "DESCRIPTION_TOO_LONG")
        self.assertEqual(found, [
            ("ERROR", "skills/account/SKILL.md", "mapped_content"),
            ("WARN", "skills/mva/account/SKILL.md", "skill_variants")])
        self.assertFalse(report["passed"])

    def test_trimming_keeps_identity_until_resolved(self):
        skill(self.root, "skills/account", "account", "a" * 1100)
        before = self.scan()
        skill(self.root, "skills/account", "account", "a" * 1050)
        partial = self.scan(previous=before)
        self.assertEqual(partial["comparison"]["new"], [])
        skill(self.root, "skills/account", "account", "a" * 1000)
        fixed = self.scan(previous=partial)
        self.assertEqual(len(fixed["comparison"]["resolved"]), 1)
        self.assertFalse(any(f["rule"] == "DESCRIPTION_TOO_LONG" for f in fixed["findings"]))

    def test_variant_scan_skips_copies_content_checks_cover(self):
        skill(self.root, "skills/account", "account", "a" * 1030)
        raw, _note = collect(self.root, self.doc)
        self.assertEqual([f for f in raw if f["rule"] == "DESCRIPTION_TOO_LONG"], [])


class TestReferenceReadBeforeAction(unittest.TestCase):
    """Explicit read-before-action ordering introduces a sequential resource hop."""

    def test_explicit_orderings_are_detected(self):
        body = textwrap.dedent("""\
            For preparation, read
            [references/device-preparation.md](references/device-preparation.md), then call
            `search_supports`, including on the first turn.

            For public discount-program terms, load `references/public_program_grounding.md` before `search_supports`.

            Before calling `get_plan_perk_info`, read references/plan_rules.md.

            **Subscription portals**: read
            [references/subscription_portal_nav.md](references/subscription_portal_nav.md)
            and follow it exactly.
            """)
        self.assertEqual(lint_cost.reference_hops(body), [
            ("references/device-preparation.md", "search_supports"),
            ("references/public_program_grounding.md", "search_supports"),
            ("references/plan_rules.md", "get_plan_perk_info"),
            ("references/subscription_portal_nav.md", None),
        ])

    def test_on_demand_mentions_and_examples_are_not_hops(self):
        body = textwrap.dedent("""\
            If the customer asks about eligibility, see references/eligibility.md.
            Detailed carrier terms live in [references/carriers.md](references/carriers.md).

            ```markdown
            Read references/example.md, then call `search_supports`.
            ```
            """)
        self.assertEqual(lint_cost.reference_hops(body), [])

    def test_cost_rule_reports_one_info_per_skill(self):
        body = "Read references/a.md and follow it. Load references/b.md before `lookup_order`."
        rules = [r for r in lint_cost.check_skill_cost("orders", body, [], {}, 0)
                 if r[1] == "REFERENCE_READ_BEFORE_ACTION"]
        self.assertEqual(len(rules), 1)
        severity, _rule, message = rules[0]
        self.assertEqual(severity, "INFO")
        self.assertIn("references/a.md (procedure delegated); references/b.md before `lookup_order`",
                      message)


def dist(site, name, version, direct_url=None):
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))


LOCK = """version = 1
requires-python = ">=3.13"

[[package]]
name = "host-app"
version = "0.1.0"
source = { virtual = "." }

[[package]]
name = "alpha"
version = "1.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "beta-lib"
version = "2.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "adk-lightning"
version = "0.1.0"
source = { path = "packages/adkl/new/adk_lightning-0.1.0-py3-none-any.whl" }
"""


class TestEnvironmentLock(unittest.TestCase):
    """An export environment can contain packages not present in uv.lock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "uv.lock").write_text(LOCK)
        (self.root / "execution.yaml").write_text(yaml.safe_dump({
            "version": 1, "default": "local", "profiles": {"local": {
                "target": "local", "environment": "test", "python": ".venv/bin/python"}}}))
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").write_text("")
        (self.root / ".venv/pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.13.7\n")
        self.site = self.root / ".venv/lib/python3.13/site-packages"
        self.site.mkdir(parents=True)
        # An older interpreter directory must not be chosen over pyvenv.cfg.
        (self.root / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
        dist(self.site, "host-app", "0.1.0")
        dist(self.site, "alpha", "1.0")

    def wheel(self, directory):
        url = (self.root / f"packages/adkl/{directory}/adk_lightning-0.1.0-py3-none-any.whl").as_uri()
        return {"url": url, "archive_info": {}}

    def test_synced_environment_passes(self):
        dist(self.site, "beta_lib", "2.0")
        dist(self.site, "adk-lightning", "0.1.0", self.wheel("new"))
        raw, note = doctor_environment.collect(self.root)
        self.assertEqual(raw, [])
        self.assertIn("Compared 4 installed distributions in the local interpreter", note)

    def test_extra_changed_and_stale_wheel_are_one_warning(self):
        dist(self.site, "beta-lib", "2.1")
        dist(self.site, "temporalio", "1.28.0")
        dist(self.site, "adk-lightning", "0.1.0", self.wheel("old"))
        raw, _note = doctor_environment.collect(self.root)
        self.assertEqual(len(raw), 1)
        finding = raw[0]
        self.assertEqual((finding["rule"], finding["severity"], finding["source"]),
                         ("ENVIRONMENT_NOT_LOCKED", "WARN", "uv.lock"))
        message = finding["message"]
        self.assertIn("1 installed distributions are not in uv.lock (temporalio 1.28.0)", message)
        self.assertIn("beta-lib 2.1 (locked 2.0)", message)
        self.assertIn("adk-lightning from packages/adkl/old/adk_lightning-0.1.0-py3-none-any.whl "
                      "(locked packages/adkl/new/adk_lightning-0.1.0-py3-none-any.whl)", message)
        # The project's own (virtual) package is never compared.
        self.assertNotIn("host-app", message)

    def test_identical_wheel_from_another_checkout_is_the_same_pin(self):
        locked = self.root / "packages/adkl/new/adk_lightning-0.1.0-py3-none-any.whl"
        other = self.root.parent / f"{self.root.name}-other" / "adk_lightning-0.1.0-py3-none-any.whl"
        for path in (locked, other):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"same wheel")
        self.addCleanup(lambda: (other.unlink(), other.parent.rmdir()))
        dist(self.site, "beta-lib", "2.0")
        dist(self.site, "adk-lightning", "0.1.0", {"url": other.as_uri(), "archive_info": {}})
        self.assertEqual(doctor_environment.collect(self.root)[0], [])
        other.write_bytes(b"different wheel")
        self.assertIn("adk-lightning from adk_lightning-0.1.0-py3-none-any.whl (outside the project)",
                      doctor_environment.collect(self.root)[0][0]["message"])

    def test_unreadable_environment_is_not_a_finding(self):
        (self.root / ".venv/pyvenv.cfg").unlink()
        raw, note = doctor_environment.collect(self.root)
        self.assertEqual(raw, [])
        self.assertIn("not compared", note)

    def test_doctor_reports_it_in_the_environment_check(self):
        (self.root / "agent.yaml").write_text(yaml.safe_dump(
            {"version": 1, "agent": {"name": "host", "factory": "agent:build"}}))
        (self.root / "pyproject.toml").write_text("[project]\nname = 'host-app'\n")
        wheel = self.root / "packages/adkl/new/adk_lightning-0.1.0-py3-none-any.whl"
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"")
        dist(self.site, "temporalio", "1.28.0")
        with patch.object(execution, "Worker", side_effect=AssertionError("no host import")):
            before = doctor.diagnose(self.root)
            finding = next(f for f in before["findings"] if f["rule"] == "ENVIRONMENT_NOT_LOCKED")
            self.assertEqual((finding["check"], finding["location"]["path"]), ("environment", "uv.lock"))
            self.assertTrue(before["passed"], "advisory: a WARN does not fail the report")
            dist(self.site, "boto3", "1.43.14")
            # Same subject while drift remains; resolved once the venv is synced.
            after = doctor.diagnose(self.root, previous=before)
            self.assertEqual(after["comparison"]["remaining"], [finding["id"]])
            for name in ("temporalio-1.28.0", "boto3-1.43.14"):
                for path in sorted((self.site / f"{name}.dist-info").iterdir()):
                    path.unlink()
                (self.site / f"{name}.dist-info").rmdir()
            synced = doctor.diagnose(self.root, previous=after)
            self.assertIn(finding["id"], synced["comparison"]["resolved"])


class TestStartupDescriptor(unittest.TestCase):
    """Inspection must be able to follow the serving import order."""

    def validate(self, agent):
        return agent_schema.validate({"version": 1, "agent": agent}, Path("."))[0]

    def test_startup_modules_are_validated(self):
        base = {"name": "host", "factory": "host_agent:build"}
        self.assertEqual(self.validate({**base, "startup": ["api"]}), [])
        self.assertEqual(agent_schema.startup_modules({"agent": base}), [])
        for bad in ([], ["api; rm"], ["api", "api"], "api", [f"m{i}" for i in range(17)]):
            self.assertEqual(self.validate({**base, "startup": bad}),
                             ["agent.startup must list 1-16 unique dotted module names"], bad)

    def test_native_check_requests_report_mode_only_when_declared(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        calls = []
        failed = {"agent": None, "startup": {"modules": ["api"], "imported": [],
                                             "failed": {"module": "api", "exception": "ImportError"}},
                  "constructionLog": {"dropped": 0, "entries": [
                      {"logger": "host", "level": "ERROR", "message": "capability_startup_failed",
                       "exception": None, "phase": "startup"}]}}
        worker = type("W", (), {"request": lambda self, op, **payload: calls.append(payload) or failed,
                                "close": lambda self: None})()
        for startup in (None, ["api"]):
            agent = {"name": "host", "factory": "host_agent:build"}
            if startup:
                agent["startup"] = startup
            (root / "agent.yaml").write_text(yaml.safe_dump({"version": 1, "agent": agent}))
            with patch.object(execution, "Worker", return_value=worker):
                report = doctor.diagnose(root, inspect=True)
            if not startup:
                # The old wire request, so existing worker contracts are untouched.
                self.assertEqual(calls.pop(), {})
                continue
            self.assertEqual(calls.pop(), {"startupFailure": "report"})
            native = next(c for c in report["checks"] if c["id"] == "native")
            self.assertEqual(native["status"], "findings")
            self.assertIn("Startup module api failed to import", native["detail"])
            self.assertIsNone(native["scopeDigest"], "no inventory: cannot resolve native findings")
            rules = {f["rule"]: f for f in report["findings"]}
            failure = rules["STARTUP_IMPORT_FAILED"]
            self.assertEqual((failure["severity"], failure["location"]["path"]), ("ERROR", "agent.yaml"))
            self.assertIn("api raised ImportError", failure["message"])
            self.assertIn("while declared startup modules were imported",
                          rules["FACTORY_CONSTRUCTION_ERROR"]["message"])
            self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
