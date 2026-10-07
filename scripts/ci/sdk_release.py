"""Prepare, verify and publish tested SDK artifacts; never build in the publisher."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version(root=ROOT):
    return tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]


def tag_version(tag):
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-rc\.[1-9]\d*)?", tag):
        raise ValueError("Release tag must be vX.Y.Z or vX.Y.Z-rc.N")
    return tag[1:].replace("-rc.", "rc")


def check(root=ROOT, tag=""):
    current = version(root)
    if not re.fullmatch(r"\d+\.\d+\.\d+(rc[1-9]\d*)?", current):
        raise ValueError("SDK version must be a stable or rc PEP 440 version")
    if tag and tag_version(tag) != current:
        raise ValueError("Release tag and pyproject version differ")
    notes = root / "docs/releases" / f"{current}.md"
    if not notes.is_file() or len(notes.read_text().strip()) < 80:
        raise ValueError("Add reviewed release notes for the current SDK version")
    return current


def artifacts(directory, current):
    wheel = directory / f"adk_lightning-{current}-py3-none-any.whl"
    source = directory / f"adk_lightning-{current}.tar.gz"
    console = directory / f"adkl_console-{current}.zip"
    if set(directory.glob("*.whl")) != {wheel} or set(directory.glob("*.tar.gz")) != {source}:
        raise ValueError("Exactly the current wheel and sdist must be present")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        prefix = f"adk_lightning-{current}.dist-info/"
        if len(names) != len(set(names)) or any(
            not n.startswith(("adkl/", prefix)) or ".." in Path(n).parts for n in names
        ) or "adkl/agent_runtime.py" not in names or any(n.startswith("adkl/web/") for n in names):
            raise ValueError("Release wheel must contain the slim SDK without console assets")
        metadata = BytesParser().parsebytes(archive.read(prefix + "METADATA"))
        if metadata["Name"] != "adk-lightning" or metadata["Version"] != current:
            raise ValueError("Wheel metadata differs from release version")
    with tarfile.open(source) as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if (not parts or parts[0] != f"adk_lightning-{current}" or ".." in parts
                    or not (member.isfile() or member.isdir())
                    or any(p.startswith(".env") or p in {"projects", ".git", ".venv"} for p in parts)
                    or parts[1:3] == ("adkl", "web")):
                raise ValueError("Unsafe or private source distribution member")
    if set(directory.glob("*.zip")) != {console}:
        raise ValueError("Exactly the current console archive must be present")
    with zipfile.ZipFile(console) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "web/index.html" not in names:
            raise ValueError("Invalid console archive inventory")
        for member in archive.infolist():
            path = Path(member.filename)
            if (not member.filename.startswith("web/") or ".." in path.parts
                    or "\\" in member.filename or path.is_absolute()
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe console archive member")
    return wheel, source, console


def prepare(directory, root=ROOT, env=os.environ):
    current = check(root, env.get("CI_COMMIT_TAG", ""))
    wheel, source, console = artifacts(directory, current)
    output = directory / "release"
    output.mkdir(exist_ok=True)
    for artifact in (wheel, source, console):
        shutil.copyfile(artifact, output / artifact.name)
    directory = output
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    commit = git("rev-parse", "HEAD")
    if env.get("CI_COMMIT_SHA") and env["CI_COMMIT_SHA"] != commit:
        raise ValueError("Checkout differs from pipeline revision")
    # Keep release evidence with the artifact instead of expiring job reports.
    evidence = ["runtime-sbom.cdx.json", "dependency-audit.json"]
    for name in evidence:
        shutil.copyfile(root / "reports" / name, directory / name)
    shutil.copyfile(root / "docs/releases" / f"{current}.md", directory / "RELEASE_NOTES.md")
    names = [wheel.name, source.name, console.name, *evidence, "RELEASE_NOTES.md"]
    manifest = {"schemaVersion": 1, "kind": "adkl-sdk-release", "version": current,
        "tag": env.get("CI_COMMIT_TAG", ""), "sourceCommit": commit,
        "pipeline": env.get("CI_PIPELINE_URL", "local"),
        "workingTreeDirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        "productionHostingReady": False, "provenance": "unsigned-ci-evidence",
        "inputs": {n: digest((root / n).read_bytes()) for n in
                   ("pyproject.toml", "uv.lock", "console/pubspec.lock", ".gitlab-ci.yml")},
        "files": {n: digest((directory / n).read_bytes()) for n in sorted(names)}}
    (directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify(directory)
    return manifest


def verify(directory):
    manifest = json.loads((directory / "release-manifest.json").read_text())
    if manifest.get("schemaVersion") != 1 or manifest.get("kind") != "adkl-sdk-release":
        raise ValueError("Unsupported release manifest")
    expected = manifest["files"]
    if set(p.name for p in directory.iterdir()) != set(expected) | {"release-manifest.json"}:
        raise ValueError("Release inventory mismatch")
    for name, checksum in expected.items():
        if Path(name).name != name or (directory / name).is_symlink():
            raise ValueError("Unsafe release path")
        if digest((directory / name).read_bytes()) != checksum:
            raise ValueError("Release checksum mismatch: " + name)
    artifacts(directory, manifest["version"])
    return manifest


class SafeDownloadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() != "GET" or not newurl.startswith("https://"):
            raise ValueError("Only HTTPS download redirects are allowed")
        # GitLab may redirect downloads to signed object-storage URLs. Never
        # forward JOB-TOKEN to that destination, including same-host redirects.
        return urllib.request.Request(newurl, method="GET")


class GitLab:
    def __init__(self, env):
        base = env["CI_API_V4_URL"]
        if not base.startswith("https://") or not env["CI_PROJECT_ID"].isdigit():
            raise ValueError("Invalid GitLab destination")
        self.base = base + "/projects/" + env["CI_PROJECT_ID"]
        self.token = env["CI_JOB_TOKEN"]
        self.opener = urllib.request.build_opener(SafeDownloadRedirect)

    def request(self, method, path, data=None, json_body=False):
        headers = {"JOB-TOKEN": self.token}
        if json_body:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            # Never print response bodies or auth headers.
            raise ValueError(f"GitLab {method} failed with HTTP {exc.code}") from None


def publish(directory, env=os.environ, client=None):
    if env.get("CI_COMMIT_REF_PROTECTED") != "true" or env.get("CI_PIPELINE_SOURCE") != "push":
        raise ValueError("Publishing requires a protected tag push pipeline")
    tag = env.get("CI_COMMIT_TAG", "")
    current = tag_version(tag)
    manifest = verify(directory)
    if (manifest["version"] != current or manifest["tag"] != tag
            or manifest["sourceCommit"] != env["CI_COMMIT_SHA"]
            or manifest["pipeline"] != env["CI_PIPELINE_URL"] or manifest["workingTreeDirty"]):
        raise ValueError("Artifact is not the clean candidate from this tag pipeline")
    client = client or GitLab(env)
    base = f"/packages/generic/adkl-sdk/{current}/"
    names = [*sorted(manifest["files"]), "release-manifest.json"]
    # Retry partial publication only when every existing byte agrees. Never overwrite.
    missing = []
    for name in names:
        data = (directory / name).read_bytes()
        existing = client.request("GET", base + name)
        if existing is not None and existing != data:
            raise ValueError("Published version already contains different bytes: " + name)
        if existing is None:
            missing.append(name)
    for name in names:
        data = (directory / name).read_bytes()
        if name in missing:
            client.request("PUT", base + name, data)
        if client.request("GET", base + name) != data:
            raise ValueError("Registry read-back mismatch: " + name)
    links = [{"name": n, "url": client.base + base + n, "link_type": "package"} for n in names]
    body = {"tag_name": tag, "name": "ADKL " + current,
            "description": (directory / "RELEASE_NOTES.md").read_text() +
            "\n\nVerified pipeline: " + manifest["pipeline"], "assets": {"links": links}}
    existing = client.request("GET", "/releases/" + tag)
    if existing is None:
        client.request("POST", "/releases", json.dumps(body).encode(), json_body=True)
    else:
        prior = json.loads(existing)
        prior_links = {(x["name"], x["url"]) for x in prior["assets"]["links"]}
        if (prior["commit"]["id"] != manifest["sourceCommit"] or prior["description"] != body["description"]
                or prior_links != {(x["name"], x["url"]) for x in links}):
            raise ValueError("Existing release differs; refusing to rewrite release history")
    print("Published and verified " + tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "prepare", "verify", "publish"])
    parser.add_argument("directory", nargs="?", type=Path)
    args = parser.parse_args()
    directory = args.directory or ROOT / "dist"
    if args.directory is None and args.action in {"verify", "publish"}:
        directory /= "release"
    try:
        if args.action == "check":
            check(tag=os.environ.get("CI_COMMIT_TAG", ""))
        elif args.action == "prepare":
            prepare(directory)
        elif args.action == "verify":
            verify(directory)
        else:
            publish(directory)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"Release check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
