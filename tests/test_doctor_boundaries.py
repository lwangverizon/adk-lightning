"""Boundary evidence stays explicit, fail-closed, profile-bound and read-only."""
import copy
import json
from unittest.mock import patch

import pytest
import yaml

from adkl import contract_probes as probes
from adkl import doctor, doctor_tools, request_costs, verification
from test_doctor_evidence import candidate, host, scan  # noqa: F401


def capture():
    return {"request": 1, "phase": "cold", "boundary": "adk_request",
            "tokenizer_sha256": "a" * 64,
            "bytes": {"system": 100, "tools": 200, "history": 30, "other": 10, "total": 340},
            "proxy_tokens": {"system": 25, "tools": 50, "history": 8, "other": 3, "total": 84}}


def configure_costs(host):
    config = yaml.safe_load((host / 'verification.yaml').read_text())
    config['request_costs'] = True
    config['contracts'] = ['consent_binding', 'outcome_consistency', 'handoff_continuity']
    (host / 'verification.yaml').write_text(yaml.safe_dump(config))
    suite = {'repetitions': 2, 'cases': [{'id': name, 'adkl_contracts': [name]}
                                      for name in config['contracts']]}
    (host / 'cases.json').write_text(json.dumps(suite))
    (host / 'host_eval.py').write_text('''from pathlib import Path
def evaluate(**kwargs):
    return {"passed": Path("subject.txt").read_text() == "good",
            "latency_ms": 1, "cost_usd": 0, "request_costs": CAPTURES}
def build():
    raise AssertionError("Doctor must not construct this factory")
CAPTURES = ''' + repr([capture()]) + '\n')


def test_costs_flow_through_real_worker_and_doctor(host):
    configure_costs(host)
    assert scan(host)['verificationEvidence']['requestCosts']['state'] == 'not_run'
    verification.run(host, 'baseline')
    assert scan(host)['verificationEvidence']['requestCosts']['state'] == 'baseline_recorded'
    result = verification.run(host, 'candidate')
    assert result['state'] == 'passed'
    assert len(result['request_costs']) == 6
    report = scan(host)
    summary = report['verificationEvidence']['requestCosts']
    assert summary['requests'] == 6
    assert summary['groups'][0]['maxProxyTokens']['total'] == 84
    assert summary['groups'][0]['maxBytes']['tools'] == 200
    assert summary['state'] == 'passed'
    assert {c['state'] for c in report['verificationEvidence']['contracts']} == {'passed'}
    brief = doctor.render(report)
    assert 'proxy tokens are not provider billing' in brief
    assert 'max bytes=340' in brief
    assert 'tools: max bytes=200' in brief
    assert report['behavioralEvaluation'] == 'not_run'
    (host / 'subject.txt').write_text('bad')
    stale = scan(host)['verificationEvidence']['requestCosts']
    assert stale == {'state': 'stale'}
    verification.run(host, 'candidate')
    assert scan(host)['verificationEvidence']['requestCosts']['state'] == 'regressed'
    assert scan(host, profile='other')['verificationEvidence']['requestCosts'] == {'state': 'profile_mismatch'}


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'wrong_case', 'raw_text', 'bad_number'])
def test_cost_evidence_corruption_is_incomplete_not_clean(host, damage):
    configure_costs(host)
    candidate(host)
    folder = host / '.adkl/verification'
    state = json.loads((folder / 'state.json').read_text())
    path = folder / (state['latest'] + '.json')
    record = json.loads(path.read_text())
    rows = record['request_costs']
    if damage == 'missing':
        rows.pop()
    elif damage == 'duplicate':
        rows[1] = rows[0]
    elif damage == 'wrong_case':
        rows[0]['case_id'] = 'not-executed'
    elif damage == 'raw_text':
        rows[0]['requests'][0]['prompt'] = 'PRIVATE-PAYLOAD'
    else:
        rows[0]['requests'][0]['bytes']['total'] = -1
    path.write_text(json.dumps(record))
    report = scan(host)
    assert report['verificationEvidence']['state'] == 'unavailable'
    assert 'PRIVATE-PAYLOAD' not in json.dumps(report)
    assert doctor.main([str(host), '--format', 'json']) == 2


