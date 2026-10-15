"""Same-named SKILL.md variants and cross-skill references. Static; no host import.

Hosts often serve one skill name from several files (environment, channel or
surface variants). A fix applied to one copy and missed in another is invisible
to per-profile checks, because each profile loads only its own variant. This
module compares every same-named copy under the project's skill roots. A
differently named copy joins a family through ``metadata.adkl_variant_of: <name>``.

- ``SKILL_VARIANT_TOOLS_DIFFER`` compares ``metadata.adk_additional_tools``.
- ``SKILL_VARIANT_PARTIAL_EDIT`` (only with an explicit Git base) reports a
  Markdown section that was byte-identical (whitespace-normalized) in several
  copies at the base, and has since changed in some copies but not others.
  This is the "fixed base and UAT, missed MVA" defect. It is diff-relative: it
  disappears once every copy matches or the base advances past the edit.
- ``SKILL_REFERENCE_UNKNOWN`` reports an explicit redirect such as
  ``(use other-skill)`` or ``route to `other-skill``` naming no skill in the
  scanned roots. A model told to load a nonexistent skill improvises.
- ``DESCRIPTION_TOO_LONG`` (WARN here) reports a copy that no per-profile
  content check covers whose description ADK's loader rejects. The served copy
  is an ERROR from the content checks; this catches the "MVA twin is 1027
  characters" case before some other surface or environment loads it.

Differences can be intentional; findings are advisory and fingerprinted by their
content, so a partial fix appears as a new finding in baseline comparison. It
never decides which copy is correct.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess

from adkl import execution, skill_lint

FILE_LIMIT = 4000
TOOL_LIMIT = 8
SECTION_LIMIT = 4
# Git revisions, branch names and simple ancestry suffixes; never an option.
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./~^@{}-]{0,199}")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SKILL_NAME = r"[a-z0-9]+(?:-[a-z0-9]+)+"
# Explicit delegation only. Hyphenated lowercase names: tools use underscores.
_REFERENCES = (
    re.compile(rf"\((?:use|see|route to|handled by|owned by|belongs to)\s+`?({_SKILL_NAME})`?(?:\s+skill)?\)", re.I),
    re.compile(rf"\b(?:use|load|route(?: it| them)? to|hand off to|defer to|switch to)\s+"
               rf"(?:the\s+)?`({_SKILL_NAME})`(?:\s+skill)?", re.I),
)


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


def _scan(root: Path, doc: dict):
    """Return (families, scanned, unreadable, roots, oversized).

    Families map name -> members; each member is ``(relative posix path,
    declared tools, body)``. ``oversized`` lists ``(path, name, message)`` for
    copies whose description ADK's loader would reject.
    """
    roots = _roots(root, doc)
    families: dict[str, list[tuple[str, frozenset[str], str]]] = {}
    oversized: list[tuple[str, str, str]] = []
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
                frontmatter, body = skill_lint.parse_frontmatter(path)
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
                (relative.as_posix(), frozenset(str(tool) for tool in declared), body))
            too_long = skill_lint.description_too_long(frontmatter.get("description"))
            if too_long is not None:
                oversized.append((relative.as_posix(), name, too_long))
    return families, scanned, unreadable, roots, oversized


def _content_checked(doc: dict) -> set[str]:
    """SKILL.md paths that the static or mapped-content check already lints."""
    if doc.get("agent", {}).get("factory"):
        mapped = (doc.get("authoring") or {}).get("skills") or {}
        return {PurePosixPath(value).as_posix() for value in mapped.values()
                if isinstance(value, str)}
    config = doc.get("skills") or {}
    base = PurePosixPath(str(config.get("dir", "skills")))
    return {(base / str(name) / "SKILL.md").as_posix()
            for name in config.get("include") or [] if isinstance(name, str)}


def sections(body: str) -> dict[str, str]:
    """Whitespace-normalized Markdown sections keyed by heading (fences ignored).

    Text before the first heading is ``(preamble)``. A repeated heading gets an
    occurrence suffix so two "Gotchas" sections never merge.
    """
    out: dict[str, str] = {}
    seen: dict[str, int] = {}
    key, lines, fence = "(preamble)", [], None
    for line in body.splitlines():
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        heading = None if fence or marker else _HEADING.match(line)
        if heading:
            out[key] = " ".join(" ".join(lines).split())
            title = " ".join(heading.group(2).split())
            seen[title] = seen.get(title, 0) + 1
            key = title if seen[title] == 1 else f"{title} ({seen[title]})"
            lines = []
        else:
            lines.append(line)
    out[key] = " ".join(" ".join(lines).split())
    return out


def _git_body(root: Path, ref: str, relative: str) -> str | None:
    """Body of ``relative`` at ``ref``, or None when absent there."""
    result = subprocess.run(  # noqa: S603, S607 -- fixed git command, validated ref
        ["git", "-C", str(root), "show", f"{ref}:./{relative}"],
        capture_output=True, timeout=10, check=False)
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="replace")
    match = re.match(r"\A---\s*\n.*?\n---\s*\n?(.*)\Z", text, re.S)
    return match.group(1) if match else text


def resolve_base(root: Path, ref: str) -> str:
    """Validate ``ref`` and return its commit; raise ValueError otherwise."""
    if not isinstance(ref, str) or not _REF.fullmatch(ref) or ".." in ref:
        raise ValueError("Base ref must be a Git revision name, not an option or range")
    try:
        result = subprocess.run(  # noqa: S603, S607 -- fixed git command
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Git is unavailable for the base ref") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("Base ref does not name a commit in this repository")
    return result.stdout.strip()


def _partial_edits(root: Path, name: str, members, ref: str, commit: str) -> list[dict]:
    """Sections identical across copies at ``commit`` but now changed in only some."""
    before = {}
    for path, _tools, _body in members:
        body = _git_body(root, commit, path)
        if body is not None:  # A copy added since the base has nothing to drift from.
            before[path] = sections(body)
    if len(before) < 2:
        return []
    now = {path: sections(body) for path, _tools, body in members if path in before}
    findings = []
    headings = sorted(set().union(*(set(value) for value in before.values())))
    for heading in headings:
        groups: dict[str, list[str]] = {}
        for path, value in before.items():
            if heading in value and value[heading]:
                groups.setdefault(value[heading], []).append(path)
        for text, paths in groups.items():
            if len(paths) < 2:
                continue
            changed = [path for path in paths if now[path].get(heading) != text]
            unchanged = [path for path in paths if path not in changed]
            if not changed or not unchanged:
                continue
            findings.append({
                "rule": "SKILL_VARIANT_PARTIAL_EDIT", "severity": "WARN", "skill": name,
                "source": unchanged[0],
                "message": (f'Section "{heading}" was identical in {len(paths)} variants at '
                            f"{ref}, but changed only in {', '.join(changed)}; still "
                            f"unchanged in {', '.join(unchanged)}. Apply the same fix "
                            "to every served copy, or confirm the divergence is intended."),
            })
            if len(findings) >= SECTION_LIMIT:
                return findings
    return findings


def collect(root: Path, doc: dict, base_ref: str | None = None):
    """Return (raw findings, note). Raises only for unreadable project inputs."""
    root = Path(root).resolve()
    commit = resolve_base(root, base_ref) if base_ref else None
    families, scanned, unreadable, roots, oversized = _scan(root, doc)
    if not roots:
        return [], "No project skill directories identified; variants not compared."
    mapped = {value: name for name, value in
              ((doc.get("authoring") or {}).get("skills") or {}).items()
              if isinstance(value, str)}
    covered = _content_checked(doc)
    # Served copies are ERRORs from the content checks; repeat only the others.
    findings = [{"rule": "DESCRIPTION_TOO_LONG", "severity": "WARN", "skill": name,
                 "source": path,
                 "message": (f"{message}. This copy is not one the selected profile's "
                             "content checks load; any surface or environment that "
                             "serves it fails at skill load.")}
                for path, name, message in oversized if path not in covered]
    compared = 0
    for name, members in sorted(families.items()):
        if len(members) < 2:
            continue
        compared += 1
        if commit:
            findings.extend(_partial_edits(root, name, members, base_ref, commit))
        if len({tools for _, tools, _ in members}) == 1:
            continue
        everything = frozenset().union(*(tools for _, tools, _ in members))
        shared = frozenset.intersection(*(tools for _, tools, _ in members))
        gaps = []
        # Name the smaller side: the copy still declaring a removed tool, or
        # the copies missing a newly added one.
        for tool in sorted(everything - shared)[:TOOL_LIMIT]:
            holders = [path for path, tools, _ in members if tool in tools]
            others = [path for path, tools, _ in members if tool not in tools]
            gaps.append(f"{tool} only in {', '.join(holders)}" if len(holders) <= len(others)
                        else f"{tool} missing from {', '.join(others)}")
        if len(everything - shared) > TOOL_LIMIT:
            gaps.append(f"{len(everything - shared) - TOOL_LIMIT} more tools differ")
        # Anchor the subject on the mapped (served) copy when there is one.
        source = next((path for path, _, _ in members if path in mapped), members[0][0])
        findings.append({
            "rule": "SKILL_VARIANT_TOOLS_DIFFER", "severity": "INFO", "skill": name,
            "source": source,
            "message": (f"{len(members)} variants of {name} declare different tools: "
                        + "; ".join(gaps) + ". Confirm each difference is intentional; "
                        "apply behavior fixes to every served variant."),
        })
    note = (f"Compared declared tools across {compared} same-named skill families "
            f"({scanned} SKILL.md files). ")
    note += (f"Section bodies compared against {base_ref} ({commit[:12]})."
             if commit else "Bodies compared only with --base-ref; runtime selection is not compared.")
    if unreadable:
        note += f" {unreadable} files lacked a readable name or tool list."
    return findings, note


def references(text: str) -> list[str]:
    """Explicitly delegated skill names in ``text``, outside fenced examples."""
    found, fence = [], None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        for pattern in _REFERENCES:
            found.extend(match.group(1).lower() for match in pattern.finditer(line))
    return list(dict.fromkeys(found))


def collect_references(root: Path, doc: dict):
    """Return (raw findings, note) for redirects to skills that do not exist."""
    root = Path(root).resolve()
    families, scanned, _unreadable, roots, _oversized = _scan(root, doc)
    if not roots:
        return [], "No project skill directories identified; references not checked."
    known = set(families)
    tops = ", ".join(sorted(path.relative_to(root).as_posix() for path in roots))
    findings = []
    checked = 0
    for name, members in sorted(families.items()):
        for path, _tools, body in members:
            frontmatter, _ = skill_lint.parse_frontmatter(root / path)
            text = f"{frontmatter.get('description') or ''}\n{body}"
            targets = references(text)
            checked += len(targets)
            missing = [target for target in targets if target not in known and target != name]
            if missing:
                findings.append({
                    "rule": "SKILL_REFERENCE_UNKNOWN", "severity": "WARN", "skill": name,
                    "source": path,
                    "message": (f"Redirects to {', '.join(missing[:TOOL_LIMIT])}, but no skill "
                                f"with that name exists under {tops}. Rename the redirect to "
                                "the served skill, or remove it. If another agent serves the "
                                "target, scope the redirect to that agent's variant."),
                })
    return findings, (f"Checked {checked} explicit skill redirects in {scanned} SKILL.md files "
                      f"under {tops}. Prose that names a skill without a redirect verb is not checked.")
