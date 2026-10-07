"""Browsing snapshots are cheap; execution still validates source bytes."""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from adkl import execution, server
from tests.support.projects import WORKER_CODE, make_project


@pytest.fixture
def worker(tmp_path):
    root = make_project(tmp_path / "project")
    (root / "collision.py").write_text("VALUE = 'one'\n")
    platform = tmp_path / "platform/adkl"
    platform.mkdir(parents=True)
    (platform / "worker.py").write_text(WORKER_CODE)
    with patch.object(execution, "PLATFORM", platform.parent):
        current = execution.Worker(root)
        try:
            yield current
        finally:
            current.close()


def test_repeated_and_parallel_browsing_reuses_isolated_snapshot(worker):
    with patch.object(worker, "request", wraps=worker.request) as request:
        first = worker.browse_inspection()
        first["value"] = "caller mutation"
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: worker.browse_inspection(), range(8)))
        assert all(report["value"] == "one" for report in reports)
        assert request.call_count == 1
        worker.request("status")
        worker.browse_inspection()
        assert request.call_count == 3  # execution invalidates the browsing snapshot
        worker.close()
        worker.browse_inspection()
        assert request.call_count == 4


@pytest.mark.parametrize("change", ["edit", "add", "delete", "rename", "replace", "profile", "link"])
def test_external_source_changes_never_reuse_browsing_snapshot(worker, change):
    worker.browse_inspection()
    source = worker.root / "collision.py"
    before = source.stat()
    if change == "edit":
        source.write_text("VALUE = 'two'\n")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "add":
        (worker.root / "added.json").write_text("{}")
    elif change == "delete":
        source.unlink()
    elif change == "rename":
        source.rename(worker.root / "renamed.py")
    elif change == "replace":
        replacement = worker.root / ".replacement"
        replacement.write_text("VALUE = 'two'\n")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(source)
    elif change == "profile":
        with (worker.root / "execution.yaml").open("a") as stream:
            stream.write("\n# edited profile\n")
    else:
        source.unlink()
        source.symlink_to(worker.root.parent / "outside.py")
    with pytest.raises(execution.ExecutionError):
        worker.browse_inspection()


def test_resource_and_dependency_marker_changes_invalidate(worker):
    skill = worker.root / "skills/math"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Math")
    (skill / "asset.bin").write_bytes(b"first")
    # Source changed before this worker was used; construct it at this revision.
    current = execution.Worker(worker.root)
    try:
        current.browse_inspection()
        (skill / "asset.bin").write_bytes(b"other")
        with pytest.raises(execution.StaleConversationError):
            current.browse_inspection()
    finally:
        current.close()
    dependency = worker.root / "custom-env"
    dependency.mkdir()
    (dependency / "pyvenv.cfg").write_text("home = synthetic")
    (dependency / "code.py").write_text("ignored = True")
    current = execution.Worker(worker.root)
    try:
        current.browse_inspection()
        (dependency / "pyvenv.cfg").unlink()
        with pytest.raises(execution.StaleConversationError):
            current.browse_inspection()
    finally:
        current.close()


def test_project_workers_do_not_share_cached_reports(worker):
    other_root = make_project(worker.root.parent / "other")
    (other_root / "collision.py").write_text("VALUE = 'other'\n")
    other = execution.Worker(other_root)
    try:
        assert worker.browse_inspection()["value"] == "one"
        assert other.browse_inspection()["value"] == "other"
        assert worker.browse_inspection()["value"] == "one"
    finally:
        other.close()


def test_metadata_is_not_an_execution_guard(worker):
    worker.browse_inspection()
    (worker.root / "collision.py").write_text("VALUE = 'two'\n")
    # Even if a filesystem or adversarial caller defeats the browsing hint,
    # status/chat/stream/edit inspection continue to use the full source digest.
    with pytest.raises(execution.StaleConversationError):
        worker.request("status")


def test_metadata_walk_does_not_reopen_tracked_content(worker):
    from pathlib import Path
    with patch.object(Path, "read_bytes", side_effect=AssertionError("content reread")):
        assert execution.source_digest(worker.root, _metadata_only=True)


def test_mutation_during_inspection_is_not_cached(worker):
    real_request = worker.request
    def mutate(operation):
        result = real_request(operation)
        (worker.root / "added.py").write_text("changed = True\n")
        return result
    with patch.object(worker, "request", side_effect=mutate):
        with pytest.raises(execution.StaleConversationError, match="during inspection"):
            worker.browse_inspection()
    assert worker._inspection is None


def test_server_only_uses_browsing_cache_when_requested(worker):
    handler = object.__new__(server.Handler)
    handler._chat = lambda: worker
    doc = {"agent": {"factory": "collision:build"}}
    report = {"agent": {"adkl": [], "children": []}}
    with patch.object(server, "_load_agent", return_value=(doc, [], [])), \
            patch.object(worker, "browse_inspection", return_value=report) as browse, \
            patch.object(worker, "request", return_value=report) as request:
        handler._native_workspace(browse=True)
        browse.assert_called_once()
        request.assert_not_called()
        handler._native_workspace()  # edits and dry-run lint use the strict path
        request.assert_called_once_with("inspect")
