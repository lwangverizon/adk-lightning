"""Retained evidence is identity-bound, complete, sanitized and never executed."""
import json
import sys
from unittest.mock import patch

import pytest
import yaml

from adkl import doctor, verification
from adkl.contract_probes import no_replay, single_emitter, state_isolation, rejected_call_recovery


@pytest.fixture
def host(tmp_path):
    (tmp_path / '.venv').symlink_to(sys.prefix, target_is_directory=True)
    files = {
        'agent.yaml': 'version: 1\nagent:\n  name: probe\n  factory: host_eval:build\n',
        'execution.yaml': yaml.safe_dump({'version': 1, 'default': 'local', 'profiles': {
            'local': {'target': 'local', 'environment': 'test'},
            'other': {'target': 'local', 'environment': 'other'}}}),
        'pyproject.toml': '# fixture', 'uv.lock': 'fixture', 'subject.txt': 'good',
        'host_eval.py': '''from pathlib import Path
def evaluate(**kwargs):
    return {"passed": Path("subject.txt").read_text() == "good", "latency_ms": 1, "cost_usd": 0}
def build():
    raise AssertionError("Doctor must not construct this factory")
''',
        'verification.yaml': yaml.safe_dump({'version': 1, 'evaluator': 'host_eval:evaluate',
            'evaluator_files': ['host_eval.py'], 'subject_files': ['subject.txt'],
            'suite': 'cases.json', 'contracts': ['no_replay', 'single_emitter']}),
        'cases.json': json.dumps({'repetitions': 2, 'cases': [
            {'id': 'retry', 'adkl_contracts': ['no_replay']},
            {'id': 'emit', 'adkl_contracts': ['single_emitter']}]}),
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def scan(host, **kwargs):
    with patch('adkl.execution.Worker', side_effect=AssertionError('must not execute')):
        return doctor.diagnose(host, **kwargs)


def candidate(host):
    assert verification.run(host, 'baseline')['state'] == 'baseline_recorded'
    assert verification.run(host, 'candidate')['state'] == 'passed'


def test_evidence_lifecycle_and_non_vacuous_contracts(host):
    before = scan(host)
    assert before['verificationEvidence']['state'] == 'not_run'
    assert {c['state'] for c in before['verificationEvidence']['contracts']} == {'not_run'}
    verification.run(host, 'baseline')
    baseline = scan(host)
    assert baseline['verificationEvidence']['state'] == 'baseline_recorded'
    assert {c['state'] for c in baseline['verificationEvidence']['contracts']} == {'baseline_only'}
    verification.run(host, 'candidate')
    passed = scan(host)
    assert passed['verificationEvidence']['state'] == 'passed'
    assert passed['behavioralEvaluation'] == 'not_run'
    assert {c['observations'] for c in passed['verificationEvidence']['contracts']} == {2}
    assert 'no_replay: passed' in doctor.render(passed)
    (host / 'subject.txt').write_text('bad')
    assert scan(host)['verificationEvidence']['state'] == 'stale'
    verification.run(host, 'candidate')
    failed = scan(host)
    assert failed['verificationEvidence']['state'] == 'regressed'
    issues = [f for f in failed['findings'] if f['rule'] == 'CONTRACT_PROBE_FAILED']
    assert len(issues) == 2 and len({f['id'] for f in issues}) == 2
    assert {f['subject']['kind'] for f in issues} == {'contract'}
    assert not failed['passed']
    (host / 'subject.txt').write_text('good')
    stale = scan(host, previous=failed)
    assert all(f['id'] in stale['comparison']['notVerified'] for f in issues)
    verification.run(host, 'candidate')
    fixed = scan(host, previous=failed)
    assert all(f['id'] in fixed['comparison']['resolved'] for f in issues)


@pytest.mark.parametrize('changed', ['uv.lock', 'host_eval.py', 'cases.json', 'execution.yaml'])
def test_changes_invalidate_evidence(host, changed):
    candidate(host)
    path = host / changed
    path.write_text(path.read_text() + ('\n' if changed == 'cases.json' else '\n# changed'))
    if changed == 'cases.json':
        suite = json.loads(path.read_text())
        suite['cases'][0]['new_expected'] = True
        path.write_text(json.dumps(suite))
    assert scan(host)['verificationEvidence']['state'] == 'stale'


def test_profile_running_missing_cases_and_failed_attempt(host):
    candidate(host)
    assert scan(host, profile='other')['verificationEvidence']['state'] == 'profile_mismatch'
    folder = host / '.adkl/verification'
    (folder / 'running').mkdir()
    assert scan(host)['verificationEvidence']['state'] == 'running'
    (folder / 'running').rmdir()
    with patch('adkl.execution.Worker.request', side_effect=RuntimeError('private-payload')):
        verification.run(host, 'candidate')
    report = scan(host)
    assert report['verificationEvidence']['state'] == 'failed'
    assert 'private-payload' not in json.dumps(report)
    suite = json.loads((host / 'cases.json').read_text())
    suite['cases'][1].pop('adkl_contracts')
    (host / 'cases.json').write_text(json.dumps(suite))
    candidate(host)
    report = scan(host)
    assert report['verificationEvidence']['contracts'][1]['state'] == 'missing_cases'


@pytest.mark.parametrize('damage', ['missing_row', 'false_pass', 'missing_baseline', 'symlink', 'invalid_json'])
def test_invalid_evidence_never_passes_or_exports_payload(host, damage):
    candidate(host)
    folder = host / '.adkl/verification'
    state = json.loads((folder / 'state.json').read_text())
    path = folder / (state['latest'] + '.json')
    record = json.loads(path.read_text())
    if damage == 'missing_row':
        record['report']['results'].pop()
    elif damage == 'false_pass':
        record['report']['results'][0]['passed'] = False
    elif damage == 'missing_baseline':
        record['baseline'] = '0' * 32
    elif damage == 'symlink':
        path.unlink()
        path.symlink_to(host / 'subject.txt')
    elif damage == 'invalid_json':
        path.write_text('{"private-payload"')
    if damage not in {'symlink', 'invalid_json'}:
        path.write_text(json.dumps(record))
    report = scan(host)
    assert next(c for c in report['checks'] if c['id'] == 'verification_evidence')['status'] == 'error'
    assert not report['passed']
    assert 'private-payload' not in json.dumps(report)


def test_removed_config_does_not_resolve_failed_evidence(host):
    candidate(host)
    (host / 'subject.txt').write_text('bad')
    verification.run(host, 'candidate')
    before = scan(host)
    (host / 'verification.yaml').unlink()
    after = scan(host, previous=before)
    ids = {f['id'] for f in before['findings'] if f['check'] == 'verification_evidence'}
    assert ids <= set(after['comparison']['notVerified'])


def test_receipt_race_is_incomplete(host):
    candidate(host)
    from adkl import doctor_evidence
    original = doctor_evidence._receipt
    calls = 0
    def receipt(root):
        nonlocal calls
        calls += 1
        value = original(root)
        if calls == 2:
            value['running'] = True
        return value
    with patch.object(doctor_evidence, '_receipt', receipt):
        report = scan(host)
    assert next(c for c in report['checks'] if c['id'] == 'verification_evidence')['status'] == 'error'


def test_contract_tags_validate_and_credential_inputs_are_not_opened(host):
    config = yaml.safe_load((host / 'verification.yaml').read_text())
    config['contracts'] = ['unknown']
    (host / 'verification.yaml').write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match='contracts'):
        verification.snapshot(host)
    config['contracts'] = []
    config['subject_files'] = ['credentials.json']
    (host / 'verification.yaml').write_text(yaml.safe_dump(config))
    execution = yaml.safe_load((host / 'execution.yaml').read_text())
    execution['profiles']['local']['env_file'] = 'credentials.json'
    (host / 'execution.yaml').write_text(yaml.safe_dump(execution))
    (host / 'credentials.json').write_text('private-payload')
    with pytest.raises(ValueError, match='Credential'):
        verification.snapshot(host)
    assert 'private-payload' not in json.dumps(scan(host))


