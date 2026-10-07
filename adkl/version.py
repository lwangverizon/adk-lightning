"""Resolve the SDK version from source metadata or the installed distribution."""
from importlib.metadata import version
from pathlib import Path
import tomllib


def sdk_version():
    metadata = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if metadata.is_file():
        project = tomllib.loads(metadata.read_text()).get("project", {})
        if project.get("name") == "adk-lightning":
            return project["version"]
    return version("adk-lightning")


def wheel_name():
    return f"adk_lightning-{sdk_version()}-py3-none-any.whl"
