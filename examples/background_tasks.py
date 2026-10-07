"""Credential-free durable launch/worker/result example.

Run: uv run --no-sync python -m examples.background_tasks
The worker is explicitly driven here; host it independently in an application.
"""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from adkl.background_tasks import (
    BackgroundCapability, BackgroundTasks, SQLiteTaskBackend, TaskIdentity, TaskWorker,
)


async def main():
    with TemporaryDirectory() as directory:
        backend = SQLiteTaskBackend(Path(directory) / "tasks.sqlite")
        # Fixture authorization only; production must use authenticated host policy.
        tasks = BackgroundTasks(backend,
            identity=lambda context: TaskIdentity("fixture-owner", "fixture-chat"),
            authorize=lambda identity, operation, capability: True)
        capability = BackgroundCapability("research", "Research a synthetic topic", "v1")
        receipt = await tasks.submit(None, capability, {"topic": "background work"}, key="request-1")
        assert receipt.status == "queued"
        print("Accepted:", receipt.receipt())

        async def execute(task, abort):
            return {"summary": "Fixture report for " + task.payload["topic"]}

        worker = TaskWorker(backend, {("research", "v1"): execute},
                            authorize=lambda task: True)
        await worker.run_once()
        result = await tasks.get(None, receipt.task_id, result=True)
        assert result["status"] == "succeeded"
        print("Completed:", result)


if __name__ == "__main__":
    asyncio.run(main())
