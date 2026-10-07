"""Scope reduction and diagnostic evolution must not masquerade as repairs."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from adkl import doctor, doctor_scope, execution


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "host"
    root.mkdir()
    path = root / "skills/teach/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text('---\nname: teach\ndescription: Teach supplied facts\n---\n' + 'Explain. ' * 1800)
    doc = {"agent": {"name": "tutor", "factory": "agent:build"},
           "authoring": {"skills": {"teach": "skills/teach/SKILL.md"}}}
    (root / "agent.yaml").write_text(yaml.safe_dump(doc))
    (root / "agent.py").write_text('raise AssertionError("must not import")')
    return root, path, doc


def cost_id(report):
    return next(f["id"] for f in report["findings"] if f["rule"] == "SKILL_BODY_LARGE")


def shorten(path):
    path.write_text('---\nname: teach\ndescription: Teach supplied facts\n---\nExplain.')


def test_same_inventory_allows_real_body_repair(project):
    root, path, _ = project
    before = doctor.diagnose(root)
    shorten(path)
    after = doctor.diagnose(root, previous=before)
    assert cost_id(before) in after["comparison"]["resolved"]
    assert not after["comparisonNotes"]


@pytest.mark.parametrize("change", ["mapping", "factory", "profile", "file", "skill_name"])
def test_changed_scan_scope_does_not_resolve(project, change):
    root, path, doc = project
    before = doctor.diagnose(root)
    shorten(path)
    if change == "mapping":
        doc["authoring"]["skills"] = {}
    elif change == "factory":
        doc["agent"]["factory"] = "other:build"
    elif change == "skill_name":
        path.write_text(path.read_text().replace("name: teach", "name: renamed"))
    elif change == "profile":
        (root / "execution.yaml").write_text('version: 1\nprofiles: {}\n')
    else:
        (root / "removed_consumer.py").write_text("# changed inventory")
    (root / "agent.yaml").write_text(yaml.safe_dump(doc))
    after = doctor.diagnose(root, previous=before)
    assert cost_id(before) in after["comparison"]["notVerified"]
    assert not after["comparison"]["resolved"]


@pytest.mark.parametrize("change", ["legacy", "scanner", "context", "stale", "baseline_stale", "failed"])
def test_incompatible_evidence_is_not_a_fix(project, change):
    root, path, _ = project
    before = doctor.diagnose(root)
    shorten(path)
    after = doctor.diagnose(root)
    if change == "legacy":
        before.pop("comparisonIdentity")
    elif change == "scanner":
        before["comparisonIdentity"]["scannerDigest"] = "different"
    elif change == "context":
        before["context"]["overlays"] = "uat"
    elif change == "baseline_stale":
        before["freshness"] = "stale"
    elif change == "stale":
        after["freshness"] = "stale"
    else:
        next(c for c in after["checks"] if c["id"] == "mapped_content")["status"] = "error"
    result = doctor.compare(before, after)
    assert cost_id(before) in result["notVerified"]
    assert not result["resolved"]


def test_legacy_report_brief_explains_non_resolution(project):
    root, path, _ = project
    before = doctor.diagnose(root)
    before.pop("comparisonIdentity")
    shorten(path)
    after = doctor.diagnose(root, previous=before)
    assert "lacks supported comparison coverage" in doctor.render(after)
    assert set(after["comparison"]) == {"new", "remaining", "resolved", "notVerified"}


def test_native_inventory_reduction_with_identical_descriptor(project):
    root, _, _ = project
    def report(include_other):
        binding = {"skills": [], "tools": [], "audit": {"findings": []}}
        children = [{"name": "other", "adkl": [deepcopy(binding)]}] if include_other else []
        data = {"agent": {"name": "tutor", "adkl": [binding], "children": children}}
        if include_other:
            binding["audit"]["findings"] = [{"rule": "PROMPT_POLICY_REPEATED",
                                                "message": "Repeated policy", "severity": "INFO"}]
        return data
    worker = Mock()
    with patch.object(execution, "Worker", return_value=worker):
        worker.request.return_value = report(True)
        before = doctor.diagnose(root, inspect=True)
        worker.request.return_value = report(False)
        after = doctor.diagnose(root, inspect=True, previous=before)
    prior = next(f for f in before["findings"] if f["rule"] == "PROMPT_POLICY_REPEATED")
    assert prior["id"] in after["comparison"]["notVerified"]
    assert "Coverage changed" in doctor.render(after)


def test_subject_ids_survive_wording_but_multi_issue_rules_do_not_collapse(project):
    root, _, doc = project
    raw = {"rule": "PROMPT_SKILL_POLICY_DUPLICATED", "skill": "teach", "message": "One sentence"}
    def finding(value):
        return doctor._finding(value, "native", "native_declarations", root, doc)
    assert finding(raw)["id"] == finding({**raw, "message": "Two sentences"})["id"]
    raw["rule"] = "TOOL_UNRESOLVED"
    assert finding(raw)["id"] != finding({**raw, "message": "Different tool"})["id"]


def test_scope_never_exports_profile_values_or_reads_secret_files(project):
    root, _, doc = project
    (root / "execution.yaml").write_text('profiles:\n  local:\n    env_file: .env\n    env:\n      MODEL: private-model-name\n')
    (root / ".env").write_text("PRIVATE=credential-content")
    original = Path.read_bytes
    def read(path):
        assert path.name != ".env"
        return original(path)
    with patch.object(Path, "read_bytes", read):
        scope = doctor_scope.source_scope(root, doc, None)
    assert len(scope) == 64
    assert "private" not in scope