def test_broken_evidence_and_config_symlinks_fail_closed(host):
    candidate(host)
    state = host / '.adkl/verification/state.json'
    state.unlink()
    state.symlink_to(host / 'missing.json')
    report = scan(host)
    assert report['verificationEvidence']['state'] == 'unavailable'
    config = host / 'verification.yaml'
    config.unlink()
    config.symlink_to(host / 'missing.yaml')
    report = scan(host)
    assert not report['passed']
    assert any(c['status'] == 'error' for c in report['checks'])


def test_probe_assertions_reject_missing_duplicate_and_corrupt_observations():
    assert no_replay(['effect'], expected=['effect'])
    assert not no_replay([], expected=[])
    assert not no_replay(['effect', 'effect'], expected=['effect'])
    assert single_emitter([('sdk', 'a')], owner='sdk', expected=['a'])
    assert not single_emitter([('sdk', 'a'), ('native', 'a')], owner='sdk', expected=['a'])
    assert not single_emitter([], owner='sdk', expected=[])
    assert state_isolation({'persisted': None, 'returned': None}, expected={'persisted': None, 'returned': None})
    assert not state_isolation({'persisted': 'old'}, expected={'persisted': None})
    assert not state_isolation({}, expected={})
    assert rejected_call_recovery([False, True])
    assert not rejected_call_recovery([False, False])
    assert not rejected_call_recovery([0, 1])
