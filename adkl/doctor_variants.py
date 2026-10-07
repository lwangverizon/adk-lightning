"""Declared-tool drift across same-named SKILL.md variants. Static; no host import.

Hosts often serve one skill name from several files (environment, channel or
surface variants). A fix applied to one copy and missed in another is invisible
to per-profile checks, because each profile loads only its own variant. This
check compares ``metadata.adk_additional_tools`` across every same-named copy
under the project's skill roots. A differently named copy joins a family through
``metadata.adkl_variant_of: <name>``. Differences can be intentional; findings
are advisory and fingerprinted by their content, so a partial fix appears as a
new finding in baseline comparison. It never decides which copy is correct.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from adkl import execution, skill_lint

FILE_LIMIT = 4000
TOOL_LIMIT = 8


def _roots(root: Path, doc: dict) -> list[Path]:
    """Top-level skill directories named by the descriptor, inside the project."""
    if doc.get("agent", {}).get("factory"):
        mapped = (doc.get("authoring") or {}).get("skills") or {}
        tops = {PurePosixPath(value).parts[0] for value in mapped.values()
                if isinstance(value, str) and len(PurePosixPath(value).parts) > 1}
    else:
        tops = {str((doc.get("skills") or {}).get("dir", "skills"))}
    found = []
    for top in sorted(tops):
        path = (root / top).resolve()
        if path.is_dir() and path.is_relative_to(root) and not (root / top).is_symlink():
            found.append(path)
    return found


def collect(root: Path, doc: dict):
    """Return (raw findings, note). Raises only for unreadable project inputs."""
    root = Path(root).resolve()
    roots = _roots(root, doc)
    if not roots:
        return [], "No project skill directories identified; variants not compared."
    mapped = {value: name for name, value in
              ((doc.get("authoring") or {}).get("skills") or {}).items()
              if isinstance(value, str)}
    families: dict[str, list[tuple[str, frozenset[str]]]] = {}
    scanned = unreadable = 0
    for base in roots:
        for path in sorted(base.rglob("SKILL.md")):
            relative = path.relative_to(root)
            if any(part.startswith(".") or part in execution.SOURCE_EXCLUDED
                   for part in relative.parts) or any(
                       (root / Path(*relative.parts[:depth])).is_symlink()
                       for depth in range(1, len(relative.parts) + 1)):
                continue
            scanned += 1
            if scanned > FILE_LIMIT:
                raise ValueError("Too many SKILL.md files to compare variants")
            try:
                frontmatter, _ = skill_lint.parse_frontmatter(path)
            except Exception:  # Reported by content checks; do not quote it here.
                unreadable += 1
                continue
            metadata = frontmatter.get("metadata") or {}
            name = metadata.get("adkl_variant_of") or frontmatter.get("name")
            declared = metadata.get("adk_additional_tools") or []
            if not isinstance(name, str) or not isinstance(declared, list):
                unreadable += 1
                continue
            families.setdefault(name, []).append(
                (relative.as_posix(), frozenset(str(tool) for tool in declared)))
    findings = []
    compared = 0
    for name, members in sorted(families.items()):
        if len(members) < 2:
            continue
        compared += 1
        if len({tools for _, tools in members}) == 1:
            continue
        everything = frozenset().union(*(tools for _, tools in members))
        shared = frozenset.intersection(*(tools for _, tools in members))
        gaps = []
        # Name the smaller side: the copy still declaring a removed tool, or
        # the copies missing a newly added one.
        for tool in sorted(everything - shared)[:TOOL_LIMIT]:
            holders = [path for path, tools in members if tool in tools]
            others = [path for path, tools in members if tool not in tools]
            gaps.append(f"{tool} only in {', '.join(holders)}" if len(holders) <= len(others)
                        else f"{tool} missing from {', '.join(others)}")
        if len(everything - shared) > TOOL_LIMIT:
            gaps.append(f"{len(everything - shared) - TOOL_LIMIT} more tools differ")
        # Anchor the subject on the mapped (served) copy when there is one.
        source = next((path for path, _ in members if path in mapped), members[0][0])
        findings.append({
            "rule": "SKILL_VARIANT_TOOLS_DIFFER", "severity": "INFO", "skill": name,
            "source": source,
            "message": (f"{len(members)} variants of {name} declare different tools: "
                        + "; ".join(gaps) + ". Confirm each difference is intentional; "
                        "apply behavior fixes to every served variant."),
        })
    note = (f"Compared declared tools across {compared} same-named skill families "
            f"({scanned} SKILL.md files). Bodies and runtime selection are not compared.")
    if unreadable:
        note += f" {unreadable} files lacked a readable name or tool list."
    return findings, note
