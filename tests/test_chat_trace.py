"""Missing ranking evidence is unknown, not zero or a failed answer."""
import json

import pytest

from adkl.chat import TurnTrace


@pytest.mark.parametrize('diagnostics, expected', [
    ({}, None), ({'margin': None}, None), ({'margin': 0}, 0),
    ({'margin': 0.123456}, 0.1235),
])
def test_nullable_margin_serializes(diagnostics, expected):
    trace = TurnTrace(user_text='upgrade fee')
    trace.record_runtime(diagnostics)
    assert json.loads(json.dumps(trace.to_json()))['margin'] == expected
