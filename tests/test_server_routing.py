"""Public Handler routing and real HTTP boundaries after transport extraction."""
from contextlib import contextmanager
import http.client
import json
import threading
from unittest.mock import Mock

import pytest

from adkl import server


@pytest.mark.parametrize("method,path,endpoint,args", [
    ("GET", "/api/health", "_health", ()),
    ("GET", "/api/agents", "_agents", ()),
    ("GET", "/api/project", "_project_status", ()),
    ("GET", "/api/project/artifact", "_project_artifact", ()),
    ("GET", "/api/audit", "_audit", ()),
    ("GET", "/api/catalog", "_catalog_route", ()),
    ("GET", "/api/workflows", "_workflow_list", ()),
    ("GET", "/api/execution", "_execution_status", ()),
    ("GET", "/api/chat", "_chat_status", ()),
    ("GET", "/api/agent?env=uat&env=prod", "_agent", ("uat",)),
    ("GET", "/api/findings", "_findings", (None,)),
    ("GET", "/api/skills?env=", "_skill_list", (None,)),
    ("GET", "/api/skills/a-1?env=uat", "_skill_get", ("a-1", "uat")),
    ("PUT", "/api/features", "_features_put", ()),
    ("PUT", "/api/skills/a-1?env=uat", "_skill_put", ("a-1", "uat")),
    ("POST", "/api/agents", "_project_post", ("/api/agents",)),
    ("POST", "/api/project/prepare", "_project_post", ("/api/project/prepare",)),
    ("POST", "/api/project/release", "_project_post", ("/api/project/release",)),
    ("POST", "/api/project/inspect", "_project_post", ("/api/project/inspect",)),
    ("POST", "/api/project/credentials", "_project_post", ("/api/project/credentials",)),
    ("POST", "/api/project/baseline", "_project_post", ("/api/project/baseline",)),
    ("POST", "/api/project/candidate", "_project_post", ("/api/project/candidate",)),
    ("POST", "/api/project/doctor", "_project_post", ("/api/project/doctor",)),
    ("POST", "/api/execution/select", "_execution_select", ()),
    ("POST", "/api/chat", "_chat_send", ()),
    ("POST", "/api/chat/clear", "_chat_clear", ()),
    ("POST", "/api/agents/select", "_agents_select", ()),
    ("POST", "/api/skills/a-1/lint?env=uat", "_skill_lint_dry", ("a-1", "uat")),
    ("GET", "/api/skills/UPPER", "_static", ("/api/skills/UPPER",)),
    ("GET", "/index.html?version=1", "_static", ("/index.html",)),
])
def test_method_path_and_query_dispatch(method, path, endpoint, args):
    handler = object.__new__(server.Handler)
    handler.path = path
    callback = Mock(return_value="response")
    setattr(handler, endpoint, callback)
    assert getattr(handler, "_route_" + method.lower())() == "response"
    callback.assert_called_once_with(*args)


@pytest.mark.parametrize("method,path", [
    ("PUT", "/api/health"), ("POST", "/api/features"),
    ("PUT", "/api/skills/UPPER"), ("POST", "/api/skills/a/lint/"),
    ("POST", "/api/project/unknown"), ("PUT", "/api/skills/a%2Fb"),
])
def test_unknown_mutations_are_not_dispatched(method, path):
    handler = object.__new__(server.Handler)
    handler.path = path
    handler._send = Mock()
    getattr(handler, "_route_" + method.lower())()
    handler._send.assert_called_once_with(404, {"error": "not found"})


@contextmanager
def running_server():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(address, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*address, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_real_http_selection_closes_old_worker_and_serves_new_workspace(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    worker = Mock()
    monkeypatch.setattr(server._workspace, "root", one)
    monkeypatch.setattr(server._workspace, "chat", worker)
    monkeypatch.setattr(server._workspace, "profile", "uat")
    monkeypatch.setattr(server._workspace, "agents", [{"id": "two", "path": str(two)}])
    with running_server() as address:
        status, _, data = request(address, "GET", "/api/health")
        assert status == 200 and json.loads(data)["root"] == str(one)
        status, _, data = request(address, "POST", "/api/agents/select", '{"id":"missing"}')
        assert status == 404 and server._workspace.root == one
        worker.close.assert_not_called()
        status, _, data = request(address, "POST", "/api/agents/select", '{"id":"two"}')
        assert status == 200 and json.loads(data)["chatReset"] is True
        worker.close.assert_called_once()
        assert server._workspace.chat is None and server._workspace.profile is None
        status, _, data = request(address, "GET", "/api/health")
        assert status == 200 and json.loads(data)["root"] == str(two)


def test_real_http_rejects_origin_and_bad_json_before_endpoint(tmp_path, monkeypatch):
    endpoint = Mock()
    monkeypatch.setattr(server.Handler, "_agents_select", endpoint)
    with running_server() as address:
        for body, headers, expected in [
            ('{}', {"Origin": "https://untrusted.example"}, 403),
            ('{}', {"Host": "untrusted.example"}, 403),
            ('[]', {}, 400),
            ('{', {}, 400),
        ]:
            status, _, _ = request(address, "POST", "/api/agents/select", body, headers)
            assert status == expected
    endpoint.assert_not_called()


def test_real_http_static_json_is_raw_and_uncached(tmp_path, monkeypatch):
    payload = b'{"version": "fixture"}\n'
    (tmp_path / "version.json").write_bytes(payload)
    monkeypatch.setattr(server._workspace, "web", tmp_path)
    with running_server() as address:
        status, headers, body = request(address, "GET", "/version.json")
        assert status == 200 and body == payload
        assert headers["Content-Type"] == "application/json"
        assert int(headers["Content-Length"]) == len(payload)
        assert "no-store" in headers["Cache-Control"]
        status, _, body = request(address, "OPTIONS", "/api/health")
        assert status == 204 and body == b""
