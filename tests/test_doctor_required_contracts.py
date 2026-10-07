"""Required evidence is an opt-in gate, not a new host execution mode."""
import json
import sys
from unittest.mock import patch

import pytest
import yaml

from adkl import doctor, verification

NAMES = ['result_integrity', 'capability_reachability', 'delivery_consistency']


@pytest.fixture
def host(tmp_path):
    (tmp_path / '.venv').symlink_to(sys.prefix, target_is_directory=True)
    files = {
        'agent.yaml': 'version: 1\nagent:\n  name: probe\n  factory: evaluator:build\n',
        'execution.yaml': yaml.safe_dump({'version': 1, 'default': 'local', 'profiles': {
            name: {'target': 'local', 'environment': 'test', 'python': '.venv/bin/python'}
            for name in ['local', 'other']}}),
        'pyproject.toml': '# fixture', 'uv.lock': 'fixture', 'subject.txt': 'fixed',
        'evaluator.py': '''from pathlib import Path
from examples.incident_contracts import checks
def evaluate(*, case, repetition):
    result = checks(faulty=Path('subject.txt').read_text() == 'broken')
    return {'passed': result[case['probe']], 'latency_ms': 0, 'cost_usd': 0}
def build():
    raise AssertionError('must not construct')
''',
        'verification.yaml': yaml.safe_dump({'version': 1, 'evaluator': 'evaluator:evaluate',
            'evaluator_files': ['evaluator.py', 'examples'], 'subject_files': ['subject.txt'],
            'suite': 'cases.json'}),
        'cases.json': json.dumps({'repetitions': 2, 'cases': [
            {'id': name, 'probe': probe, 'adkl_contracts': [name]}
            for name, probe in zip(NAMES, ['metadata', 'followup', 'failed_sink'])]}),
    }
    # Track the actual toy consumer/observer code as evaluator inputs, not just a tag.
    from pathlib import Path
    (tmp_path / 'examples').mkdir()
    (tmp_path / 'examples/__init__.py').write_text('')
    source = Path(__file__).parents[1] / 'examples/incident_contracts.py'
    (tmp_path / 'examples/incident_contracts.py').write_bytes(source.read_bytes())
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def scan(host, **kwargs):
    with patch('adkl.execution.Worker', side_effect=AssertionError('no execution')):
        return doctor.diagnose(host, **kwargs)


def run_candidate(host):
    assert verification.run(host, 'baseline')['state'] == 'baseline_recorded'
    assert verification.run(host, 'candidate')['state'] == 'passed'


def test_gate_lifecycle_and_resolution(host):
    report = scan(host, require_contracts=NAMES)
    assert not report['passed']
    assert {f['subject']['name'] for f in report['findings']
            if f['rule'] == 'CONTRACT_EVIDENCE_REQUIRED'} == set(NAMES)
    verification.run(host, 'baseline')
    assert not scan(host, require_contracts=NAMES)['passed']
    verification.run(host, 'candidate')
    passed = scan(host, require_contracts=NAMES)
    assert passed['passed']
    assert passed['behavioralEvaluation'] == 'not_run'
    assert '--require-contract result_integrity' in doctor.render(passed)
    (host / 'subject.txt').write_text('broken')
    assert not scan(host, require_contracts=NAMES)['passed']
    assert verification.run(host, 'candidate')['state'] == 'regressed'
    broken = scan(host, require_contracts=NAMES)
    failures = {f['id'] for f in broken['findings'] if f['rule'] == 'CONTRACT_PROBE_FAILED'}
    assert len(failures) == 3
    (host / 'subject.txt').write_text('fixed')
    stale = scan(host, require_contracts=NAMES, previous=broken)
    assert failures <= set(stale['comparison']['notVerified'])
    assert verification.run(host, 'candidate')['state'] == 'passed'
    fixed = scan(host, require_contracts=NAMES, previous=broken)
    assert failures <= set(fixed['comparison']['resolved'])
    assert fixed['passed']


def test_absent_configuration_defaults_and_explicit_gate(host, capsys):
    (host / 'verification.yaml').unlink()
    assert scan(host)['passed']  # backwards-compatible advisory default
    report = scan(host, require_contracts=NAMES)
    assert not report['passed']
    assert next(c for c in report['checks'] if c['id'] == 'verification_evidence')['status'] == 'findings'
    assert doctor.main([str(host), '--require-contract', NAMES[0], '--format', 'json']) == 1
    assert json.loads(capsys.readouterr().out)['context']['requiredContracts'] == [NAMES[0]]


@pytest.mark.parametrize('damage', ['missing_cases', 'profile', 'running', 'failed', 'malformed'])
def test_missing_stale_or_failed_evidence_never_passes(host, damage):
    run_candidate(host)
    kwargs = {}
    if damage == 'missing_cases':
        suite = json.loads((host / 'cases.json').read_text())
        suite['cases'][0].pop('adkl_contracts')
        (host / 'cases.json').write_text(json.dumps(suite))
        run_candidate(host)
    elif damage == 'profile':
        kwargs['profile'] = 'other'
    elif damage == 'running':
        (host / '.adkl/verification/running').mkdir()
    elif damage == 'failed':
        with patch('adkl.execution.Worker.request', side_effect=RuntimeError('private-input')):
            verification.run(host, 'candidate')
    else:
        (host / '.adkl/verification/state.json').write_text('private-input')
    report = scan(host, require_contracts=NAMES, **kwargs)
    assert not report['passed']
    assert 'private-input' not in json.dumps(report)


def test_narrower_gate_does_not_resolve_findings(host):
    before = scan(host, require_contracts=NAMES)
    after = scan(host, require_contracts=[NAMES[0]], previous=before)
    missing = {f['id'] for f in before['findings'] if f['rule'] == 'CONTRACT_EVIDENCE_REQUIRED'}
    assert len(missing & set(after['comparison']['notVerified'])) == 2


@pytest.mark.parametrize('value', ['result_integrity', ['unknown'], [1], None])
def test_invalid_api_requirements_fail_before_inspection(host, value):
    with pytest.raises(ValueError, match='require_contracts'):
        scan(host, require_contracts=value)


def test_cli_pass_and_incomplete_exit_codes(host, capsys):
    run_candidate(host)
    options = [str(host), '--require-contract', NAMES[0]]
    assert doctor.main(options) == 0
    (host / '.adkl/verification/state.json').write_text('{')
    assert doctor.main(options) == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        doctor.main([str(host), '--require-contract', 'unknown'])
    assert error.value.code == 2
