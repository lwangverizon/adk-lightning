"""Stable portal source identity (not an artifact attestation)."""
from pathlib import Path
import hashlib


def build_identity(root: Path) -> str:
    paths = [root / "console/pubspec.yaml", root / "console/pubspec.lock",
             root / "scripts/build_portal.sh", root / "scripts/portal_build_identity.py"]
    for directory in ("console/lib", "console/web", "deploy/portal"):
        paths.extend(path for path in (root / directory).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


if __name__ == "__main__":
    print(build_identity(Path(__file__).resolve().parents[1]))
