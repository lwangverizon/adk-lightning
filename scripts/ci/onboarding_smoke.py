"""Verify wheel -> independent project -> checked bundle -> native ADK turn."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from adkl import projects, release


def main():
    wheel = pathlib.Path(sys.argv[1]).resolve()
    release.validate_wheel(wheel)
    with tempfile.TemporaryDirectory(prefix="adkl-package-smoke-") as directory:
        work = pathlib.Path(directory)
        root = projects.create(work, "bundle_agent", "Help organize user-supplied meeting notes.")
        projects.prepare(root, wheel)
        archive = work / "release.zip"
        manifest = release.build(root, archive)
        assert manifest == release.verify(archive)
        with zipfile.ZipFile(archive) as package:
            package.extractall(work / "unpacked")
        unpacked = work / "unpacked/project"
        subprocess.run(["uv", "sync", "--locked", "--project", str(unpacked)], check=True)
        # -I and a separate cwd prohibit accidental imports from the ADKL checkout.
        code = '''
import asyncio, pathlib, sys
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from adkl.agent_runtime import build_agent
import adkl
assert str(pathlib.Path(adkl.__file__)).startswith(str(pathlib.Path(sys.executable).parent.parent))
class Model(BaseLlm):
    model: str = "synthetic"
    async def generate_content_async(self, request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Bundle executed.")]))
async def test():
    agent = build_agent(pathlib.Path.cwd(), env={}, model=Model())
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name="bundle_agent", session_service=sessions)
    session = await sessions.create_session(app_name="bundle_agent", user_id="synthetic")
    replies = []
    async for event in runner.run_async(user_id="synthetic", session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="Organize my notes.")])):
        replies.extend(p.text for p in (event.content.parts if event.content else []) if p.text)
    assert "Bundle executed." in replies, replies
asyncio.run(test())
'''
        subprocess.run([str(unpacked / ".venv/bin/python"), "-I", "-c", code], cwd=unpacked, check=True)
        # Verify the exact native server command shipped in the Dockerfile is installed.
        subprocess.run([str(unpacked / ".venv/bin/adk"), "api_server", "--help"],
                       stdout=subprocess.DEVNULL, check=True)
        external = work / "external_agent"
        shutil.copytree(pathlib.Path(__file__).resolve().parents[2] / "projects/native-agent",
                        external, ignore=shutil.ignore_patterns(
                            ".venv", ".adkl", "vendor", "__pycache__", "uv.lock"))
        projects.prepare(external, wheel)
        subprocess.run([str(external / ".venv/bin/python"), "-I", str(external / "verify.py")],
                       cwd=external, check=True)
        print("PASS: isolated wheel, locked project, verified bundle, and native ADK turn.")


if __name__ == "__main__":
    main()