def test_missing_host_measurements_fail_run(host):
    config = yaml.safe_load((host / 'verification.yaml').read_text())
    config['request_costs'] = True
    (host / 'verification.yaml').write_text(yaml.safe_dump(config))
    assert verification.run(host, 'baseline')['state'] == 'failed'
    assert scan(host)['verificationEvidence']['requestCosts']['state'] == 'failed'


@pytest.mark.parametrize('change', [
    lambda r: r.update(request=2),
    lambda r: r.update(request=True),
    lambda r: r.update(phase='unknown'),
    lambda r: r.update(boundary='source_file'),
    lambda r: r.update(tokenizer_sha256='provider'),
    lambda r: r['bytes'].update(total=0),
    lambda r: r['proxy_tokens'].update(tools=True),
    lambda r: r['proxy_tokens'].update(total=float('inf')),
    lambda r: r.update(prompt='PRIVATE'),
])
def test_cost_validation(change):
    row = capture()
    change(row)
    with pytest.raises(ValueError):
        request_costs.validate([row])


def test_measurement_groups_do_not_mix_tokenizers_or_phases():
    rows = [capture(), capture(), capture()]
    rows[1].update(request=2, phase='followup')
    rows[2].update(request=3, tokenizer_sha256='b' * 64)
    request_costs.validate(rows)
    summary = request_costs.summarize([{'requests': rows}])
    assert len(summary['groups']) == 3
    # Proxy counts intentionally need not sum, unlike bytes.
    assert sum(rows[0]['proxy_tokens'][k] for k in request_costs.PARTITIONS) != 84


def test_new_probes_reject_wrong_missing_and_unsafe_observations():
    observers = ['workflow', 'callback', 'analytics', 'completion']
    observed = dict.fromkeys(observers, 'error')
    assert probes.outcome_consistency(observed, expected='error', observers=observers)
    assert not probes.outcome_consistency(observed, expected='success', observers=observers)
    observed['analytics'] = 'success'
    assert not probes.outcome_consistency(observed, expected='error', observers=observers)
    assert not probes.outcome_consistency({}, expected='empty', observers=observers)
    assert not probes.outcome_consistency({}, expected='empty', observers=[])
    checks = dict.fromkeys(['affirmative', 'account_matches', 'target_matches', 'fresh',
                           'next_user_turn', 'not_consumed'], True)
    assert probes.consent_binding(checks=checks, effects=['write'], expected_effects=['write'])
    for key in checks:
        invalid = {**checks, key: False}
        assert probes.consent_binding(checks=invalid, effects=[], expected_effects=[])
        assert not probes.consent_binding(checks=invalid, effects=['write'], expected_effects=[])
    assert not probes.consent_binding(checks=checks, effects=[], expected_effects=[])
    assert not probes.consent_binding(checks=checks, effects=['write', 'write'], expected_effects=['write'])
    assert not probes.consent_binding(checks={**checks, 'fresh': 1}, effects=['write'], expected_effects=['write'])
    assert not probes.consent_binding(checks={}, effects=[], expected_effects=[])
    handoff = {'session': 's', 'invocation': 'i', 'query_digest': 'synthetic',
               'tools': ['search'], 'handoff_used': True}
    assert probes.handoff_continuity(observed=handoff, expected=handoff)
    for key, value in [('session', 'wrong'), ('query_digest', 'yes-only'), ('tools', []),
                       ('handoff_used', False)]:
        assert not probes.handoff_continuity(observed={**handoff, key: value}, expected=handoff)
    assert not probes.handoff_continuity(observed={}, expected={})


def tool_config(host):
    config = {'version': 1, 'tools': [{'name': 'offers', 'version': 'v4',
        'fields': {'/options/*/tier': 'string', '/options/*/delta': 'number'},
        'fixtures': ['offers.json'], 'consumers': [
            {'name': 'formatter', 'source': 'subject.txt', 'version': 'v4',
             'fields': {'/options/*/tier': 'string'}}]}]}
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    (host / 'offers.json').write_text(json.dumps({'options': [{'tier': 'less', 'delta': -5}]}))
    return config


