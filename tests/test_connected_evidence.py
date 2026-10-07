"""Retained receipts are optional publisher claims, never automatic evaluations."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import patch

import pytest

from adkl.connected_evidence import EvidenceStore
from tests.test_doctor_evidence import host, candidate, scan  # noqa: F401
from tests.test_connected import declaration, gateway  # noqa: F401


def receipt(data, digest):
    now = datetime.now(timezone.utc)
    return {'schemaVersion': 1, 'kind': 'adkl_retained_verification_receipt',
            'binding': {**{key: data[key] for key in (
                'agentId', 'environment', 'release', 'instanceId', 'generation')},
                'snapshotDigest': digest},
            'publishedAt': (now - timedelta(seconds=1)).isoformat(),
            'expiresAt': (now + timedelta(minutes=5)).isoformat(),
            'doctor': {'sourceDigest': 'source-fixture', 'verificationEvidence': {
                'state': 'passed', 'evidence': 'retained_host_report', 'runId': 'run-fixture',
                'subjectDigest': 'subject', 'suiteDigest': 'suite',
                'evaluatorDigest': 'evaluator', 'coverageDigest': 'coverage',
                'contracts': [{'name': 'no_replay', 'state': 'passed', 'required': True,
                               'cases': ['private-case-id'], 'observations': 1}]}}}


def test_actual_doctor_summary_can_be_published(host, tmp_path):
    candidate(host)  # Explicit scripted evaluator, never run by the gateway.
    report = scan(host)
    data = declaration()
    value = receipt(data, 'digest')
    value['doctor'] = {'sourceDigest': report['sourceDigest'],
                       'verificationEvidence': report['verificationEvidence']}
    folder = tmp_path / 'published'
    folder.mkdir()
    (folder / 'prospect.json').write_text(json.dumps(value))
    result = EvidenceStore(folder).summary(data, 'digest')
    assert result['state'] == 'matched'
    assert result['verificationState'] == 'passed'
    assert {row['name'] for row in result['contracts']} == {'no_replay', 'single_emitter'}


def test_request_cost_addition_is_validated_but_not_published(tmp_path):
    from adkl.request_costs import summarize
    from tests.test_doctor_boundaries import capture
    data = declaration()
    value = receipt(data, 'digest')
    costs = summarize([{'requests': [capture()]}])
    costs['state'] = 'passed'
    value['doctor']['verificationEvidence']['requestCosts'] = costs
    path = tmp_path / 'prospect.json'
    path.write_text(json.dumps(value))
    result = EvidenceStore(tmp_path).summary(data, 'digest')
    assert result['state'] == 'matched'
    assert 'requestCosts' not in result
    costs['groups'][0]['prompt'] = 'PRIVATE-PAYLOAD'
    path.write_text(json.dumps(value))
    result = EvidenceStore(tmp_path).summary(data, 'digest')
    assert result['state'] == 'invalid'
    assert 'PRIVATE-PAYLOAD' not in json.dumps(result)


def test_published_receipt_matches_without_running_host(tmp_path):
    data = declaration()
    body = json.dumps(data).encode()
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / 'prospect.json').write_text(json.dumps(receipt(data, digest)))
    with patch('adkl.doctor.diagnose', side_effect=AssertionError('Do not run Doctor')), \
            patch('adkl.verification.run', side_effect=AssertionError('Do not evaluate')):
        result = EvidenceStore(tmp_path).summary(data, digest)
    assert result['state'] == 'matched'
    assert result['verificationState'] == 'passed'
    assert result['provenance'] == 'publisher_asserted'
    assert result['executedHere'] is False
    assert 'private-case-id' not in json.dumps(result)


@pytest.mark.parametrize('key', ['agentId', 'environment', 'release', 'instanceId', 'generation', 'snapshotDigest'])
def test_each_binding_axis_required(tmp_path, key):
    data = declaration()
    value = receipt(data, 'digest')
    value['binding'][key] = 'different'
    (tmp_path / 'prospect.json').write_text(json.dumps(value))
    assert EvidenceStore(tmp_path).summary(data, 'digest')['state'] == 'identity_mismatch'


@pytest.mark.parametrize('change', ['expired', 'vacuous', 'private', 'missing_identity', 'invalid_time'])
def test_invalid_or_expired_evidence_never_passes(tmp_path, change):
    data = declaration()
    value = receipt(data, 'digest')
    evidence = value['doctor']['verificationEvidence']
    if change == 'expired':
        value['publishedAt'] = '2000-01-01T00:00:00Z'
        value['expiresAt'] = '2001-01-01T00:00:00Z'
    elif change == 'vacuous':
        evidence['contracts'][0]['cases'] = []
    elif change == 'private':
        evidence['trace'] = 'private-data'
    elif change == 'missing_identity':
        del evidence['suiteDigest']
    else:
        value['expiresAt'] = 'invalid'
    (tmp_path / 'prospect.json').write_text(json.dumps(value))
    result = EvidenceStore(tmp_path).summary(data, 'digest')
    assert result['state'] == ('expired' if change == 'expired' else 'invalid')
    assert 'verificationState' not in result


def test_missing_symlink_and_directory_fail_closed(tmp_path):
    data = declaration()
    store = EvidenceStore(tmp_path)
    path = tmp_path / 'prospect.json'
    assert store.summary(data, 'digest')['state'] == 'not_published'
    path.symlink_to(tmp_path / 'missing')
    assert store.summary(data, 'digest')['state'] == 'invalid'
    path.unlink()
    path.mkdir()
    assert store.summary(data, 'digest')['state'] == 'invalid'


def test_evidence_updated_and_removed_even_on_authorized_cache_hit(gateway, tmp_path):
    service, headers, calls = gateway
    service.evidence = EvidenceStore(tmp_path)
    route = '/api/v1/agents/prospect/inspection'
    first = service.handle('GET', route, headers)[1]
    assert first['retainedEvidence']['state'] == 'not_published'
    path = tmp_path / 'prospect.json'
    path.write_text(json.dumps(receipt(first['snapshot'], first['snapshotDigest'])))
    assert service.handle('GET', route, headers)[1]['retainedEvidence']['state'] == 'matched'
    assert service.handle('GET', route, {**headers, 'x-adkl-subject': 'bob'})[0] == 404
    path.unlink()
    assert service.handle('GET', route, headers)[1]['retainedEvidence']['state'] == 'not_published'
    assert calls == ['prospect']
