"""Check local documentation link/import targets without dependencies or network.

Scope: root/console Markdown, docs Markdown, and repository workflow skills. Supports
inline Markdown links, reference definitions and standalone Claude @imports.
Fenced examples are ignored. Does not validate URL availability or fragments.
"""
from __future__ import annotations

import pathlib
import re
import sys
from urllib.parse import unquote, urlsplit


INLINE = re.compile(r"\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))")
IMPORT = re.compile(r"^@([^\s]+)\s*$")


def targets(text):
    """Yield (line number, target) for the supported documentation syntax."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        for match in INLINE.finditer(line):
            yield number, match[1] or match[2]
        match = REFERENCE.match(line)
        if match:
            yield number, match[1] or match[2]
        match = IMPORT.match(line)
        if match:
            yield number, match[1]


def check(root):
    root = root.resolve()
    files = sorted(set(root.glob("*.md")) | set((root / "console").glob("*.md"))
                   | set((root / "docs").rglob("*.md"))
                   | set((root / ".agents" / "skills").rglob("*.md"))
                   | {p for p in (root / "projects").glob("README.md")}
                   | {root / "projects" / name / "README.md" for name in ("native-agent", "staged-ui")
                      if (root / "projects" / name / "README.md").exists()})
    errors = []
    for source in files:
        for line, target in targets(source.read_text(encoding="utf-8")):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            path = pathlib.Path(unquote(url.path))
            resolved = (source.parent / path).resolve()
            if path.is_absolute() or not resolved.is_relative_to(root):
                reason = "target must stay relative to this repository"
            elif not resolved.exists():
                reason = "missing target"
            else:
                continue
            errors.append(f"{source.relative_to(root)}:{line}: {reason}: {target}")
    return files, errors


def main():
    root = pathlib.Path(__file__).resolve().parents[2]
    files, errors = check(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"PASS: local link/import targets in {len(files)} documentation files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
