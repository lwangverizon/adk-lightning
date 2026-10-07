"""Selected workspace lifecycle, independent of HTTP and native ADK imports."""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock

import pytest

from adkl import execution, server
from adkl._server_workspace import ServerWorkspace
from tests.support.projects import make_project


def test_workspace_defaults_do_not_share_mutable_state_or_locks():
    one, two = ServerWorkspace(), ServerWorkspace()
    one.agents.append({"id": "one"})
    assert two.agents == []
    assert one.lock is not two.lock
    assert one.chat is None and two.chat is None


def test_parallel_worker_access_constructs_once_and_reset_closes_once(tmp_path, monkeypatch):
    workspace = ServerWorkspace(root=tmp_path, profile="uat")
    worker = Mock()
    constructor = Mock(return_value=worker)
    monkeypatch.setattr(execution, "Worker", constructor)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: workspace.worker(), range(8))) == [worker] * 8
    constructor.assert_called_once_with(tmp_path, "uat")
    workspace.reset_chat()
    workspace.reset_chat()
    worker.close.assert_called_once()
    assert workspace.chat is None


def test_worker_construction_failure_can_retry(tmp_path, monkeypatch):
    workspace = ServerWorkspace(root=tmp_path)
    worker = Mock()
    constructor = Mock(side_effect=[execution.ExecutionError("unavailable"), worker])
    monkeypatch.setattr(execution, "Worker", constructor)
    with pytest.raises(execution.ExecutionError):
        workspace.worker()
    assert workspace.chat is None
    assert workspace.worker() is worker
    assert constructor.call_count == 2


def test_unknown_agent_preserves_worker_and_profile_and_current_selection_resets(tmp_path):
    worker = Mock()
    entry = {"id": "current", "path": str(tmp_path)}
    workspace = ServerWorkspace(root=tmp_path, agents=[entry], profile="uat", chat=worker)
    assert workspace.select_agent("missing") is None
    assert workspace.chat is worker and workspace.profile == "uat"
    worker.close.assert_not_called()
    assert workspace.select_agent("current") is entry
    worker.close.assert_called_once()
    assert workspace.root == tmp_path and workspace.profile is None and workspace.chat is None


def test_profile_validation_precedes_worker_reset(tmp_path):
    root = make_project(tmp_path / "project")
    worker = Mock()
    workspace = ServerWorkspace(root=root, profile=None, chat=worker)
    for invalid in (None, "missing"):
        with pytest.raises(execution.ExecutionError):
            workspace.select_profile(invalid)
        assert workspace.chat is worker and workspace.profile is None
    worker.close.assert_not_called()
    workspace.select_profile("local")
    worker.close.assert_called_once()
    assert workspace.chat is None and workspace.profile == "local"


def test_close_failure_retains_worker_and_prevents_profile_commit(tmp_path):
    root = make_project(tmp_path / "project")
    worker = Mock()
    worker.close.side_effect = OSError("close failed")
    workspace = ServerWorkspace(root=root, chat=worker)
    with pytest.raises(OSError, match="close failed"):
        workspace.select_profile("local")
    assert workspace.chat is worker and workspace.profile is None


def test_direct_transition_waits_for_workspace_operation_lock(tmp_path):
    workspace = ServerWorkspace(root=tmp_path, chat=Mock())
    started = threading.Event()
    def reset():
        started.set()
        workspace.reset_chat()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with workspace.lock:
            pending = pool.submit(reset)
            assert started.wait(2)
            assert not pending.done()
            workspace.chat.close.assert_not_called()
        pending.result(timeout=2)
    assert workspace.chat is None


def test_server_helpers_use_replacement_workspace_not_captured_state(tmp_path, monkeypatch):
    workspace = ServerWorkspace(root=tmp_path, chat=Mock(),
                                agents=[{"id": "next", "path": str(tmp_path / "next")}])
    monkeypatch.setattr(server, "_workspace", workspace)
    worker = workspace.chat
    assert server.select_agent("next")["id"] == "next"
    worker.close.assert_called_once()
    server.reset_chat()
    assert workspace.chat is None
