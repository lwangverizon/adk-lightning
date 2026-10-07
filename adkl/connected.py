"""Read-only declaration gateway. No developer-server workspace or host imports.

Deploy only behind an authenticating proxy that strips and replaces identity
headers and supplies a private gateway credential. Direct-origin network denial
is also required. This module does not implement an OIDC login flow.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 4 * 1024 * 1024
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class Unavailable(Exception):
    """Safe public upstream error; never forward exception details."""


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    environment: str
    url: str = field(repr=False)
    token: str = field(repr=False)

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if (not SLUG.fullmatch(self.id) or self.environment != "uat"
                or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path != f"/internal/adkl/v1/agents/{self.id}/declarations"
                or len(self.token) < 32):
            raise ValueError("Invalid private inspection target")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unavailable("Upstream redirect refused")


def fetch(target: Target) -> bytes:
    """Fixed approved HTTPS endpoint; bounded body and read deadline, no proxy env."""
    from urllib.request import ProxyHandler

    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()),
                          NoRedirect())
    request = Request(target.url, headers={"Authorization": f"Bearer {target.token}",
                                           "Accept": "application/json"})
    deadline = time.monotonic() + 10
    with opener.open(request, timeout=3) as response:
        if response.status != 200 or response.headers.get_content_type() != "application/json":
            raise Unavailable("Invalid upstream response")
        chunks, count = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise Unavailable("Upstream deadline exceeded")
            chunk = response.read1(min(65536, MAX_BYTES + 1 - count))
            count += len(chunk)
            if count > MAX_BYTES:
                raise Unavailable("Upstream response too large")
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


def validate(body: bytes, target: Target) -> dict:
    """Validate identity and structural limits before exposing rich host content."""
    if len(body) > MAX_BYTES:
        raise Unavailable("Oversized declaration")
    try:
        from adkl.connected_schema import declarations, document

        data = declarations(document(body))
        if (data["schemaVersion"] != 1 or data["kind"] != "adkl_loaded_declarations"
                or data["agentId"] != target.id or data["environment"] != target.environment
                or data["capabilities"] != {"chat": False, "edit": False, "sessions": False}
                or not isinstance(data["agents"], list) or not 1 <= len(data["agents"]) <= 128):
            raise ValueError()
        for key in ("instanceId", "release", "generation", "capturedAt", "scope", "root"):
            if not isinstance(data[key], str) or not data[key] or len(data[key]) > 1024:
                raise ValueError()
        captured = datetime.fromisoformat(data["capturedAt"])
        if captured.tzinfo is None or captured.timestamp() > time.time() + 60:
            raise ValueError()
        def named_rows(rows, *, skills=False):
            if not isinstance(rows, list) or len(rows) > 256:
                raise ValueError()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError()
                for key in ("id", "owner", "name"):
                    if not isinstance(row[key], str):
                        raise ValueError()
                if skills:
                    instruction = row["instructions"]
                    if instruction["kind"] not in ("static", "dynamic", "absent"):
                        raise ValueError()
                    if instruction["kind"] == "static" and not isinstance(instruction["text"], str):
                        raise ValueError()

        ids = set()
        for node in data["agents"]:
            if not isinstance(node["id"], str) or node["id"] in ids:
                raise ValueError()
            ids.add(node["id"])
            if not isinstance(node["name"], str):
                raise ValueError()
            for key in ("children", "tools", "toolsets", "bindings"):
                if not isinstance(node[key], list) or len(node[key]) > 256:
                    raise ValueError()
            named_rows(node["tools"])
            for group in node["toolsets"] + node["bindings"]:
                for key in ("id", "owner"):
                    if not isinstance(group[key], str):
                        raise ValueError()
                named_rows(group["skills"], skills=True)
                named_rows(group["tools"])
            for key in ("instruction", "globalInstruction"):
                value = node[key]
                if value["kind"] not in ("static", "dynamic", "absent"):
                    raise ValueError()
                if value["kind"] == "static" and not isinstance(value["text"], str):
                    raise ValueError()
        if data["root"] not in ids or any(
                child not in ids for node in data["agents"] for child in node["children"]):
            raise ValueError()
        if not isinstance(data.get("limitations", []), list):
            raise ValueError()
        # Bounded JSON primitives/depth independent of known schema fields.
        pending, count = [(data, 0)], 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if count > 100000 or depth > 32:
                raise ValueError()
            if isinstance(value, dict):
                pending.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, list):
                pending.extend((v, depth + 1) for v in value)
        return data
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise Unavailable("Invalid declaration protocol") from error


class Gateway:
    """Fixed registry and per-request principal ACL; no global selected agent."""

    def __init__(self, targets: list[Target], grants: dict[str, list[str]],
                 proxy_token: str, *, transport=fetch, evidence_dir=None):
        if (len(proxy_token) < 32 or len(targets) > 32
                or any(hmac.compare_digest(proxy_token, target.token) for target in targets)):
            raise ValueError("Invalid gateway configuration")
        self.targets = {target.id: target for target in targets}
        if len(self.targets) != len(targets):
            raise ValueError("Duplicate target")
        if any(not set(ids) <= self.targets.keys() for ids in grants.values()):
            raise ValueError("Unknown grant target")
        self.grants = {subject: frozenset(ids) for subject, ids in grants.items()}
        self.proxy_token = proxy_token
        self.transport = transport
        # Trusted operator-mounted evidence only; no browser-supplied paths.
        from adkl.connected_evidence import EvidenceStore
        self.evidence = EvidenceStore(evidence_dir)
        self.cache = {}
        self.locks = {key: threading.Lock() for key in self.targets}
        self.slots = threading.BoundedSemaphore(4)

    def handle(self, method: str, path: str, headers: dict) -> tuple[int, dict]:
        # Proxy authenticates SSO, strips browser values, and injects both headers.
        # Auth and authorization precede every cache lookup, including hits.
        credential = headers.get("x-adkl-proxy-token", "")
        if not hmac.compare_digest(credential.encode(), self.proxy_token.encode()):
            return 401, {"error": "Authentication required"}
        subject = headers.get("x-adkl-subject", "")
        allowed = self.grants.get(subject)
        if allowed is None:
            return 403, {"error": "Access denied"}
        if method != "GET":
            return 405, {"error": "Read only"}
        if path == "/api/v1/agents":
            return 200, {"schemaVersion": 1, "mode": "connected", "agents": [
                {"id": t.id, "name": t.name, "environment": t.environment}
                for t in self.targets.values() if t.id in allowed]}
        match = re.fullmatch(r"/api/v1/agents/([a-z0-9-]+)/inspection", path)
        if not match or match[1] not in allowed:
            return 404, {"error": "Not found"}
        agent_id = match[1]
        if not self.slots.acquire(blocking=False):
            return 503, {"error": "Inspection busy"}
        try:
            with self.locks[agent_id]:
                cached = self.cache.get(agent_id)
                if cached and time.monotonic() < cached[0]:
                    return 200, self._with_evidence(cached[1])
                self.cache.pop(agent_id, None)
                target = self.targets[agent_id]
                body = self.transport(target)
                data = validate(body, target)
                envelope = {
                    "schemaVersion": 1, "mode": "connected", "snapshot": data,
                    "gatewayReceivedAt": datetime.now(timezone.utc).isoformat(),
                    "observationValidForSeconds": 15,
                    "snapshotDigest": hashlib.sha256(body).hexdigest(),
                }
                self.cache[agent_id] = (time.monotonic() + 15, envelope)
                return 200, self._with_evidence(envelope)
        except Exception:
            return 503, {"error": "Inspection unavailable or invalid"}
        finally:
            self.slots.release()

    def _with_evidence(self, envelope):
        # Check receipt/expiry on every authorized read, including cache hits.
        return {**envelope, "retainedEvidence": self.evidence.summary(
            envelope["snapshot"], envelope["snapshotDigest"])}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        headers = {}
        duplicate = False
        for key, value in scope.get("headers", []):
            name = key.decode("latin-1").lower()
            if name in headers:
                duplicate = True
            headers[name] = value.decode("latin-1")
        if duplicate or scope.get("query_string"):
            status, data = 400, {"error": "Invalid request"}
        else:
            status, data = await asyncio.to_thread(
                self.handle, scope["method"], scope["path"], headers)
        body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def configured(path: str) -> Gateway:
    """Read trusted deployment config; tokens come from named secret env vars."""
    with open(path, encoding="utf-8") as source:
        config = json.load(source)
    targets = [Target(t["id"], t["name"], t["environment"], t["url"],
                      os.environ[t["tokenEnv"]]) for t in config["targets"]]
    return Gateway(targets, config["grants"], os.environ[config["proxyTokenEnv"]],
                   evidence_dir=config.get("evidenceDirectory"))


def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    uvicorn.run(configured(args.config), host=args.host, port=args.port,
                lifespan="off", access_log=False, limit_concurrency=32)


if __name__ == "__main__":
    main()
