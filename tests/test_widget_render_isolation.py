"""Opt-in per-spec fault containment retains truthful partial-delivery receipts."""
import asyncio
from functools import wraps
from types import SimpleNamespace

import pytest

from adkl.widgets import PENDING_KEY, WidgetSpec, emit_staged, stage


def run_async(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def setup():
    async def build(ctx, items, rid):
        return [({'type': 'fixture'}, f'caption {i}') for i in items]

    specs = [WidgetSpec(key=k, widget_id=k, payload_type='fixture', build=build)
             for k in ('first', 'second')]
    ctx = SimpleNamespace(state={}, invocation_id='turn-1')
    for spec in specs:
        stage(ctx.state, spec, [0, 1] if spec.key == 'first' else [2])
    return ctx, specs


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('partial', [False, True])
@run_async
async def test_isolated_failure_preserves_other_specs_and_no_same_turn_retry(asynchronous, partial):
    ctx, specs = setup()
    calls = []

    def accept(ctx, wid, payload):
        calls.append(wid)
        if wid == ('first-1' if partial else 'first-0'):
            raise ValueError('private data never enters diagnostics')

    async def accept_async(*args):
        accept(*args)

    result = await emit_staged(ctx, specs, sink=accept_async if asynchronous else accept,
                               isolate_render_failures=True)
    assert result.gates[0].gate == 'render_failed'
    assert result.gates[0].detail == 'ValueError'
    assert result.gates[1].passed
    assert not ctx.state.get(specs[0].state_emitted)
    assert ctx.state[specs[1].state_emitted]
    assert not ctx.state[PENDING_KEY]
    assert [r['widgetId'] for r in result.emitted] == (
        ['first-0', 'second'] if partial else ['second'])
    assert result.companion == ('caption 0' if partial else 'caption 2')
    before = list(calls)
    await emit_staged(ctx, specs, sink=accept, isolate_render_failures=True)
    assert calls == before
    # A fresh invocation with freshly staged data is not permanently blocked.
    ctx.invocation_id = 'turn-2'
    stage(ctx.state, specs[0], [0])
    recovered = await emit_staged(ctx, specs, sink=lambda *args: None,
                                  isolate_render_failures=True)
    assert recovered.gates[0].passed


@run_async
async def test_default_still_propagates_and_retains_pending():
    ctx, specs = setup()

    def fail(*args):
        raise OSError('fixture')

    with pytest.raises(OSError):
        await emit_staged(ctx, specs, sink=fail)
    assert ctx.state[PENDING_KEY]
    assert not ctx.state.get(specs[1].state_emitted)


@run_async
async def test_cancellation_is_never_converted_to_decline():
    ctx, specs = setup()

    async def cancel(*args):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await emit_staged(ctx, specs, sink=cancel, isolate_render_failures=True)
    assert ctx.state[PENDING_KEY]
    assert not ctx.state.get(specs[1].state_emitted)


@run_async
async def test_binding_honors_opt_in_and_records_decline():
    pytest.importorskip('google.adk')
    from adkl.bindings import RuntimeBindings

    ctx, specs = setup()
    ctx.agent_name = 'fixture'
    ctx.session = SimpleNamespace(events=[])
    captions = []

    def sink(ctx, wid, payload):
        if wid.startswith('first'):
            raise ValueError('private')

    binding = RuntimeBindings(
        skills=[], widget_specs=specs, widget_sink=sink,
        companion_sink=lambda ctx, text: captions.append(text),
        feature_config={'pipeline': ['staged_widgets'],
                        'staged_widgets': {'isolate_render_failures': True}})
    await binding._make_emitter()(ctx)
    assert ctx.state['adkl:widgets:fixture']['gates'][0]['gate'] == 'render_failed'
    assert captions == ['caption 2']
