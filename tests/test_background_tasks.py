"""Hermetic background queue lifecycle, ownership and cancellation contracts."""
import asyncio
import sqlite3

import pytest

from adkl.background_tasks import (
    BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity, TaskWorker,
)


CAP = BackgroundCapability("analysis", "Analyze synthetic data", "v1")
IDENTITY = TaskIdentity("tenant:user", "chat")


def test_persistent_idempotent_submission_and_owner_isolation(tmp_path):
    async def exercise():
        path = tmp_path / "tasks.db"
        backend = SQLiteTaskBackend(path)
        task = await backend.submit(IDENTITY, CAP, {"question": "x"}, "call")
        reopened = SQLiteTaskBackend(path)
        assert (await reopened.submit(IDENTITY, CAP, {"question": "x"}, "call")) == task
        with pytest.raises(ValueError):
            await reopened.submit(IDENTITY, CAP, {"question": "y"}, "call")
        with pytest.raises(PermissionError):
            await reopened.get("other", task.task_id)
        assert await reopened.list(TaskIdentity("other", "chat")) == []
        assert (await reopened.get(IDENTITY.owner, task.task_id)).status == "queued"
    asyncio.run(exercise())


def test_worker_is_independent_and_claim_is_exclusive(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "call")
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(record, abort):
            calls.append(record.task_id)
            started.set()
            await release.wait()
            return {"report": "done"}
        worker = TaskWorker(backend, {("analysis", "v1"): execute}, authorize=lambda task: True)
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 2)
        assert (await backend.get(IDENTITY.owner, task.task_id)).status == "running"
        # Other workers cannot claim it; conversation need not await the job.
        assert await worker.run_once() is False
        release.set()
        await running
        completed = await backend.get(IDENTITY.owner, task.task_id)
        assert completed.status == "succeeded" and completed.result == {"report": "done"}
        assert calls == [task.task_id]
    asyncio.run(exercise())


def test_cancel_queued_and_running_and_suppress_late_result(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        queued = await backend.submit(IDENTITY, CAP, {}, "queued")
        assert (await backend.cancel(IDENTITY.owner, queued.task_id)).status == "cancelled"
        task = await backend.submit(IDENTITY, CAP, {}, "running")
        started = asyncio.Event()
        async def execute(record, abort):
            started.set()
            await abort.wait()
            return "late result"
        worker = TaskWorker(backend, {("analysis", "v1"): execute}, authorize=lambda task: True)
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 2)
        assert (await backend.cancel(IDENTITY.owner, task.task_id)).status == "cancel_requested"
        await asyncio.wait_for(running, 2)
        result = await backend.get(IDENTITY.owner, task.task_id)
        assert result.status == "cancelled" and result.result is None
    asyncio.run(exercise())


def test_expired_worker_is_failed_not_replayed_and_cannot_publish(tmp_path):
    async def exercise():
        path = tmp_path / "tasks.db"
        backend = SQLiteTaskBackend(path)
        submitted = await backend.submit(IDENTITY, CAP, {}, "call")
        task = await backend.claim((("analysis", "v1"),), 30)
        assert task is not None
        with sqlite3.connect(path) as db:
            db.execute("UPDATE adkl_tasks SET expires=0")
        assert await backend.claim((("analysis", "v1"),), 30) is None
        assert await backend.finish(task, "succeeded", result="stale") is False
        result = await backend.get(IDENTITY.owner, submitted.task_id)
        assert result.status == "failed" and result.error == "worker_lost"
    asyncio.run(exercise())


def test_authorization_is_rechecked_and_cancellation_can_remain_allowed(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        allowed = {"submit", "status", "result", "cancel"}
        tasks = BackgroundTasks(backend, identity=lambda ctx: IDENTITY,
            authorize=lambda identity, operation, capability: operation in allowed)
        task = await tasks.submit(None, CAP, {}, key="call")
        allowed.clear()
        assert await tasks.list(None) == []
        with pytest.raises(PermissionError):
            await tasks.get(None, task.task_id, result=True)
        allowed.add("cancel")
        assert (await tasks.cancel(None, task.task_id))["status"] == "cancelled"
    asyncio.run(exercise())


@pytest.mark.parametrize("permitted,error", [(False, "execution_denied"), (True, "execution_failed")])
def test_worker_rechecks_authorization_and_sanitizes_failure(tmp_path, permitted, error):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "call")
        async def execute(record, abort):
            raise RuntimeError("private customer text")
        worker = TaskWorker(backend, {("analysis", "v1"): execute}, authorize=lambda task: permitted)
        assert await worker.run_once()
        result = await backend.get(IDENTITY.owner, task.task_id)
        assert result.status == "failed" and result.error == error
        assert result.result is None
    asyncio.run(exercise())


def test_cancellation_during_authorization_never_starts_target(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "call")
        async def authorize(record):
            await backend.cancel(IDENTITY.owner, record.task_id)
            return True
        async def execute(record, abort):
            pytest.fail("Cancelled task must not start")
        worker = TaskWorker(backend, {("analysis", "v1"): execute}, authorize=authorize)
        assert await worker.run_once()
        assert (await backend.get(IDENTITY.owner, task.task_id)).status == "cancelled"
    asyncio.run(exercise())


def test_task_timeout_cancels_executor_and_records_failure(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        task = await backend.submit(IDENTITY, CAP, {}, "call")
        stopped = asyncio.Event()
        async def execute(record, abort):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        worker = TaskWorker(backend, {("analysis", "v1"): execute},
            authorize=lambda task: True, timeout_seconds=0.01)
        # Exercise execution timeout, not a race with SQLite's thread startup.
        # Authorization/heartbeat timeouts have separate worker-lifecycle tests.
        from unittest.mock import AsyncMock, patch
        with patch.object(backend, "heartbeat", new=AsyncMock(return_value=True)):
            await worker.run_once()
        assert stopped.is_set()
        assert (await backend.get(IDENTITY.owner, task.task_id)).status == "failed"
    asyncio.run(exercise())


def test_version_mismatch_does_not_execute(tmp_path):
    async def exercise():
        backend = SQLiteTaskBackend(tmp_path / "tasks.db")
        await backend.submit(IDENTITY, CAP, {}, "call")
        assert await backend.claim((("analysis", "v2"),), 30) is None
    asyncio.run(exercise())
