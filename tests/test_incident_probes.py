"""Incident-shaped faulty hosts, corrected implementations and negative controls."""
import pytest

from adkl.contract_probes import capability_reachability, delivery_consistency, result_integrity
from examples.incident_contracts import checks, consume_result, deliver, resolve_followup


def test_broken_and_fixed_consumers():
    assert not any(checks(faulty=True).values())
    assert all(checks().values())


@pytest.mark.parametrize('payload,status,cached,fallback', [
    ({'items': []}, 'empty', True, False),
    ({'items': ['one']}, 'present', True, False),
    ({'error': True}, 'error', False, True),
    (None, 'unknown', False, True),
])
def test_legitimate_result_controls(payload, status, cached, fallback):
    assert result_integrity(observed=consume_result(payload), expected=dict(
        status=status, cached=cached, fallback=fallback))


@pytest.mark.parametrize('row', [{}, {'status': 'empty'},
    {'status': 'empty', 'cached': 1, 'fallback': False},
    {'status': 'invented', 'cached': True, 'fallback': False}, None])
def test_result_requires_typed_complete_evidence(row):
    expected = {'status': 'empty', 'cached': True, 'fallback': False}
    assert not result_integrity(observed=row, expected=expected)
    assert not result_integrity(observed=expected, expected=row)


@pytest.mark.parametrize('field', ['catalog', 'indexed', 'exposed', 'dispatchable'])
def test_each_reachability_boundary_is_required(field):
    row = resolve_followup()
    row[field] = ['other']
    assert not capability_reachability(**row)


def test_alias_duplicate_and_missing_observations():
    row = resolve_followup()
    assert capability_reachability(**row)  # dispatch-only handler is allowed
    assert not capability_reachability(**{**row, 'references': {'owner': 'removed'}})
    assert not capability_reachability(**{**row, 'required': []})
    assert not capability_reachability(**{**row, 'indexed': ['answer', 'answer']})
    assert not capability_reachability(**{**row, 'indexed': 'answer'})
    assert not capability_reachability(**{**row, 'references': {'owner': None}})


@pytest.mark.parametrize('sink_ok', [True, False])
def test_delivery_success_and_failure_controls(sink_ok):
    assert delivery_consistency(**deliver(sink_ok=sink_ok))


def test_delivery_rejects_duplicates_unstaged_missing_and_false_agreement():
    row = deliver(sink_ok=True)
    assert not delivery_consistency(**{**row, 'accepted': ['connect', 'connect']})
    assert not delivery_consistency(**{**row, 'staged': ['other']})
    assert not delivery_consistency(**{**row, 'claimed': []})
    assert not delivery_consistency(**{**row, 'accepted': [], 'claimed': []})
    assert not delivery_consistency(staged=[], accepted=[], claimed=[], expected=[])
    assert not delivery_consistency(**{**row, 'staged': 'connect'})
