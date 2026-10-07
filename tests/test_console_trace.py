"""Bounded, opt-in observations require no ADK dependency."""
import asyncio

from adkl.console_trace import _current, capture


def test_bounded_collector_counts_omitted_requests_and_closes():
    with capture() as collector:
        collector.limit = 2
        for _ in range(5):
            collector.record({"type": "model_request"})
        snapshot = collector.snapshot()
        assert snapshot["preparedModelRequests"] == 5
        assert len(snapshot["events"]) == 2
        assert snapshot["droppedEvents"] == 3
    collector.record({"type": "model_request"})
    assert collector.prepared_requests == 5
    assert _current.get() is None


def test_late_child_task_cannot_write_to_completed_turn():
    async def run():
        gate = asyncio.Event()

        async def child():
            await gate.wait()
            _current.get().record({"type": "model_request"})

        with capture() as collector:
            task = asyncio.create_task(child())
        gate.set()
        await task
        assert collector.events == []
    asyncio.run(run())
