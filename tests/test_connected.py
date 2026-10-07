"""Credential-free protocol, ACL and transport-boundary regressions."""
import asyncio
from datetime import datetime, timezone
import json
import secrets

import pytest

from adkl.connected import Gateway, NoRedirect, Target, Unavailable, validate


def declaration(agent_id="prospect"):
    return {
        "schemaVersion": 1, "kind": "adkl_loaded_declarations", "agentId": agent_id,
        "environment": "uat", "instanceId": "fixture-process", "release": "fixture-release",
        "generation": "fixture-generation", "capturedAt": datetime.now(timezone.utc).isoformat(),
        "scope": "registered runner root; declarations at registration", "root": "agents/0",
        "capabilities": {"chat": False, "edit": False, "sessions": False},
        "checks": {"doctor": "not_run", "behavioral": "not_run"},
        "limitations": ["Synthetic declarations, no deployed UAT evidence."],
        "agents": [{"id": "agents/0", "name": agent_id, "description": "Fixture agent",
                    "instruction": {"kind": "static", "text": "Approved procedure"},
                    "globalInstruction": {"kind": "dynamic", "evaluated": False},
                    "children": [], "tools": [], "toolsets": [], "bindings": []}],
    }


@pytest.fixture
def gateway():
    token = secrets.token_hex(32)
    targets = [Target(id, id, "uat", f"https://approved.internal/internal/adkl/v1/agents/{id}/declarations", token)
               for id in ("prospect", "classic-search")]
    calls = []

    def transport(target):
        calls.append(target.id)
        return json.dumps(declaration(target.id)).encode()

    proxy_token = secrets.token_hex(32)
    service = Gateway(targets, {"alice": ["prospect"], "bob": ["classic-search"]},
                      proxy_token, transport=transport)
    headers = {"x-adkl-proxy-token": proxy_token, "x-adkl-subject": "alice"}
    return service, headers, calls


def test_authorization_precedes_registry_fetch_and_cached_reads(gateway):
    service, headers, calls = gateway
    path = "/api/v1/agents/prospect/inspection"
    assert service.handle("GET", path, {})[0] == 401
    assert service.handle("GET", path, {**headers, "x-adkl-subject": "unknown"})[0] == 403
    assert calls == []
    registry = service.handle("GET", "/api/v1/agents", headers)[1]
    assert [row["id"] for row in registry["agents"]] == ["prospect"]
    assert "approved.internal" not in json.dumps(registry)
    first = service.handle("GET", path, headers)
    assert first[0] == 200
    assert service.handle("GET", path, headers) == first
    assert calls == ["prospect"]
    assert service.handle("GET", path, {**headers, "x-adkl-subject": "bob"})[0] == 404
    assert calls == ["prospect"]
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert service.handle(method, path, headers)[0] == 405


def test_expired_failure_cannot_serve_previous_content(gateway, monkeypatch):
    service, headers, _ = gateway
    path = "/api/v1/agents/prospect/inspection"
    assert service.handle("GET", path, headers)[0] == 200
    service.cache["prospect"] = (0, service.cache["prospect"][1])

    def fail(_):
        raise RuntimeError("private upstream URL and secret")

    monkeypatch.setattr(service, "transport", fail)
    status, data = service.handle("GET", path, headers)
    assert status == 503
    assert "secret" not in json.dumps(data)
    assert "prospect" not in service.cache


@pytest.mark.parametrize("change", [
    {"kind": "adkl_static_inspection"}, {"agentId": "classic-search"},
    {"environment": "prod"}, {"schemaVersion": 2}, {"agents": []},
    {"capturedAt": "not-a-date"}, {"root": "missing"},
    {"capabilities": {"chat": True, "edit": False, "sessions": False}},
])
def test_protocol_mismatch_refused(gateway, change):
    service, _, _ = gateway
    with pytest.raises(Unavailable):
        validate(json.dumps({**declaration(), **change}).encode(), service.targets["prospect"])


def test_target_and_redirect_restrictions():
    token = secrets.token_hex(32)
    path = "/internal/adkl/v1/agents/prospect/declarations"
    for url in ("http://host" + path, "https://user:pass@host" + path,
                "https://host" + path + "?url=https://other", "https://host/arbitrary"):
        with pytest.raises(ValueError):
            Target("prospect", "Prospect", "uat", url, token)
    with pytest.raises(Unavailable):
        NoRedirect().redirect_request(None, None, 302, None, {}, "https://other")


def test_asgi_headers_no_store_and_duplicate_identity_denial(gateway):
    service, headers, _ = gateway

    async def request(extra=()):
        sent = []

        async def send(message):
            sent.append(message)

        await service({"type": "http", "method": "GET", "path": "/api/v1/agents",
                       "headers": [(k.encode(), v.encode()) for k, v in headers.items()] + list(extra)},
                      None, send)
        return sent

    response = asyncio.run(request())
    assert response[0]["status"] == 200
    assert (b"cache-control", b"no-store") in response[0]["headers"]
    assert asyncio.run(request([(b"x-adkl-subject", b"bob")]))[0]["status"] == 400


def test_transport_slots_bounded(gateway):
    service, headers, _ = gateway
    for _ in range(4):
        assert service.slots.acquire(blocking=False)
    assert service.handle("GET", "/api/v1/agents/prospect/inspection", headers)[0] == 503
    for _ in range(4):
        service.slots.release()
