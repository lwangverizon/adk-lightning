"""Credential-free smoke: native registration and authenticated embedded HTTP.

Run: uv run --no-sync python -m examples.embedded_inspection
No server, model, tool, session or external network is started by this example.
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent

from adkl.embedded import InspectionRegistry
from adkl.inspection_fastapi import mount_inspection


def main():
    app = FastAPI()
    root = LlmAgent(name="help", model="not-called", instruction="A declared help agent.")
    inspection = InspectionRegistry(environment="local", release="synthetic-example",
                                    content_approved=True)
    registration = inspection.register("help", root, name="Help")

    # Synthetic identity for this in-process smoke ONLY. A real application uses
    # its own authenticated session/SSO dependency and derives grants server-side.
    async def authorize(request: Request):
        if request.headers.get("authorization") != "Bearer synthetic-fixture":
            raise HTTPException(401, "Authentication required")
        return {"help"}

    mount_inspection(app, inspection, authorize=authorize, enabled=True)
    with TestClient(app) as client:
        path = "/adkl/api/v1/agents/help/inspection"
        assert client.get(path).status_code == 401
        client.headers["authorization"] = "Bearer synthetic-fixture"
        first = client.get(path).json()["snapshot"]
        assert first["generation"] == registration.generation
        inspection.invalidate(registration)
        assert client.get(path).status_code == 503
        root.instruction = "A revised declared help agent."
        replacement = inspection.register("help", root)
        second = client.get(path).json()["snapshot"]
        assert second["generation"] == replacement.generation != registration.generation
        assert second["agents"][0]["instruction"]["text"] == root.instruction
        assert not inspection.invalidate(registration)
    inspection.clear()
    print("Embedded inspection: authorized reads, replacement and invalidation passed.")


if __name__ == "__main__":
    main()
