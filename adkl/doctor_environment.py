"""Project interpreter versus ``uv.lock``. Static: reads distribution metadata only.

``--inspect``, console chat and offline inspection exports all run in the
profile's interpreter, not in the frozen image. A developer environment that
carries packages outside the lock, other versions, or a stale vendored wheel can
render different tool declarations from production. (A host's published
inspection rendered 181 of 182 tool schemas differently for exactly this reason:
45 installed packages were outside its lock.) This check compares what is
installed against the lock without importing or running the interpreter.

``ENVIRONMENT_NOT_LOCKED`` (WARN) reports installed distributions absent from
the lock, versions the lock does not list, and path-locked packages installed
from a different file (a byte-identical copy at another path is the same pin). Packages locked but not installed are not reported: a
lock spans groups, extras and platforms the profile need not install. Local
project sources (editable, virtual, directory) are skipped.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import tomllib
from urllib.parse import unquote, urlparse

from adkl import execution

NAME_LIMIT = 6
_VERSION = re.compile(r"^\s*(?:version_info|version)\s*=\s*(\d+)\.(\d+)", re.M)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _site_packages(python: Path) -> Path | None:
    """The venv site-packages for ``python`` (unresolved path), or None."""
    venv = python.parent.parent
    config = venv / "pyvenv.cfg"
    if not config.is_file():
        return None
    match = _VERSION.search(config.read_text(encoding="utf-8", errors="replace"))
    candidates = [venv / "Lib" / "site-packages", *sorted((venv / "lib").glob("python*/site-packages"))]
    existing = [path for path in candidates if path.is_dir()]
    if match:
        preferred = [path for path in existing
                     if path.parent.name == f"python{match.group(1)}.{match.group(2)}"]
        if preferred:
            return preferred[0]
    return existing[0] if len(existing) == 1 else None


def _locked(root: Path):
    """({name: versions}, {name: resolved wheel paths}, local project names)."""
    data = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8", errors="replace"))
    versions: dict[str, set[str]] = {}
    paths: dict[str, set[Path]] = {}
    local: set[str] = set()
    for package in data.get("package", []):
        name = package.get("name")
        if not isinstance(name, str):
            continue
        name = _normalize(name)
        source = package.get("source") or {}
        if any(key in source for key in ("editable", "virtual", "directory")):
            local.add(name)
            continue
        if isinstance(package.get("version"), str):
            versions.setdefault(name, set()).add(package["version"].lower())
        if isinstance(source.get("path"), str):
            paths.setdefault(name, set()).add((root / source["path"]).resolve())
    return versions, paths, local


def _same_file(installed: Path | None, locked: set[Path]) -> bool:
    """Same path, or byte-identical (a venv shared by two checkouts of one pin)."""
    if installed is None:
        return False
    if installed in locked:
        return True
    if not installed.is_file():
        return False
    digest = _sha256(installed)
    return any(path.is_file() and _sha256(path) == digest for path in locked)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _display(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else f"{path.name} (outside the project)"


def _listed(items: list[str]) -> str:
    more = f" and {len(items) - NAME_LIMIT} more" if len(items) > NAME_LIMIT else ""
    return ", ".join(items[:NAME_LIMIT]) + more


def collect(root: Path, profile: str | None = None):
    """Return (raw findings, note). Unreadable inputs skip the comparison."""
    root = Path(root).resolve()
    if not (root / "uv.lock").is_file() or not (root / "execution.yaml").is_file():
        return [], "Installed environment not compared: uv.lock or execution.yaml is missing."
    try:
        name, entry = execution.profile(root, profile)
        if entry.get("target") != "local":
            return [], "Installed environment not compared: the profile is not a local interpreter."
        site = _site_packages(root / entry.get("python", ".venv/bin/python"))
        locked = _locked(root) if site else None
    except (OSError, ValueError, tomllib.TOMLDecodeError, execution.ExecutionError):
        return [], "Installed environment not compared: profile or uv.lock could not be read."
    if site is None or locked is None:
        return [], "Installed environment not compared: the profile interpreter is not a readable virtual environment."
    versions, paths, local = locked
    extra, changed, moved = [], [], []
    installed = 0
    for dist in importlib.metadata.distributions(path=[str(site)]):
        raw_name = dist.metadata.get("Name")
        if not raw_name:
            continue
        installed += 1
        package, version = _normalize(raw_name), str(dist.version)
        if package in local:
            continue
        if package not in versions and package not in paths:
            extra.append(f"{package} {version}")
            continue
        if versions.get(package) and version.lower() not in versions[package]:
            changed.append(f"{package} {version} (locked {', '.join(sorted(versions[package]))})")
        if package in paths:
            try:
                url = json.loads(dist.read_text("direct_url.json") or "{}").get("url", "")
            except (ValueError, AttributeError):
                url = ""
            source = Path(unquote(urlparse(url).path)).resolve() if url.startswith("file:") else None
            if not _same_file(source, paths[package]):
                locked_paths = " or ".join(sorted(_display(root, path) for path in paths[package]))
                origin = _display(root, source) if source else "an unrecorded source"
                moved.append(f"{package} from {origin} (locked {locked_paths})")
    note = (f"Compared {installed} installed distributions in the {name} interpreter "
            "with uv.lock; metadata only, nothing imported.")
    if not (extra or changed or moved):
        return [], note
    parts = []
    if extra:
        parts.append(f"{len(extra)} installed distributions are not in uv.lock ({_listed(sorted(extra))})")
    if changed:
        parts.append(f"{len(changed)} differ from the locked version ({_listed(sorted(changed))})")
    if moved:
        parts.append(f"path-locked packages were installed from another file ({_listed(sorted(moved))})")
    return [{"rule": "ENVIRONMENT_NOT_LOCKED", "severity": "WARN", "source": "uv.lock",
             "skill": None,
             "message": (f"The {name} profile interpreter differs from uv.lock: "
                         + "; ".join(parts) + ". --inspect, console chat and inspection "
                         "exports run in this interpreter, so their tool declarations and "
                         "behavior can differ from a frozen image build.")}], note
