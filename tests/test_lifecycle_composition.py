"""Callback-chain mechanics remain lightweight and preserve failure semantics."""
import asyncio
from types import SimpleNamespace

import pytest

from adkl.callbacks import _lifecycle


def test_native_callback_normalization_copies_lists_without_calling_hooks():
    def callback():
        pytest.fail("construction must not execute callbacks")
    original = {"before": [callback], "after": callback, "error": None}
    normalized = _lifecycle.normalize_callbacks(original, {"before", "after", "error"})
    assert normalized == {"before": [callback], "after": [callback]}
    normalized["before"].clear()
    assert original["before"] == [callback]
    with pytest.raises(ValueError, match="Unknown callback kinds"):
        _lifecycle.normalize_callbacks({"unknown": callback}, set())
    with pytest.raises(TypeError, match="sequence of callables"):
        _lifecycle.normalize_callbacks({"before": [None]}, {"before"})
    with pytest.raises(TypeError, match="sequence of callables"):
        _lifecycle.require_callbacks(callback, "observer")


def test_output_transforms_keep_empty_replacements_and_none_retains_current():
    seen = []
    async def empty(**kwargs):
        seen.append(kwargs["llm_response"])
        return {}
    def retain(**kwargs):
        seen.append(kwargs["llm_response"])
    callback = _lifecycle.compose_output_transforms([empty, retain])
    assert asyncio.run(callback(None, {"original": True})) == {}
    assert seen == [{"original": True}, {}]
    assert asyncio.run(_lifecycle.compose_output_transforms([retain])(None, {})) is None


def test_observer_replacement_refusal_prevents_tool_transforms():
    async def observer(**kwargs):
        return False
    def transform(**kwargs):
        pytest.fail("invalid observer must abort before transform")
    callback = _lifecycle.compose_tool_callbacks([observer], [transform])
    with pytest.raises(TypeError, match="observer must return None"):
        asyncio.run(callback(None, {}, None, {"raw": 1}))


def test_emission_error_uses_current_reporter_before_completion_short_circuit():
    context = SimpleNamespace(agent_name="host", state={})
    calls, current = [], []
    async def emitter(ctx):
        raise OSError("private detail")
    async def complete(callback_context):
        calls.append("complete")
        return "host answer"
    def unreachable(callback_context):
        pytest.fail("native completion short circuit must remain")
    callback = _lifecycle.compose_completion(emitter, [complete, unreachable], lambda: current[0])
    current.append(lambda notes: calls.extend(notes))  # Resolve at execution, not construction.
    assert asyncio.run(callback(context)) == "host answer"
    assert calls == ["staged widget emission failed: OSError", "complete"]
    assert context.state == {"adkl:widgets:host": {"delivery_error": "OSError"}}


def test_reporter_failure_keeps_original_delivery_error_after_completion():
    calls = []
    failure = OSError("delivery")
    reporter_failure = ValueError("reporter")
    async def emitter(ctx):
        raise failure
    def report(notes):
        raise reporter_failure
    def complete(callback_context):
        calls.append("complete")
        return "must not conceal failure"
    callback = _lifecycle.compose_completion(emitter, [complete], lambda: report)
    with pytest.raises(OSError) as caught:
        asyncio.run(callback(SimpleNamespace(agent_name="host", state={})))
    assert caught.value is failure
    assert caught.value.__cause__ is reporter_failure
    assert calls == ["complete"]


def test_emitter_cancellation_is_not_reclassified_as_ordinary_delivery_error():
    context = SimpleNamespace(agent_name="host", state={})
    async def emitter(ctx):
        raise asyncio.CancelledError()
    def unreachable(*args, **kwargs):
        pytest.fail("cancellation is not an ordinary-error completion boundary")
    callback = _lifecycle.compose_completion(emitter, [unreachable], unreachable)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(callback(context))
    assert context.state == {}
