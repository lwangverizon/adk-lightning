"""Bounded record of warnings and errors logged while a host factory is built.

Hosts often catch a construction failure, log it and keep serving a fallback,
for example a native lane after a duplicate scoped tool name. The inspected
agent is then valid but no longer the configuration the host intended. These
records make that visible to Doctor without replaying traffic.

Formatted arguments, exception text and tracebacks can contain credentials or
customer data, so they are never retained. A message is kept only when it is a
parameterized template (its data travels in arguments); other messages keep a
leading event key such as ``customer_startup_failed`` when one is present.
"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import re

LIMIT = 50
TEXT_LIMIT = 240
_EVENT_KEY = re.compile(r"[a-z][a-z0-9]*(?:[_.:-][a-z0-9]+)+")


def _message(record: logging.LogRecord) -> str | None:
    template = record.msg if isinstance(record.msg, str) else None
    if template is None:
        return None
    if record.args:
        return template[:TEXT_LIMIT]
    head = template.split(maxsplit=1)[0] if template.strip() else ""
    return head[:TEXT_LIMIT] if _EVENT_KEY.fullmatch(head) else None


@contextmanager
def capture(level: int = logging.WARNING):
    """Collect unique WARNING+ records from every logger, without altering them.

    Wraps ``Logger.handle`` for the duration so handler configuration,
    propagation and ``dictConfig`` performed during import cannot hide records.
    The original method is always restored; records still reach host handlers.
    """
    result: dict = {"entries": [], "dropped": 0}
    seen: set[tuple] = set()
    original = logging.Logger.handle

    def handle(self, record):
        try:
            if record.levelno >= level:
                exception = (record.exc_info[0].__name__
                             if record.exc_info and record.exc_info[0] else None)
                entry = {"logger": str(record.name)[:120],
                         "level": logging.getLevelName(record.levelno),
                         "message": _message(record), "exception": exception}
                key = tuple(entry.values())
                if key not in seen:
                    seen.add(key)
                    if len(result["entries"]) < LIMIT:
                        result["entries"].append(entry)
                    else:
                        result["dropped"] += 1
        except Exception:  # Never let diagnostics change host logging.
            pass
        return original(self, record)

    logging.Logger.handle = handle  # type: ignore[method-assign]
    try:
        yield result
    finally:
        logging.Logger.handle = original  # type: ignore[method-assign]


def findings(snapshot: dict) -> list[dict]:
    """Doctor findings for records captured during native factory construction."""
    log = snapshot.get("constructionLog")
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        return []
    raw = []
    for entry in log["entries"]:
        if not isinstance(entry, dict):
            continue
        error = entry.get("level") in {"ERROR", "CRITICAL"}
        detail = entry.get("message") or "message withheld; it may contain data"
        if entry.get("exception"):
            detail += f" ({entry['exception']})"
        raw.append({
            "rule": "FACTORY_CONSTRUCTION_ERROR" if error else "FACTORY_CONSTRUCTION_WARNING",
            # Advisory: Doctor cannot tell whether the host recovered correctly.
            "severity": "WARN" if error else "INFO",
            "message": (f"{entry.get('level')} logged by {entry.get('logger')} while the "
                        f"factory was constructed: {detail}. A caught startup failure can "
                        "leave the inspected agent on a fallback configuration."),
        })
    if log.get("dropped"):
        raw.append({"rule": "FACTORY_CONSTRUCTION_WARNING", "severity": "INFO",
                    "message": f"{log['dropped']} further construction log records were not retained."})
    return raw
