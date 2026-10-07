"""Native declarations, publication lifecycle and host transport without credentials."""
import gc
import json
import threading
import weakref

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.apps import App
from google.adk.tools import FunctionTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.skill_toolset import SkillToolset

from adkl.embedded import InspectionRegistry, InspectionUnavailable
from adkl.inspection_fastapi import mount_inspection
from tests.support.native import plain_skill


def registry():
    return InspectionRegistry(environment="uat", release="fixture-release", content_approved=True)


def agent(name="host"):
    return LlmAgent(name=name, model="fixture-model", instruction="Approved instructions")


def decoded(service, agent_id="host"):
    return json.loads(service.snapshot(agent_id)[0])


def test_native_content_and_gateway_protocol_without_runtime_execution():
    from adkl.connected import Target, validate
    from adkl.inspection import attach

    def lookup(query: str, enabled: bool = False) -> dict:
        """Look up the requested item."""
        raise AssertionError("Must not execute tools")

    tool = FunctionTool(lookup)
    native = SkillToolset(skills=[plain_skill("help")], additional_tools=[tool])
    root = agent()
    root.tools = [native, tool]
    attach(root, skills=[plain_skill("attached")], tools=[tool])
    original_tools = list(root.tools)
    service = registry()
    handle = service.register("host", root)
    body, digest = service.snapshot("host")
    target = Target("host", "Host", "uat",
                    "https://fixture.invalid/internal/adkl/v1/agents/host/declarations", "a" * 32)
    data = validate(body, target)
    assert data["generation"] == handle.generation
    node = data["agents"][0]
    assert node["instruction"]["text"] == "Approved instructions"
    assert node["toolsets"][0]["skills"][0]["name"] == "help"
    assert node["bindings"][0]["skillsOwner"] == "host"
    schema = node["tools"][0]["schema"]
    parameters = schema.get("parameters") or schema["parameters_json_schema"]
    assert parameters["properties"]["enabled"]["type"].lower() == "boolean"
    assert root.tools == original_tools and root.before_agent_callback is None
    assert len(digest) == 64


def test_no_dynamic_hooks_or_custom_schema_provider():
    class DynamicToolset(BaseToolset):
        async def get_tools(self, readonly_context=None):
            raise AssertionError("Must not enumerate")

    class CustomTool(FunctionTool):
        def _get_declaration(self):
            raise AssertionError("Must not invoke custom providers")

    def forbidden(*args, **kwargs):
        raise AssertionError("Must not invoke instructions/callbacks/tools")

    root = LlmAgent(name="host", model="fixture", instruction=forbidden,
                    before_agent_callback=forbidden,
                    tools=[DynamicToolset(), CustomTool(forbidden)])
    service = registry()
    service.register("host", root)
    node = decoded(service)["agents"][0]
    assert node["instruction"] == {"kind": "dynamic", "evaluated": False}
    assert node["tools"][0]["schemaStatus"] == "not_collected"
    assert "not called" in node["toolsets"][0]["omission"]


def test_prepare_activate_refresh_invalidate_and_old_shutdown():
    root = agent()
    service = registry()
    first = service.prepare("host", root)
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")
    service.activate(first, root)
    before = decoded(service)
    root.instruction = "New procedure"
    assert decoded(service) == before  # No implied mutation watcher.
    second = service.register("host", root)
    assert second != first
    assert not service.invalidate(first)
    assert decoded(service)["agents"][0]["instruction"]["text"] == "New procedure"
    with pytest.raises(InspectionUnavailable):
        service.activate(first, root)
    assert decoded(service)["generation"] == second.generation
    assert service.invalidate(second)
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")


def test_failed_refresh_wrong_identity_and_garbage_collection_revoke():
    service, root = registry(), agent()
    handle = service.register("host", root)
    with pytest.raises(InspectionUnavailable):
        service.activate(handle, agent("different"))
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")
    service.register("host", root)
    root.instruction = "x" * (512 * 1024 + 1)
    with pytest.raises(InspectionUnavailable, match="preparation failed"):
        service.register("host", root)
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")
    root.instruction = "ok"
    service.register("host", root)
    reference = weakref.ref(root)
    del root
    gc.collect()
    assert reference() is None
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")


def test_late_collection_cannot_resurrect_retired_or_replaced_root(monkeypatch):
    from adkl import embedded
    real_collect = embedded.collect
    entered, finish = threading.Event(), threading.Event()
    old, new = agent("old"), agent("new")
    failures = []

    def collect(root):
        if root is old:
            entered.set()
            assert finish.wait(10)
        return real_collect(root)

    monkeypatch.setattr(embedded, "collect", collect)
    service = registry()

    def prepare():
        try:
            service.register("host", old)
        except InspectionUnavailable:
            failures.append(True)

    worker = threading.Thread(target=prepare)
    worker.start()
    assert entered.wait(10)
    with pytest.raises(InspectionUnavailable):
        service.snapshot("host")
    service.clear()
    current = service.register("host", new)
    finish.set()
    worker.join(10)
    assert not worker.is_alive() and failures == [True]
    assert decoded(service)["generation"] == current.generation
    assert decoded(service)["agents"][0]["name"] == "new"


def test_reads_never_collect_or_regenerate_schema(monkeypatch):
    from adkl import embedded
    root, service = agent(), registry()
    service.register("host", root)
    before = service.snapshot("host")
    monkeypatch.setattr(embedded, "collect", lambda _: pytest.fail("read collected"))
    for _ in range(20):
        body, digest = service.snapshot("host")
        assert body is before[0] and digest == before[1]


