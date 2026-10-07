"""Hash the exact console review tree. This is unsigned evidence, not a release."""
import argparse
import hashlib
import json
import pathlib
import subprocess


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    root = pathlib.Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("artifact root must be a real directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
        elif not path.is_dir():
            raise ValueError(f"unsupported artifact entry: {path}")
    if not result or "index.html" not in result:
        raise ValueError("console artifact must contain index.html")
    return result


def create(root, repo):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    return {
        "schemaVersion": 1,
        "kind": "lightning-console-review",
        "productionReady": False,
        "sourceCommit": git("rev-parse", "HEAD"),
        "workingTreeDirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
        "inputSha256": {name: file_hash(repo / name) for name in (
            "uv.lock", "console/pubspec.lock", ".gitlab-ci.yml")},
        "files": inventory(root),
    }


def verify(root, manifest):
    if (manifest.get("schemaVersion") != 1
            or manifest.get("kind") != "lightning-console-review"
            or manifest.get("productionReady") is not False):
        raise ValueError("unsupported manifest or misleading release status")
    if inventory(root) != manifest.get("files"):
        raise ValueError("artifact files differ from the manifest")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("root", type=pathlib.Path)
    parser.add_argument("manifest", type=pathlib.Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            if args.manifest.resolve().is_relative_to(args.root.resolve()):
                raise ValueError("manifest must live outside the hashed tree")
            repo = pathlib.Path(__file__).resolve().parents[2]
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(create(args.root, repo), indent=2) + "\n",
                                     encoding="utf-8")
        else:
            verify(args.root, json.loads(args.manifest.read_text(encoding="utf-8")))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"artifact check failed: {exc}\n")


if __name__ == "__main__":
    main()
