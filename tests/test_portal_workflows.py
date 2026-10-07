"""Workflow export ownership, disclosure and publication identity contracts."""
import json

import pytest

from adkl import portal


def test_projection_keeps_owners_and_missing_topology(tmp_path):
    def agent(name):
        return {"name": name, "children": [], "adkl": [{
            "skills": [{"name": "help", "description": "Help", "tools": [],
                        "instructions": "## Overview\nPrivate instructions"}],
            "tools": [], "capabilities": [{"name": "flow", "kind": "workflow",
                "description": "Flow", "operation": None}],
            "workflowGraphs": []}]}
    root = agent("parent")
    root["children"] = [agent("child")]
    result = portal._inventory(tmp_path, {}, {"agent": root}, False)
    assert len(result["workflows"]) == 2
    assert len({row["owner"] for row in result["workflows"]}) == 2
    assert all(row["graph"] is None for row in result["workflows"])
    assert "Private instructions" not in json.dumps(result)
    disclosed = portal._inventory(tmp_path, {}, {"agent": root}, True)
    assert disclosed["skills"][0]["doc"]["sections"]


def test_registry_identity_and_invalid_identity_cleanup(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"agents": [{"id": "a", "name": "A"}]}),
                        encoding="utf-8")
    out = tmp_path / "data"
    portal.assemble(registry, out, ui_build="0123456789abcdef")
    assert json.loads((out / "registry.json").read_text())["uiBuild"] == "0123456789abcdef"
    with pytest.raises(ValueError, match="UI build"):
        portal.assemble(registry, tmp_path / "bad", ui_build="bad")
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".portal-*"))