def test_app_and_capability_workflow_inspection():
    from pydantic import BaseModel
    from google.adk.workflow import Workflow, FunctionNode
    from adkl.capability_bindings import CapabilityBindings

    class Input(BaseModel):
        query: str

    async def forbidden(ctx, node_input: Input):
        raise AssertionError("No workflow execution")

    choose = FunctionNode(name="choose", func=forbidden)
    left = FunctionNode(name="left", func=forbidden)
    right = FunctionNode(name="right", func=forbidden)
    flow = Workflow(name="flow", description="Choose a path", input_schema=Input,
                    edges=[("START", choose), (choose, {"a": left, "b": right})])
    binding = CapabilityBindings(skills=[plain_skill("help")], workflows=[flow])
    native = LlmAgent(name="host", model="fixture", **binding.agent_kwargs())
    root = App(name="fixture", root_agent=native)
    service = registry()
    service.register("host", root)
    data = decoded(service)
    assert {node["name"] for node in data["agents"]} >= {"host", "flow", "choose", "left", "right"}
    assert data["agents"][0]["bindings"][0]["skillsOwner"] == "adkl"


def test_limits_and_content_approval():
    service = InspectionRegistry(environment="local", release="fixture")
    with pytest.raises(InspectionUnavailable):
        service.register("host", agent())
    service = registry()
    with pytest.raises(ValueError):
        service.register("../host", agent())
    root = SequentialAgent(name="root", sub_agents=[agent(f"child{i}") for i in range(128)])
    with pytest.raises(InspectionUnavailable):
        service.register("host", root)
    with pytest.raises(InspectionUnavailable):
        service.register("host", object())


@pytest.fixture
def http_service():
    service = registry()
    roots = [agent("alpha"), agent("beta")]
    handles = [service.register(root.name, root) for root in roots]
    calls = []

    async def authorize(request: Request):
        calls.append(request.url.path)
        if request.headers.get("authorization") != "Bearer fixture":
            raise HTTPException(401, "Sign in")
        return {"alpha"}

    app = FastAPI()
    mount_inspection(app, service, authorize=authorize, enabled=True)
    with TestClient(app) as client:
        yield client, service, roots, handles, calls


def test_http_authorization_every_read_no_cross_agent_or_mutations(http_service):
    client, service, roots, handles, calls = http_service
    path = "/adkl/api/v1/agents/alpha/inspection"
    assert client.get(path).status_code == 401
    client.headers["authorization"] = "Bearer fixture"
    response = client.get("/adkl/api/v1/agents")
    assert [a["id"] for a in response.json()["agents"]] == ["alpha"]
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    generation = response.json()["snapshot"]["generation"]
    assert client.get(path).json()["snapshot"]["generation"] == generation
    assert client.get(path.replace("alpha", "beta")).status_code == 404
    assert client.get(path + "?refresh=true").status_code == 400
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(path).status_code == 405
    response = client.get(path.replace("inspection", "declarations"))
    body, digest = service.snapshot("alpha")
    assert response.content == body and response.headers["etag"] == f'"{digest}"'
    service.invalidate(handles[0])
    assert client.get(path).status_code == 503
    assert "Approved instructions" not in client.get(path).text
    del client.headers["authorization"]
    assert client.get(path).status_code == 401
    assert len(calls) >= 8


def test_optional_portal_auth_asset_allowlist_and_disabled_no_io(tmp_path):
    app = FastAPI()
    service = registry()
    assert not mount_inspection(app, service, authorize=None, portal_directory="missing")
    assert all(not getattr(route, "path", "").startswith("/adkl") for route in app.routes)
    for name in ("index.html", "main.dart.js", "flutter_bootstrap.js", "font.otf", "effect.frag", "private.py", "flutter_service_worker.js"):
        (tmp_path / name).write_text("fixture", encoding="utf-8")

    def authorize(request: Request):
        return {"alpha"} if request.headers.get("authorization") == "fixture" else set()

    mount_inspection(app, service, authorize=authorize, enabled=True, portal_directory=tmp_path)
    with TestClient(app) as client:
        for path in ("/adkl", "/adkl/", "/adkl/main.dart.js"):
            assert client.get(path).status_code == 403
        client.headers["authorization"] = "fixture"
        assert client.get("/adkl", follow_redirects=False).headers["location"] == "adkl/"
        assert client.get("/adkl/").status_code == 200
        assert client.get("/adkl/main.dart.js").headers["cache-control"] == "no-store"
        assert client.get("/adkl/font.otf").status_code == 200
        assert client.get("/adkl/effect.frag").status_code == 200
        for path in ("private.py", "flutter_service_worker.js", "missing", "api/chat"):
            assert client.get("/adkl/" + path).status_code == 404
        assert client.post("/adkl/").status_code == 405
    with pytest.raises(ValueError, match="already"):
        mount_inspection(app, service, authorize=authorize, enabled=True)


def test_portal_symlinks_and_missing_build_rejected(tmp_path):
    with pytest.raises(ValueError, match="Incomplete"):
        mount_inspection(FastAPI(), registry(), authorize=lambda: {"host"},
                         enabled=True, portal_directory=tmp_path)
    (tmp_path / "escape.js").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="inventory"):
        mount_inspection(FastAPI(), registry(), authorize=lambda: {"host"},
                         enabled=True, portal_directory=tmp_path)