def test_output_consumer_drift_and_fixture_repair(host):
    config = tool_config(host)
    assert scan(host)['toolOutputContracts']['state'] == 'passed'
    consumer = config['tools'][0]['consumers'][0]
    consumer.update(version='v3', fields={'/tier': 'string'})
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    report = scan(host)
    rules = {f['rule'] for f in report['findings']}
    assert {'TOOL_OUTPUT_VERSION_MISMATCH', 'TOOL_OUTPUT_CONSUMER_MISMATCH'} <= rules
    assert 'offers version v4: formatter' in doctor.render(report)
    consumer.update(version='v4', fields={'/options/*/tier': 'string'})
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    (host / 'offers.json').write_text(json.dumps({'options': [{'tier': False, 'delta': -5}]}))
    before = scan(host)
    assert any(f['rule'] == 'TOOL_OUTPUT_FIXTURE_MISMATCH' for f in before['findings'])
    (host / 'offers.json').write_text(json.dumps({'options': [{'tier': 'less', 'delta': -5}]}))
    after = scan(host, previous=before)
    ids = {f['id'] for f in before['findings'] if f['check'] == 'tool_outputs'}
    assert ids <= set(after['comparison']['resolved'])
    assert after['toolOutputContracts']['state'] == 'passed'


def test_removed_consumer_cannot_resolve_earlier_mismatch(host):
    config = tool_config(host)
    consumer = config['tools'][0]['consumers'][0]
    other = {**copy.deepcopy(consumer), 'name': 'skill', 'version': 'v3'}
    config['tools'][0]['consumers'].append(other)
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    before = scan(host)
    config['tools'][0]['consumers'].pop()
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    after = scan(host, previous=before)
    ids = {f['id'] for f in before['findings'] if f['check'] == 'tool_outputs'}
    assert ids and ids <= set(after['comparison']['notVerified'])
    (host / 'doctor-tools.json').unlink()
    assert next(c for c in scan(host)['checks'] if c['id'] == 'tool_outputs')['status'] == 'not_checked'


@pytest.mark.parametrize('fixture', [{'options': []}, {'options': [{'tier': 'ok', 'delta': True}]},
                                     {'options': [{'tier': 'ok', 'delta': 1}, {'delta': 2}]}])
def test_output_fixtures_cannot_pass_vacuously(host, fixture):
    tool_config(host)
    (host / 'offers.json').write_text(json.dumps(fixture))
    assert doctor_tools.collect(host)[1]


@pytest.mark.parametrize('source', ['.env', '../secret.json', '/tmp/secret.json', 'secret.json'])
def test_output_inputs_reject_credentials_and_escape(host, source):
    config = tool_config(host)
    settings = yaml.safe_load((host / 'execution.yaml').read_text())
    settings['profiles']['local']['env_file'] = 'secret.json'
    (host / 'execution.yaml').write_text(yaml.safe_dump(settings))
    (host / 'secret.json').write_text('PRIVATE-PAYLOAD')
    config['tools'][0]['fixtures'] = [source]
    (host / 'doctor-tools.json').write_text(json.dumps(config))
    report = scan(host)
    assert next(c for c in report['checks'] if c['id'] == 'tool_outputs')['status'] == 'error'
    assert 'PRIVATE-PAYLOAD' not in json.dumps(report)


def test_output_malformed_and_symlink_inputs_fail_closed(host):
    tool_config(host)
    path = host / 'offers.json'
    path.unlink()
    path.symlink_to(host / 'missing.json')
    assert not scan(host)['passed']
    path.unlink()
    path.write_text('{"private":"PRIVATE-PAYLOAD", "private":0}')
    report = scan(host)
    assert 'PRIVATE-PAYLOAD' not in json.dumps(report)
    assert not report['passed']


def test_doctor_never_executes_for_new_checks(host):
    tool_config(host)
    with patch('adkl.execution.Worker', side_effect=AssertionError('no execution')):
        report = doctor.diagnose(host)
    assert report['toolOutputContracts']['state'] == 'passed'
    assert report['behavioralEvaluation'] == 'not_run'
