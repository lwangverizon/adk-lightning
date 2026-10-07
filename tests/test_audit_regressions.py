"""Behavioral regressions from the main-branch audit; no external hosts."""
import io
import threading
from email.message import Message
from http.client import HTTPConnection

import pytest

from adkl import execution, features, server, verification
from adkl.retrieval import RetrievalConfig


@pytest.mark.parametrize("options", [
    {"query_history_weight": 2.}, {"query_history_weight": float("nan")},
    {"top_k": -1}, {"total_cap": 1}, {"min_score": float("nan")},
    {"auto_activate_min_margin": float("inf")}, {"auto_activate_min_score": 2.},
    {"top_k": True}, {"dimensions": -1}, {"min_score": 10**400},
])
def test_ranking_validation_matches_direct_and_declarative_paths(options):
    with pytest.raises(ValueError):
        features.resolve({"pipeline": [], "skill_retrieval": options})
    with pytest.raises(ValueError):
        RetrievalConfig(**options)


@pytest.mark.parametrize("target", ["cache/rules.py", "runtime/rules.py", "secret.json", "untracked.txt"])
def test_alias_targets_must_actually_be_hashed(tmp_path, target):
    (tmp_path / "agent.yaml").write_text("{}")
    (tmp_path / "execution.yaml").write_text(
        "source:\n  content: []\n  exclude: [cache]\nprofiles:\n  local:\n    env_file: secret.json\n")
    path = tmp_path / target
    path.parent.mkdir(exist_ok=True)
    path.write_text("content")
    if target.startswith("runtime/"):
        (tmp_path / "runtime/pyvenv.cfg").write_text("home = fixture")
    (tmp_path / "alias.py").symlink_to(path)
    with pytest.raises(execution.ExecutionError, match="alias.py"):
        execution.source_digest(tmp_path)


@pytest.mark.parametrize("content,exclude", [("prompts", "prompts/rules.md"), ("prompts/rules.md", "prompts")])
def test_content_exclude_ancestor_conflicts(tmp_path, content, exclude):
    (tmp_path / "execution.yaml").write_text(f"source:\n  content: [{content}]\n  exclude: [{exclude}]\n")
    with pytest.raises(execution.ExecutionError, match="content and exclude"):
        execution.source_digest(tmp_path)


def test_verification_rejects_recursive_directory_alias(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules/again").symlink_to(tmp_path / "rules")
    with pytest.raises(ValueError, match="cycle"):
        verification._files(tmp_path.resolve(), ["rules"])


@pytest.mark.parametrize("length,body,status", [
    ("-1", b"", 400), ("abc", b"", 400), ("1048577", b"", 413),
    ("5", b"{}", 400), ("1", b"{", 400), ("2", b"[]", 400),
])
def test_request_body_validation(length, body, status):
    handler = object.__new__(server.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = length
    handler.rfile = io.BytesIO(body)
    with pytest.raises(server.RequestError) as error:
        handler._body()
    assert error.value.status == status


@pytest.mark.parametrize("header", ["duplicate", "chunked"])
def test_ambiguous_request_framing_is_refused(header):
    handler = object.__new__(server.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    handler.headers["Content-Length" if header == "duplicate" else "Transfer-Encoding"] = (
        "2" if header == "duplicate" else "chunked")
    handler.rfile = io.BytesIO(b"{}")
    with pytest.raises(server.RequestError) as error:
        handler._body()
    assert error.value.status == 400


def test_request_body_is_read_once():
    handler = object.__new__(server.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    handler.rfile = io.BytesIO(b"{}")
    assert handler._body() is handler._body()


def test_incomplete_post_does_not_hold_workspace_lock_and_times_out():
    entered = threading.Event()
    class Handler(server.Handler):
        BODY_TIMEOUT = .5
        def _body(self):
            entered.set()
            return super()._body()
        def log_message(self, *args):
            pass
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    slow = HTTPConnection(*httpd.server_address, timeout=3)
    fast = HTTPConnection(*httpd.server_address, timeout=2)
    try:
        slow.putrequest("POST", "/api/chat")
        slow.putheader("Content-Length", "100")
        slow.endheaders(b"{")
        assert entered.wait(2)
        fast.request("GET", "/api/health")
        response = fast.getresponse()
        assert response.status == 200
        response.read()
        assert slow.getresponse().status == 408
    finally:
        slow.close()
        fast.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
