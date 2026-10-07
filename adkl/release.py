"""Build and verify an explicit agent bundle for customer-owned native ADK serving."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import zipfile

import yaml

from adkl import audit, capabilities, features
from adkl.version import sdk_version, wheel_name

IMAGE = "ghcr.io/astral-sh/uv:0.10.10-python3.13-trixie-slim@sha256:0eee2be1fc47d7ee2af13b6feee653e8a29c043191f63c9c6d0c1453547a7131"
FORBIDDEN = {".env", ".git", ".venv", ".adkl", "__pycache__", "node_modules", "releases"}


def validate_wheel(path):
    path = pathlib.Path(path)
    if path.name != wheel_name():
        raise ValueError(f"Use {wheel_name()} built for this SDK version.")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if "adkl/agent_runtime.py" not in names or any(
            not (n.startswith("adkl/") or n.startswith(f"adk_lightning-{sdk_version()}.dist-info/"))
            or ".." in pathlib.PurePosixPath(n).parts for n in names
        ):
            raise ValueError("Wheel must contain only ADKL and its distribution metadata.")


def _safe_name(name):
    p = pathlib.PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name:
        raise ValueError("Release paths must be relative and remain inside the project.")
    if any(part in FORBIDDEN or part.startswith(".env") or part.lower().endswith(
        (".pem", ".key", ".p12", ".pfx", ".pyc")) for part in p.parts):
        raise ValueError(f"Private/runtime file is forbidden in releases: {name}")
    return p


def collect(root):
    """Only explicit release inputs; never walk the whole project implicitly."""
    root = pathlib.Path(root).resolve()
    declaration = root / "release.yaml"
    if declaration.is_symlink():
        raise ValueError("Release declarations must not be symlinks.")
    doc = yaml.safe_load(declaration.read_text())
    if (not isinstance(doc, dict) or doc.get("version") != 1
            or set(doc) != {"version", "files"} or not isinstance(doc.get("files"), list)
            or not doc["files"]):
        raise ValueError("release.yaml requires version: 1 and an explicit files list.")
    result = {}

    def visit(path):
        rel = path.relative_to(root).as_posix()
        _safe_name(rel)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Release symlinks are forbidden: {rel}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.is_file():
            if path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError(f"Release input exceeds 100 MiB: {rel}")
            result[rel] = path.read_bytes()
        else:
            raise ValueError(f"Missing release input: {rel}")

    for name in doc["files"]:
        if not isinstance(name, str):
            raise ValueError("Release files must be relative path strings.")
        p = _safe_name(name)
        # Check every ancestor, including symlinks pointing back into the root.
        if any((root / pathlib.Path(*p.parts[:i])).is_symlink() for i in range(1, len(p.parts) + 1)):
            raise ValueError(f"Release symlinks are forbidden: {name}")
        visit(root / name)
    if sum(map(len, result.values())) > 250 * 1024 * 1024:
        raise ValueError("Release exceeds 250 MiB.")
    return result


def build(root, output):
    root, output = pathlib.Path(root).resolve(), pathlib.Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Release output already exists; choose a new revision filename.")
    payload = collect(root)
    doc = yaml.safe_load(payload.get("agent.yaml", b""))
    if isinstance(doc, dict) and doc.get("agent", {}).get("factory"):
        raise ValueError("Host inspection descriptors use the existing host deployment, not an ADKL bundle.")
    required = {"agent.yaml", "pyproject.toml", "uv.lock", "agent.py", "__init__.py"}
    if required - payload.keys():
        raise ValueError(f"Release is missing {sorted(required - payload.keys())}")
    wheels = [n for n in payload if n.startswith("vendor/") and n.endswith(".whl")]
    if len(wheels) != 1:
        raise ValueError("Release requires exactly one vendored ADKL wheel.")
    validate_wheel(root / wheels[0])
    # Validate the packaged tree, not a larger working tree with omitted resources.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="adkl-release-check-") as directory:
        stage = pathlib.Path(directory)
        for name, data in payload.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        report = audit.audit(stage)
        if not report["passed"]:
            raise ValueError("Packaged agent fails audit: " + "; ".join(
                f["message"] for f in report["findings"] if f["severity"] == "ERROR"))
        uv = shutil.which("uv")
        if not uv:
            raise ValueError("Install uv to verify the release lock.")
        check = subprocess.run([uv, "lock", "--check", "--offline", "--project", str(stage)],
                               capture_output=True, text=True)
        if check.returncode:
            raise ValueError("Packaged lock does not match its inputs; run project preparation first.")
    doc = yaml.safe_load(payload["agent.yaml"])
    name = doc["agent"]["name"]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ValueError("Agent name is not a safe deployment directory name.")
    docker = (f"FROM {IMAGE}\nWORKDIR /opt/agents/{name}\n"
        "COPY project/ ./\nRUN uv sync --frozen --no-dev --no-editable\n"
        "RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /opt/agents\n"
        "USER agent\nEXPOSE 8080\n"
        f'CMD ["/opt/agents/{name}/.venv/bin/adk", "api_server", "/opt/agents", "--host", "0.0.0.0", "--port", "8080"]\n')
    files = {"project/" + k: v for k, v in payload.items()}
    files["Dockerfile"] = docker.encode()
    files[".dockerignore"] = b"**/.env*\n**/.venv\n**/.git\n**/.adkl\n"
    files["DEPLOY.md"] = (
        "# Customer-owned deployment\n\nVerify: python -m adkl.release verify release.zip\n"
        "Extract in a new directory, then: docker build -t your-agent:revision .\n"
        "Supply GOOGLE_API_KEY (or the project's declared provider secrets) at runtime.\n"
        "Native ADK API server listens on 8080. Place it behind your authenticated gateway.\n"
        "The image does not implement end-user authorization or durable learner storage.\n"
        "Configure host session storage, tool permissions, resource limits and evaluation before production traffic.\n"
        "Smoke-test staging, retain this bundle/image digest, then promote that same image.\n"
        "Rollback by restoring the prior image and compatible session store.\n"
    ).encode()
    manifest = {"version": 1, "agent": name, "target": "native-adk-customer-host",
                "productionReady": False, "behavioralEvaluation": "not_run",
                "runtime": capabilities.manifest(features.resolve(doc.get("features")), "sdk"),
                "files": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}}
    manifest["sourceDigest"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
    files["manifest.json"] = json.dumps(manifest, indent=2).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream, zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    verify(output)
    return manifest


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if sum(info.file_size for info in archive.infolist()) > 250 * 1024 * 1024:
            raise ValueError("Release exceeds 250 MiB.")
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Invalid release inventory.")
        for info in archive.infolist():
            _safe_name(info.filename)
            if info.file_size > 100 * 1024 * 1024 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive member.")
        manifest = json.loads(archive.read("manifest.json"))
        expected = manifest.get("files", {})
        if manifest.get("version") != 1 or set(names) != set(expected) | {"manifest.json"}:
            raise ValueError("Release inventory mismatch.")
        for name, digest in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"Release checksum mismatch: {name}")
        if hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest() != manifest.get("sourceDigest"):
            raise ValueError("Release source digest mismatch.")
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("root", type=pathlib.Path)
    make.add_argument("--output", required=True, type=pathlib.Path)
    check = commands.add_parser("verify")
    check.add_argument("path", type=pathlib.Path)
    args = parser.parse_args()
    try:
        result = build(args.root, args.output) if args.command == "build" else verify(args.path)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
