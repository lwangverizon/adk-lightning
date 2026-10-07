"""Offline portal boundaries: no serving process imports host code."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml

from adkl import portal


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'agent.yaml').write_text(yaml.safe_dump({
        'agent': {'factory': 'private_host:build'},
        'authoring': {'skills': {'math': 'skills/math/SKILL.md'}}}))
    skill = tmp_path / 'skills/math/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: math\ndescription: Teach math\nmetadata:\n  adk_additional_tools: [lookup]\n---\nSensitive body\n')
    return tmp_path


def export(project, **kwargs):
    return portal.export(project, agent_id='math', name='Math', artifact='sha256:fixture',
                         sdk_revision='fixture-sdk-revision', **kwargs)


def test_static_export_never_constructs_worker_and_omits_bodies(project):
    with patch.object(portal.execution, 'Worker', side_effect=AssertionError('No host imports')):
        result = export(project)
    assert result['inventory']['skills'][0]['instructions'] is None
    assert result['inventory']['skills'][0]['doc'] is None
    assert result['inventory']['tools'] == []
    assert result['report']['project'] == '.'
    assert result['identity']['deployment'] == 'not_verified'
    assert next(c for c in result['report']['checks'] if c['id'] == 'native')['status'] == 'not_checked'
    assert result['capabilities'] == dict(readOnly=True, chat=False, edit=False, liveInspection=False)
    assert export(project, include_instructions=True)['inventory']['skills'][0]['instructions'].strip() == 'Sensitive body'
    assert export(project, include_instructions=True)['inventory']['skills'][0]['doc'] is not None


def test_native_export_reuses_single_doctor_snapshot(project):
    worker = Mock()
    worker.request.return_value = {'agent': {'name': 'math', 'children': [], 'adkl': [{
        'skills': [{'name': 'math', 'description': 'Teach math', 'instructions': 'Sensitive body', 'tools': ['lookup']}],
        'tools': [], 'audit': {'findings': []}}]}}
    with patch.object(portal.execution, 'Worker', return_value=worker) as constructor:
        result = export(project, inspect=True)
    assert constructor.call_count == 1
    worker.request.assert_called_once_with('inspect')
    worker.close.assert_called_once()
    assert result['inventory']['scope'] == 'native_declarations'
    assert any(f['rule'] == 'TOOL_UNRESOLVED' for f in result['report']['findings'])
    assert 'inspectionSnapshot' not in result['report']
    assert 'Sensitive body' not in json.dumps(result)


def test_failed_native_keeps_file_evidence_and_failed_coverage(project):
    with patch.object(portal.execution, 'Worker', side_effect=RuntimeError('secret credential')):
        result = export(project, inspect=True)
    assert result['inventory']['scope'] == 'saved_skill_files_only'
    assert next(c for c in result['report']['checks'] if c['id'] == 'native')['status'] == 'error'
    assert 'secret credential' not in json.dumps(result)


def test_changing_inputs_fail_export(project):
    original = portal.doctor.diagnose
    def change(*args, **kwargs):
        report = original(*args, **kwargs)
        (project / 'new.py').write_text('changed = True')
        return report
    with patch.object(portal.doctor, 'diagnose', side_effect=change), pytest.raises(ValueError, match='changed'):
        export(project)


def test_assemble_reviewed_bundles_no_workers_and_missing_entries(project, tmp_path):
    result = export(project)
    portal.write_bundle(tmp_path / 'math.json', result)
    assert (tmp_path / 'math.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        portal.write_bundle(tmp_path / 'math.json', result)
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'agents': [
        {'id': 'math', 'name': 'Math', 'bundle': 'math.json'},
        {'id': 'prospect', 'name': 'Prospect'}]}))
    out = tmp_path / 'published'
    with patch.object(portal.execution, 'Worker', side_effect=AssertionError('No imports')):
        portal.assemble(registry, out)
    rows = json.loads((out / 'registry.json').read_text())['agents']
    assert rows[1]['snapshot'] is None
    assert json.loads((out / rows[0]['snapshot']).read_text()) == result
    assert set(p.name for p in out.iterdir()) == {'registry.json', rows[0]['snapshot']}
    with pytest.raises(ValueError, match='exists'):
        portal.assemble(registry, out)


@pytest.mark.parametrize('entry', [
    {'id': '../secret', 'name': 'Bad'},
    {'id': 'math', 'name': 'Wrong', 'bundle': 'math.json'},
    {'id': 'math', 'name': 'Math', 'bundle': 'symlink.json'},
])
def test_bad_registry_fails_without_partial_publication(project, tmp_path, entry):
    portal.write_bundle(tmp_path / 'math.json', export(project))
    (tmp_path / 'symlink.json').symlink_to(tmp_path / 'math.json')
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'agents': [entry]}))
    out = tmp_path / 'public'
    with pytest.raises(ValueError):
        portal.assemble(registry, out)
    assert not out.exists()
    assert not list(tmp_path.glob('.portal-*'))


def test_symlinked_skill_cannot_export_external_content(project, tmp_path):
    path = project / 'skills/math/SKILL.md'
    path.unlink()
    path.symlink_to(Path(__file__))
    with pytest.raises((ValueError, portal.execution.ExecutionError)):
        export(project)


def test_approval_and_duplicate_ids_required(tmp_path):
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'agents': [{'id': 'a', 'name': 'A'}] * 2}))
    with pytest.raises(SystemExit) as error:
        portal.main(['assemble', str(registry), '--out', str(tmp_path / 'out')])
    assert error.value.code == 2
    with pytest.raises(ValueError, match='unique'):
        portal.assemble(registry, tmp_path / 'out')
