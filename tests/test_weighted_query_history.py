"""Reusable vector history: no ADK/model dependency, no mutable session cache."""
import copy
import json
from types import MappingProxyType

import pytest

from adkl.query_history import QueryHistory


def prepare(history, state, turn, vector=(1., 0.), **kwargs):
    return history.prepare(vector, state, scope="agent", invocation_id=str(turn),
                           fingerprint=kwargs.get("fingerprint", "model/query/2"))


def test_readonly_prepare_and_explicit_commit():
    history, state = QueryHistory(), {}
    first = prepare(history, MappingProxyType(state), 1, (10., 0.))
    assert first.vector == (1., 0.)
    assert state == {}
    with pytest.raises(TypeError):
        history.commit(MappingProxyType(state), first)
    assert history.commit(state, first)
    second = prepare(history, state, 2, (0., 20.))
    assert second.vector == pytest.approx((.316227766, .948683298))
    assert second.turns_used == 2
    assert second.records[0][1] == (0., 1.)  # Never store the blended vector.


def test_hard_window_json_reload_dedupe_and_no_nested_mutation():
    history, state = QueryHistory(), {}
    for turn in range(7):
        old = copy.deepcopy(state)
        update = prepare(history, state, turn)
        assert state == old
        assert history.commit(state, update)
        assert not history.commit(state, update)
        again = prepare(history, state, turn)
        assert again.vector == update.vector
        assert again.records == update.records
        state = json.loads(json.dumps(state))
    assert [ident for ident, _ in update.records] == ["6", "5", "4", "3", "2"]
    assert update.turns_used == 5


def test_stale_update_cannot_overwrite_newer_commit():
    history, state = QueryHistory(), {}
    a, b = prepare(history, state, "a"), prepare(history, state, "b")
    assert history.commit(state, a)
    assert not history.commit(state, b)
    c = prepare(history, state, "c")
    history.commit(state, c)
    with pytest.raises(ValueError, match="older invocation"):
        prepare(history, state, "a")


@pytest.mark.parametrize("stored", [None, [], {"version": 1}, {"records": "bad"}])
def test_corrupt_history_resets(stored):
    history = QueryHistory()
    state = {"adkl:weighted_query_history:agent": stored}
    update = prepare(history, state, 1)
    assert update.turns_used == 1
    assert history.commit(state, update)


def test_provenance_dimension_and_policy_changes_reset():
    history, state = QueryHistory(), {}
    history.commit(state, prepare(history, state, 1))
    assert prepare(history, state, 2, fingerprint="different-model/query/2").turns_used == 1
    assert prepare(history, state, 2, vector=(1., 0., 0.)).turns_used == 1
    assert prepare(QueryHistory((.8, .2)), state, 2).turns_used == 1


def test_session_and_agent_isolation():
    history, state = QueryHistory(), {}
    history.commit(state, prepare(history, state, 1))
    assert prepare(history, {}, 2).turns_used == 1
    other = history.prepare((0., 1.), state, scope="other", invocation_id="2",
                            fingerprint="model/query/2")
    assert other.turns_used == 1


@pytest.mark.parametrize("weights", [(), (0.,), (-1.,), (.4, .6), (.5, .5),
                                     (float("nan"),), (float("inf"),)])
def test_invalid_weights(weights):
    with pytest.raises(ValueError):
        QueryHistory(weights)


@pytest.mark.parametrize("vector", [(), (0., 0.), (float("nan"), 1.),
                                    (float("inf"), 1.)])
def test_invalid_current_vector_does_not_write(vector):
    state = {}
    with pytest.raises(ValueError):
        prepare(QueryHistory(), state, 1, vector)
    assert state == {}


def test_current_majority_handles_opposite_topic_without_zero_vector():
    history, state = QueryHistory(), {}
    for turn in range(4):
        history.commit(state, prepare(history, state, turn))
    update = prepare(history, state, 4, (-1., 0.))
    assert update.vector == (-1., 0.)
