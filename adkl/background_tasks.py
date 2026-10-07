"""Background task contracts and a persistent, single-host reference queue.

No ADK imports or implicit worker startup. Identity and authorization come from
trusted host callbacks, never from model-supplied arguments.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
import inspect
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Protocol
from uuid import uuid4


TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class TaskIdentity:
    """Opaque host-authenticated owner and originating conversation."""

    owner: str
    session: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.owner, self.session)):
            raise ValueError("Task identity requires owner and session")


@dataclass(frozen=True)
class BackgroundCapability:
    """Versioned worker target; description is the only procedure discovery data."""

    name: str
    description: str
    version: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in
                   (self.name, self.description, self.version)):
            raise ValueError("Background capability fields must be nonempty strings")


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    owner: str
    session: str
    capability: str
    version: str
    payload: dict[str, Any]
    status: str
    created_at: float
    updated_at: float
    result: Any = None
    error: str | None = None
    lease: str | None = None

    def receipt(self) -> dict[str, Any]:
        """Bounded metadata, without execution input or result."""
        return {k: getattr(self, k) for k in (
            "task_id", "capability", "version", "status", "created_at", "updated_at"
        )}


class TaskBackend(Protocol):
    async def submit(self, identity: TaskIdentity, capability: BackgroundCapability,
                     payload: dict, key: str) -> TaskRecord: ...
    async def get(self, owner: str, task_id: str) -> TaskRecord: ...
    async def list(self, identity: TaskIdentity) -> list[TaskRecord]: ...
    async def cancel(self, owner: str, task_id: str) -> TaskRecord: ...
    async def claim(self, targets: tuple[tuple[str, str], ...],
                    lease_seconds: float) -> TaskRecord | None: ...
    async def heartbeat(self, task: TaskRecord, lease_seconds: float) -> bool: ...
    async def finish(self, task: TaskRecord, status: str, *,
                     result: Any = None, error: str | None = None) -> bool: ...


def _json(value):
    # Fail before dispatch/persistence on non-JSON values or nonfinite numbers.
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


class SQLiteTaskBackend:
    """Durable reference queue for processes sharing a local SQLite file.

    Expired leases FAIL rather than replay unknown side effects. A production
    backend may implement an explicitly authorized recovery policy. Not suitable
    for network filesystems or high-throughput distributed deployments.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file-backed SQLite database")
        with closing(self._connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS adkl_tasks (
                task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, session TEXT NOT NULL,
                capability TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                result TEXT, error TEXT, lease TEXT, expires REAL,
                dedupe TEXT NOT NULL, UNIQUE(owner, session, dedupe))""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _record(row):
        data = dict(row)
        for key in ("expires", "dedupe"):
            data.pop(key)
        data["payload"] = json.loads(data["payload"])
        data["result"] = json.loads(data["result"]) if data["result"] else None
        return TaskRecord(**data)

    async def _call(self, operation):
        def transaction():
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                # An expired executor cannot publish a late result. Do not rerun
                # it: its external side effects may already have happened.
                db.execute("""UPDATE adkl_tasks SET status='failed', error='worker_lost',
                    lease=NULL, updated_at=? WHERE status IN ('running','cancel_requested')
                    AND expires < ?""", (time.time(), time.time()))
                return operation(db)
        return await asyncio.to_thread(transaction)

    async def submit(self, identity, capability, payload, key):
        if not isinstance(key, str) or not key:
            raise ValueError("An idempotency key is required")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a JSON object")
        serialized = _json(payload)
        if len(serialized.encode("utf-8")) > 65536:
            raise ValueError("Task input exceeds 64 KiB; pass artifact references")
        def operation(db):
            old = db.execute("SELECT * FROM adkl_tasks WHERE owner=? AND session=? AND dedupe=?",
                             (identity.owner, identity.session, key)).fetchone()
            if old:
                if (old["capability"], old["version"], old["payload"]) != (
                        capability.name, capability.version, serialized):
                    raise ValueError("Idempotency key reused with different input")
                return self._record(old)
            now, task_id = time.time(), str(uuid4())
            db.execute("""INSERT INTO adkl_tasks
                (task_id,owner,session,capability,version,payload,status,created_at,updated_at,dedupe)
                VALUES (?,?,?,?,?,?,'queued',?,?,?)""",
                (task_id, identity.owner, identity.session, capability.name,
                 capability.version, serialized, now, now, key))
            return self._record(db.execute("SELECT * FROM adkl_tasks WHERE task_id=?", (task_id,)).fetchone())
        return await self._call(operation)

    @staticmethod
    def _owned(db, owner, task_id):
        row = db.execute("SELECT * FROM adkl_tasks WHERE task_id=? AND owner=?",
                         (task_id, owner)).fetchone()
        if row is None:
            raise PermissionError("Task unavailable")
        return row

    async def get(self, owner, task_id):
        return await self._call(lambda db: self._record(self._owned(db, owner, task_id)))

    async def list(self, identity):
        return await self._call(lambda db: [self._record(row) for row in db.execute(
            "SELECT * FROM adkl_tasks WHERE owner=? AND session=? ORDER BY created_at DESC LIMIT 100",
            (identity.owner, identity.session))])

    async def cancel(self, owner, task_id):
        def operation(db):
            row = self._owned(db, owner, task_id)
            status = row["status"]
            if status not in TERMINAL:
                status = "cancelled" if status == "queued" else "cancel_requested"
                db.execute("UPDATE adkl_tasks SET status=?,updated_at=? WHERE task_id=?",
                           (status, time.time(), task_id))
            return self._record(self._owned(db, owner, task_id))
        return await self._call(operation)

    async def claim(self, targets, lease_seconds=30):
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive and finite")
        targets = tuple(targets)
        def operation(db):
            if not targets:
                return None
            conditions = " OR ".join("(capability=? AND version=?)" for _ in targets)
            row = db.execute(f"""SELECT * FROM adkl_tasks WHERE status='queued'
                AND ({conditions}) ORDER BY created_at, rowid LIMIT 1""",
                tuple(value for target in targets for value in target)).fetchone()
            if row is None:
                return None
            now = time.time()
            db.execute("""UPDATE adkl_tasks SET status='running',lease=?,expires=?,updated_at=?
                WHERE task_id=?""", (str(uuid4()), now + lease_seconds, now, row["task_id"]))
            return self._record(db.execute("SELECT * FROM adkl_tasks WHERE task_id=?",
                                           (row["task_id"],)).fetchone())
        return await self._call(operation)

    async def heartbeat(self, task, lease_seconds=30):
        # Retain the claim during cooperative cancellation cleanup, but report
        # False so the worker stops execution. Expired/replaced claims stay fenced.
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive and finite")
        def operation(db):
            row = db.execute("""UPDATE adkl_tasks SET expires=?,updated_at=?
                WHERE task_id=? AND lease=? AND status IN ('running','cancel_requested')
                RETURNING status""",
                (time.time() + lease_seconds, time.time(), task.task_id, task.lease)).fetchone()
            return row is not None and row["status"] == "running"
        return await self._call(operation)

    async def finish(self, task, status, *, result=None, error=None):
        if status not in TERMINAL:
            raise ValueError("finish requires a terminal status")
        serialized = _json(result)
        def operation(db):
            return db.execute("""UPDATE adkl_tasks SET
                status=CASE WHEN status='cancel_requested' THEN 'cancelled' ELSE ? END,
                result=CASE WHEN status='cancel_requested' THEN NULL ELSE ? END,
                error=?,lease=NULL,expires=NULL,updated_at=?
                WHERE task_id=? AND lease=? AND status IN ('running','cancel_requested')""",
                (status, serialized, error, time.time(), task.task_id, task.lease)).rowcount == 1
        return await self._call(operation)


async def resolve(value):
    return await value if inspect.isawaitable(value) else value


class BackgroundTasks:
    """Authorized task API shared by model tools and host endpoints.

    authorize(identity, operation, capability_name) must return exactly True.
    Ownership is also enforced by the backend. Each operation is checked anew.
    """

    def __init__(self, backend: TaskBackend, *, identity: Callable, authorize: Callable):
        self.backend, self.identity, self.authorize = backend, identity, authorize

    async def _identity(self, context):
        identity = await resolve(self.identity(context))
        if not isinstance(identity, TaskIdentity):
            raise TypeError("identity must return TaskIdentity")
        return identity

    async def _allow(self, identity, operation, capability):
        if await resolve(self.authorize(identity, operation, capability)) is not True:
            raise PermissionError("Task operation denied")

    async def submit(self, context, capability, payload, *, key):
        identity = await self._identity(context)
        await self._allow(identity, "submit", capability.name)
        return await self.backend.submit(identity, capability, payload, key)

    async def get(self, context, task_id, *, result=False):
        identity = await self._identity(context)
        task = await self.backend.get(identity.owner, task_id)
        await self._allow(identity, "result" if result else "status", task.capability)
        return {**task.receipt(), **({"result": task.result, "error": task.error} if result else {})}

    async def list(self, context):
        identity = await self._identity(context)
        records = await self.backend.list(identity)
        visible = []
        for task in records:
            if await resolve(self.authorize(identity, "status", task.capability)) is True:
                visible.append(task.receipt())
        return visible

    async def cancel(self, context, task_id):
        identity = await self._identity(context)
        task = await self.backend.get(identity.owner, task_id)
        await self._allow(identity, "cancel", task.capability)
        return (await self.backend.cancel(identity.owner, task_id)).receipt()


class TaskWorker:
    """Explicit worker loop; executors accept (TaskRecord, asyncio.Event).

    authorize(task) rechecks execution permission. Executors must cooperate with
    cancellation; Python tasks are cancelled if the signal is not acknowledged.
    """

    def __init__(self, backend: TaskBackend, executors: dict, *, authorize: Callable,
                 poll_seconds=0.1, lease_seconds=30, cancel_grace_seconds=2,
                 timeout_seconds=3600):
        if (not all(math.isfinite(v) for v in (poll_seconds, lease_seconds,
                                               cancel_grace_seconds, timeout_seconds))
                or not 0 < poll_seconds < lease_seconds / 2
                or cancel_grace_seconds < 0 or timeout_seconds <= 0):
            raise ValueError("Invalid worker timing")
        self.backend, self.executors, self.authorize = backend, dict(executors), authorize
        self.poll_seconds, self.lease_seconds = poll_seconds, lease_seconds
        self.cancel_grace_seconds = cancel_grace_seconds
        self.timeout_seconds = timeout_seconds

    async def run_once(self) -> bool:
        task = await self.backend.claim(tuple(self.executors), self.lease_seconds)
        if task is None:
            return False
        execution = None
        abort = asyncio.Event()
        try:
            async def bounded_execution():
                # Authorization is part of the bounded, heartbeat-supervised job,
                # not an unbounded await before monitoring starts.
                async with asyncio.timeout(self.timeout_seconds):
                    if await resolve(self.authorize(task)) is not True:
                        return "failed", None, "execution_denied"
                    # Recheck after external policy before starting business work.
                    if abort.is_set() or not await self.backend.heartbeat(task, self.lease_seconds):
                        return "cancelled", None, None
                    result = await self.executors[(task.capability, task.version)](task, abort)
                    return "succeeded", result, None
            execution = asyncio.create_task(bounded_execution())
            while not execution.done():
                await asyncio.wait({execution}, timeout=self.poll_seconds)
                if not await self.backend.heartbeat(task, self.lease_seconds):
                    abort.set()
                    deadline = asyncio.get_running_loop().time() + self.cancel_grace_seconds
                    while not execution.done():
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            execution.cancel()
                            break
                        await asyncio.wait({execution}, timeout=min(self.poll_seconds, remaining))
                        # A cancelled task keeps its lease until cleanup finishes.
                        # The return stays False; this never authorizes more work.
                        await self.backend.heartbeat(task, self.lease_seconds)
                    await asyncio.gather(execution, return_exceptions=True)
                    await self.backend.finish(task, "cancelled")
                    return True
            status, result, error = execution.result()
            await self.backend.finish(task, status, result=result, error=error)
        except asyncio.CancelledError:
            abort.set()
            if execution:
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
            await self.backend.finish(task, "failed", error="worker_stopped")
            raise
        except Exception:
            # Do not persist raw exception strings (credentials/customer data).
            await self.backend.finish(task, "failed", error="execution_failed")
        finally:
            if execution and not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(stop.wait(), self.poll_seconds)
                except TimeoutError:
                    pass
