"""Private, task-local Console observations; never an execution input.

A collector exists only while Console consumes one native Runner invocation.
Child tasks inherit it; closed collectors ignore late writes. No callback or
host observer is replaced, and no raw prompt, argument or result is retained.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field


@dataclass
class Collector:
    limit: int = 256
    events: list = field(default_factory=list)
    prepared_requests: int = 0
    dropped: int = 0
    closed: bool = False
    selections: dict = field(default_factory=dict)

    def record(self, event):
        if self.closed:
            return
        if event["type"] == "model_request":
            self.prepared_requests += 1
        if len(self.events) >= self.limit:
            self.dropped += 1
            return
        self.events.append({"sequence": len(self.events) + 1, **event})

    def snapshot(self):
        return {"schemaVersion": 1, "events": list(self.events),
                "preparedModelRequests": self.prepared_requests,
                "droppedEvents": self.dropped,
                "coverage": "shared_binding_requests_and_capability_adapters",
                "limitations": [
                    "Prepared requests are not provider calls or usage; private model calls are excluded.",
                    "Native guards may reject before an adapter; absence of an event is not success.",
                    "Node output events indicate observed output, not internal start times or business success.",
                ]}


_current = ContextVar("adkl_console_trace", default=None)


@contextmanager
def activate(collector):
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


@contextmanager
def capture():
    collector = Collector()
    try:
        with activate(collector):
            yield collector
    finally:
        collector.closed = True


def capability_event(event):
    collector = _current.get()
    if collector is not None:
        collector.record({"type": "capability", **asdict(event)})


def discovery(context, catalog, selected):
    """Record a changed shared shortlist, not a second retrieval pass."""
    collector = _current.get()
    if collector is None or collector.closed:
        return
    names = set(selected.names)
    candidates = [{"name": d.name, "kind": d.kind}
                  for d in catalog if d.name in names]
    signature = (tuple((d["name"], d["kind"]) for d in candidates), selected.fallback)
    key = (context.agent_name, context.invocation_id)
    if collector.selections.get(key) == signature:
        return
    collector.selections[key] = signature
    collector.record({"type": "retrieval", "agent": context.agent_name,
                      "candidates": candidates[:128], "fallback": selected.fallback,
                      "candidatesTruncated": len(candidates) > 128})


def node_output(event):
    """Read public ADK node metadata only; never store node payloads."""
    collector = _current.get()
    info = getattr(event, "node_info", None)
    if collector is None or collector.closed or info is None:
        return
    if not (getattr(info, "output_for", None) or getattr(info, "message_as_output", False)):
        return
    path = getattr(info, "path", "")
    if not path:
        return
    # Run IDs may contain provider thought suffixes. Keep declared node names.
    nodes = [segment.split("@", 1)[0][:128] for segment in path.split("/")][:16]
    collector.record({"type": "node_output", "agent": event.author,
                      "path": "/".join(nodes), "status": "output_observed"})


def model_request(context, request):
    collector = _current.get()
    if collector is None or collector.closed:
        return
    # Read only declarations already prepared for this request. Never enumerate
    # toolsets (which may perform retrieval), nor count the registered catalog
    # as current availability. Bound the per-event metadata as well as the list.
    names = sorted({declaration.name
                    for group in request.config.tools or []
                    for declaration in getattr(group, "function_declarations", None) or []
                    if declaration.name})
    collector.record({"type": "model_request", "agent": context.agent_name,
                      "invocation_id": context.invocation_id,
                      "tools": names[:128], "toolsTruncated": len(names) > 128})
