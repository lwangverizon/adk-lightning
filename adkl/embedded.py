"""Explicit process-local inspection lifecycle; no Runner or host discovery.

Collection happens only in prepare/register, never on a GET or inference turn.
Hosts serialize their own agent mutations and publish after controlled changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from threading import Lock
import uuid
import weakref

from adkl._declarations import MAX_BYTES, collect
from adkl.connected_schema import declarations, document

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class InspectionUnavailable(Exception):
    """No current approved registration; safe to expose without host details."""


@dataclass(frozen=True)
class Registration:
    """Generation handle. Old lifecycle callbacks cannot retire a replacement."""

    agent_id: str
    generation: str


@dataclass
class _Entry:
    registration: Registration
    name: str
    root: weakref.ReferenceType | None = None
    body: bytes | None = None
    digest: str | None = None
    active: bool = False


class InspectionRegistry:
    """Host-approved declarations for at most 32 explicit roots per process.

    Enabling publication requires content_approved=True, independently of HTTP
    authorization. No default environment, release identity or public endpoint.
    No strong agent/Runner/session references are retained after collection.
    """

    def __init__(self, *, environment: str, release: str, content_approved: bool = False):
        for value in (environment, release):
            if type(value) is not str or not value.strip() or len(value) > 256:
                raise ValueError("Explicit bounded environment and release required")
        if type(content_approved) is not bool:
            raise TypeError("content_approved must be bool")
        self.environment = environment
        self.release = release
        self._approved = content_approved
        self.instance_id = uuid.uuid4().hex
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    def prepare(self, agent_id: str, root, *, name: str | None = None) -> Registration:
        """Revoke old content first, then capture the exact supplied native root/App.

        Call outside inference and while the host prevents concurrent mutation.
        Failure leaves this registration unavailable, never the old snapshot.
        Concurrent retirement/replacement prevents a late collection publishing.
        """
        if type(agent_id) is not str or not _SLUG.fullmatch(agent_id):
            raise ValueError("Invalid agent id")
        name = agent_id if name is None else name
        if type(name) is not str or not name.strip() or len(name) > 256:
            raise ValueError("Invalid display name")
        entry = _Entry(Registration(agent_id, uuid.uuid4().hex), name)
        with self._lock:
            if not self._approved:
                raise InspectionUnavailable("Content publication is disabled")
            if agent_id not in self._entries and len(self._entries) >= 32:
                raise ValueError("Registration limit exceeded")
            self._entries[agent_id] = entry
        try:
            reference = weakref.ref(root)
            root_id, nodes = collect(root)
            payload = {
                "schemaVersion": 1, "kind": "adkl_loaded_declarations", "agentId": agent_id,
                "environment": self.environment, "release": self.release,
                "releaseProvenance": "host_asserted", "instanceId": self.instance_id,
                "generation": entry.registration.generation,
                "capturedAt": datetime.now(timezone.utc).isoformat(),
                "scope": "host-registered root; declarations at explicit preparation",
                "root": root_id, "agents": nodes,
                "capabilities": {"chat": False, "edit": False, "sessions": False},
                "checks": {"doctor": "not_run", "behavioral": "not_run"},
                "limitations": [
                    "One process and explicit root, not fleet or session evidence.",
                    "Host asserts serving ownership; SDK does not discover or replace a Runner.",
                    "In-place mutations require host invalidation and re-registration.",
                    "Dynamic prompts/toolsets/callbacks are not evaluated; tool visibility is not authorization.",
                    "Resources, source spans, workflow routes and private configuration omitted.",
                    "Content is host-approved, not automatically secret-redacted.",
                ],
            }
            body = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
            if len(body) > MAX_BYTES:
                raise ValueError("Snapshot exceeds bounds")
            declarations(document(body))
            digest = hashlib.sha256(body).hexdigest()
            with self._lock:
                if self._entries.get(agent_id) is not entry:
                    raise InspectionUnavailable("Registration was replaced or retired")
                entry.root, entry.body, entry.digest = reference, body, digest
            return entry.registration
        except Exception:
            with self._lock:
                if self._entries.get(agent_id) is entry:
                    entry.root = entry.body = entry.digest = None
                    entry.active = False
            raise InspectionUnavailable("Declaration preparation failed") from None

    def activate(self, registration: Registration, root) -> None:
        """Confirm exact prepared identity after host runner registration.

        Pass the same object supplied to prepare (use runner.agent consistently,
        or the same native App). Unknown/stale handles cannot affect newer data.
        A wrong root for the current generation revokes its prepared content.
        """
        with self._lock:
            entry = self._entries.get(registration.agent_id)
            if entry is None or entry.registration != registration:
                raise InspectionUnavailable("Registration is no longer current")
            if entry.root is None or entry.root() is not root or entry.body is None:
                entry.root = entry.body = entry.digest = None
                entry.active = False
                raise InspectionUnavailable("Prepared root does not match")
            entry.active = True

    def register(self, agent_id: str, root, *, name: str | None = None) -> Registration:
        """Capture and activate an already registered serving root in one call."""
        registration = self.prepare(agent_id, root, name=name)
        self.activate(registration, root)
        return registration

    def invalidate(self, registration: Registration) -> bool:
        """Retire one generation; an old shutdown callback cannot revoke a new one."""
        with self._lock:
            entry = self._entries.get(registration.agent_id)
            if entry is None or entry.registration != registration:
                return False
            del self._entries[registration.agent_id]
            return True

    def clear(self) -> None:
        """Revoke all registrations and any in-progress preparation on shutdown."""
        with self._lock:
            self._entries.clear()

    def agents(self, allowed) -> list[dict]:
        """List only explicitly granted IDs; unavailable entries contain no content."""
        with self._lock:
            return [{"id": key, "name": entry.name, "environment": self.environment}
                    for key, entry in self._entries.items() if key in allowed]

    def snapshot(self, agent_id: str) -> tuple[bytes, str]:
        """Return immutable pre-encoded bytes and digest; never recollect."""
        with self._lock:
            entry = self._entries.get(agent_id)
            if (entry is None or not entry.active or entry.root is None
                    or entry.root() is None or entry.body is None):
                raise InspectionUnavailable("No current registered root")
            return entry.body, entry.digest
