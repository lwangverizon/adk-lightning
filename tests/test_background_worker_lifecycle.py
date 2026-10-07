"""Worker supervision, cancellation leases and fair queue claims."""
import asyncio

import pytest

from adkl.background_tasks import BackgroundCapability, SQLiteTaskBackend, TaskIdentity, TaskWorker


CAP = BackgroundCapability("research", "Research", "v1")
IDENTITY = TaskIdentity("owner", "chat")


def test_authorization_is_bounded_and_worker_can_take_next_task(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        first = await backend.submit(IDENTITY, CAP, {}, "first")
        second = await backend.submit(IDENTITY, CAP, {}, "second")
        async def authorize(task):
            if task.task_id == first.task_id:
                await asyncio.Event().wait()
            return True
        calls = []
        async def execute(task, abort):
            calls.append(task.task_id)
            return "done"
        worker = TaskWorker(backend, {("research", "v1"): execute}, authorize=authorize,
                            poll_seconds=.01, timeout_seconds=.1)
        await asyncio.wait_for(worker.run_once(), 3)
        record = await backend.get(IDENTITY.owner, first.task_id)
        assert (record.status, record.error) == ("failed", "execution_failed")
        await worker.run_once()
        assert calls == [second.task_id]
        assert (await backend.get(IDENTITY.owner, second.task_id)).status == "succeeded"
    asyncio.run(exercise())


def test_cancellation_interrupts_pending_authorization(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "first")
        started, stopped = asyncio.Event(), asyncio.Event()
        async def authorize(task):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def execute(task, abort):
            pytest.fail("Authorization never granted")
        worker = TaskWorker(backend, {("research", "v1"): execute}, authorize=authorize,
                            poll_seconds=.01, cancel_grace_seconds=.02)
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 2)
        await backend.cancel(IDENTITY.owner, task.task_id)
        await asyncio.wait_for(running, 2)
        assert stopped.is_set()
        assert (await backend.get(IDENTITY.owner, task.task_id)).status == "cancelled"
    asyncio.run(exercise())


def test_authorization_and_cancellation_cleanup_keep_lease(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "first")
        authorizing, allow, executing, cleanup, release = [asyncio.Event() for _ in range(5)]
        async def authorize(task):
            authorizing.set()
            await allow.wait()
            return True
        async def execute(task, abort):
            executing.set()
            await abort.wait()
            cleanup.set()
            await release.wait()
            return "late"
        worker = TaskWorker(backend, {("research", "v1"): execute}, authorize=authorize,
                            poll_seconds=.01, lease_seconds=.2, cancel_grace_seconds=3)
        running = asyncio.create_task(worker.run_once())
        try:
            await asyncio.wait_for(authorizing.wait(), 2)
            # Wait longer than one lease, while the worker renews it.
            await asyncio.sleep(.35)
            assert (await backend.get(IDENTITY.owner, task.task_id)).status == "running"
            allow.set()
            await asyncio.wait_for(executing.wait(), 2)
            await backend.cancel(IDENTITY.owner, task.task_id)
            await asyncio.wait_for(cleanup.wait(), 2)
            await asyncio.sleep(.35)
            assert (await backend.get(IDENTITY.owner, task.task_id)).status == "cancel_requested"
            release.set()
            await asyncio.wait_for(running, 2)
            record = await backend.get(IDENTITY.owner, task.task_id)
            assert record.status == "cancelled" and record.result is None
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    asyncio.run(exercise())


def test_claim_is_oldest_across_registered_targets(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        other = BackgroundCapability("other", "Other", "v1")
        oldest = await backend.submit(IDENTITY, other, {}, "old")
        newer = await backend.submit(IDENTITY, CAP, {}, "new")
        targets = (("research", "v1"), ("other", "v1"))
        assert (await backend.claim(targets)).task_id == oldest.task_id
        assert (await backend.claim(targets)).task_id == newer.task_id
        assert await backend.claim(()) is None
    asyncio.run(exercise())


def test_worker_shutdown_cleans_up_and_fences_result(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "first")
        started, stopped = asyncio.Event(), asyncio.Event()
        claims = []
        async def execute(record, abort):
            claims.append(record)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                assert abort.is_set()
                stopped.set()
        worker = TaskWorker(backend, {("research", "v1"): execute}, authorize=lambda t: True)
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert stopped.is_set()
        record = await backend.get(IDENTITY.owner, task.task_id)
        assert (record.status, record.error) == ("failed", "worker_stopped")
        assert not await backend.finish(claims[0], "succeeded", result="late")
    asyncio.run(exercise())
