"""Internal state for one local console workspace, not a tenant/session service.

The server holds one instance. HTTP handlers acquire its lock after body reads;
transitions also acquire that reentrant lock for direct lifecycle callers. This
object owns the disposable worker, not host runtime/session persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any

from adkl import execution


@dataclass
class ServerWorkspace:
    root: Path = field(default_factory=Path.cwd)
    web: Path | None = None
    projects: Path | None = None
    sdk_wheel: Path | None = None
    agents: list[dict] = field(default_factory=list)
    profile: str | None = None
    chat: Any = field(default=None, repr=False)
    lock: Any = field(default_factory=threading.RLock, repr=False)

    def reset_chat(self):
        with self.lock:
            if self.chat is not None and hasattr(self.chat, "close"):
                self.chat.close()
            self.chat = None

    def select_agent(self, agent_id: str) -> dict | None:
        """Unknown IDs leave state intact; known IDs discard old conversation."""
        with self.lock:
            for entry in self.agents:
                if entry["id"] == agent_id:
                    self.root = Path(entry["path"])
                    self.reset_chat()
                    self.profile = None
                    return entry
        return None

    def select_profile(self, name: str):
        """Validate before closing the conversation or committing selection."""
        with self.lock:
            if not isinstance(name, str):
                raise execution.ExecutionError("profile is required")
            execution.profile(self.root, name)
            self.reset_chat()
            self.profile = name

    def worker(self):
        """Construct lazily, once; failed construction never caches a worker."""
        with self.lock:
            if self.chat is None:
                self.chat = execution.Worker(self.root, self.profile)
            return self.chat
