"""Host build artifacts and host-code patterns. Static: reads files, never imports.

Two checks, both derived from host release regressions:

``artifacts`` (automatic when ``uv.lock`` pins path wheels)
    ``ARTIFACT_WHEEL_MISSING`` / ``ARTIFACT_WHEEL_HASH_MISMATCH`` (ERROR): a
    locked path wheel is absent or its bytes differ from the lock hash.
    ``ARTIFACT_DOCKER_WHEEL_MISMATCH`` (ERROR): a root ``Dockerfile*`` ``ARG``
    default names a wheel with the same filename as a locked path wheel but a
    different directory. ``COPY`` of a stale-but-present wheel succeeds and the
    later frozen sync fails, so the image build breaks only in CI.
    ``DOCTOR_SDK_DIFFERS_FROM_LOCK`` (INFO): the running Doctor's checker files
    differ from the adk-lightning wheel the project locks. Findings then
    describe a different SDK than the one served; a diagnostic overlay may be
    intentional, but the report must say so.

``host_code`` (opt-in through ``doctor-host.json``)
    ``HISTORY_ROLE_USER_SCAN`` (WARN): a function loops over history, tests
    ``role == "user"`` and breaks/returns on it, yet never mentions function
    responses. ADK records tool results as ``role="user"`` content, so such a
    scan treats a tool result as the user's latest turn. Suppress a reviewed
    site with ``# doctor: ignore[HISTORY_ROLE_USER_SCAN]`` on the flagged line.

``doctor-host.json``::

    {"version": 1, "python": {"include": ["tools", "sub_agents"],
                               "exclude": ["tests"]}}
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
import zipfile

from adkl import execution

CONFIG = "doctor-host.json"
FILE_LIMIT = 20000
FILE_BYTES = 2_000_000
FINDING_LIMIT = 200
_IGNORE = re.compile(r"#\s*doctor:\s*ignore\[([A-Z_,\s]+)\]")
_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+\.whl)\s*$", re.M)
_FUNCTION_PARTS = frozenset({"function_response", "function_responses", "functionResponse",
                             "function_call", "function_calls", "functionCall",
                             "get_function_responses", "get_function_calls"})


def _safe_file(root: Path, relative: str) -> Path | None:
    """A regular project file reached without symlinks, or None."""
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    target = root / Path(*path.parts)
    if any((root / Path(*path.parts[:depth])).is_symlink()
           for depth in range(1, len(path.parts) + 1)):
        return None
    return target if target.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _locked_wheels(root: Path):
    """[(package, relative wheel path, expected sha256 or None)] from uv.lock."""
    lock = root / "uv.lock"
    if not lock.is_file() or lock.is_symlink():
        return None
    try:
        data = tomllib.loads(lock.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        # Lock validity belongs to the environment check and to uv itself.
        return None
    wheels = []
    for package in data.get("package", []):
        source = package.get("source") or {}
        path = source.get("path")
        if not isinstance(path, str) or not path.endswith(".whl"):
            continue
        expected = None
        name = PurePosixPath(path).name
        for wheel in package.get("wheels") or []:
            value = wheel.get("hash") or ""
            if wheel.get("filename") == name and value.startswith("sha256:"):
                expected = value.split(":", 1)[1]
        wheels.append((package.get("name"), path, expected))
    return wheels


def _installed_scanner():
    """{relative checker file: sha256} for the running Doctor."""
    from adkl import doctor_scope
    here = Path(__file__).parent
    names = doctor_scope.SCANNER_FILES
    return {f"adkl/{name}": _sha256(here / name) for name in names if (here / name).is_file()}


def collect_artifacts(root: Path):
    """Return (raw findings, note, coverage) for locked path wheels."""
    root = Path(root).resolve()
    wheels = _locked_wheels(root)
    if not wheels:
        return [], "No uv.lock path wheels; build artifacts not compared.", None
    findings = []

    def finding(rule, severity, source, message):
        findings.append({"rule": rule, "severity": severity, "source": source,
                         "skill": None, "message": message})

    present = {}
    for package, relative, expected in wheels:
        path = _safe_file(root, relative)
        if path is None:
            finding("ARTIFACT_WHEEL_MISSING", "ERROR", "uv.lock",
                    f"uv.lock pins {package} to {relative}, which is not a regular project file.")
            continue
        actual = _sha256(path)
        if expected and actual != expected:
            finding("ARTIFACT_WHEEL_HASH_MISMATCH", "ERROR", "uv.lock",
                    f"{relative} does not match the uv.lock sha256 for {package}.")
        present[relative] = (package, path)
    dockerfiles = sorted(path for path in root.glob("Dockerfile*")
                         if path.is_file() and not path.is_symlink())
    for dockerfile in dockerfiles:
        text = dockerfile.read_text(encoding="utf-8", errors="replace")
        for match in _ARG.finditer(text):
            variable, value = match.groups()
            name = PurePosixPath(value).name
            candidates = [relative for _p, relative, _e in wheels
                          if PurePosixPath(relative).name == name]
            if not candidates or any(relative == value or relative.endswith("/" + value)
                                     for relative in candidates):
                continue
            # Same filename, but only exact path suffixes agree with the lock.
            finding("ARTIFACT_DOCKER_WHEEL_MISMATCH", "ERROR", dockerfile.name,
                    f"{dockerfile.name} ARG {variable}={value} does not match the locked "
                    f"wheel path {' or '.join(candidates)}. COPY can succeed with a stale "
                    "wheel and the frozen dependency sync then fails in the image build.")
    locked_sdk = next((path for _relative, (package, path) in present.items()
                       if package == "adk-lightning"), None)
    sdk_note = ""
    if locked_sdk is not None:
        installed = _installed_scanner()
        try:
            with zipfile.ZipFile(locked_sdk) as archive:
                members = set(archive.namelist())
                differing = sorted(
                    name for name, digest in installed.items()
                    if name not in members
                    or hashlib.sha256(archive.read(name)).hexdigest() != digest)
        except (OSError, zipfile.BadZipFile):
            differing = None
        if differing is None:
            sdk_note = " Locked adk-lightning wheel is unreadable; Doctor SDK identity not compared."
        elif differing:
            shown = ", ".join(PurePosixPath(name).name for name in differing[:6])
            more = f" and {len(differing) - 6} more" if len(differing) > 6 else ""
            finding("DOCTOR_SDK_DIFFERS_FROM_LOCK", "INFO", "uv.lock",
                    f"The running Doctor differs from the locked adk-lightning wheel in "
                    f"{len(differing)} checker files ({shown}{more}). Findings describe the "
                    "diagnostic SDK, not the served one. Record the diagnostic pin.")
            sdk_note = " Doctor SDK differs from the locked wheel."
        else:
            sdk_note = " Doctor checker files match the locked adk-lightning wheel."
    coverage = [[relative, expected] for _package, relative, expected in wheels]
    coverage += [[path.name, _sha256(path)] for path in dockerfiles]
    note = (f"Compared {len(wheels)} locked path wheels with {len(dockerfiles)} root "
            f"Dockerfile(s).{sdk_note} Remote images and registry contents are not checked.")
    return findings, note, coverage


def _config(root: Path) -> dict | None:
    path = root / CONFIG
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{CONFIG} must be a regular project file")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("version") != 1 or set(config) - {"version", "python"}:
        raise ValueError(f"{CONFIG} must be version 1 with an optional python section")
    python = config.get("python") or {}
    if not isinstance(python, dict) or set(python) - {"include", "exclude"}:
        raise ValueError(f"{CONFIG} python accepts include and exclude")
    for key in ("include", "exclude"):
        values = python.get(key, [])
        if not isinstance(values, list) or not all(
                isinstance(value, str) and value and not PurePosixPath(value).is_absolute()
                and ".." not in PurePosixPath(value).parts for value in values):
            raise ValueError(f"{CONFIG} python.{key} must list project-relative paths")
    if not python.get("include"):
        raise ValueError(f"{CONFIG} python.include must name at least one path")
    return python


def _python_files(root: Path, python: dict) -> list[Path]:
    excluded = [PurePosixPath(value) for value in python.get("exclude", [])]
    found = []
    for value in python["include"]:
        start = root / value
        if not start.exists() or any((root / Path(*PurePosixPath(value).parts[:depth])).is_symlink()
                                     for depth in range(1, len(PurePosixPath(value).parts) + 1)):
            continue
        paths = [start] if start.is_file() else sorted(start.rglob("*.py"))
        for path in paths:
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if (path.suffix != ".py" or path.is_symlink()
                    or any(part.startswith(".") or part in execution.SOURCE_EXCLUDED
                           for part in relative.parts)
                    or any(relative == item or item in relative.parents for item in excluded)):
                continue
            found.append(path)
            if len(found) > FILE_LIMIT:
                raise ValueError("Too many host Python files; narrow doctor-host.json")
    return sorted(set(found))


def _is_role_user(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return False
    if not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return False
    sides = [node.left, *node.comparators]

    def role(value):
        if isinstance(value, ast.Attribute):
            return value.attr == "role"
        return (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "getattr" and len(value.args) > 1
                and isinstance(value.args[1], ast.Constant) and value.args[1].value == "role")

    return any(role(side) for side in sides) and any(
        isinstance(side, ast.Constant) and side.value == "user" for side in sides)


def _names(node: ast.AST) -> set[str]:
    """Attribute/variable names, plus getattr-style string names."""
    found = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute):
            found.add(item.attr)
        elif isinstance(item, ast.Name):
            found.add(item.id)
        elif (isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
              and item.func.id in {"getattr", "hasattr"} and len(item.args) > 1
              and isinstance(item.args[1], ast.Constant) and isinstance(item.args[1].value, str)):
            found.add(item.args[1].value)
    return found


_TEXT_NAMES = frozenset({"author", "text", "thought"})


def _text_variables(loop: ast.AST) -> frozenset[str]:
    """Names assigned from text inside ``loop`` (two propagation passes)."""
    tainted = set(_TEXT_NAMES)
    for _ in range(2):
        for node in ast.walk(loop):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if value is None or not _names(value) & tainted:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    tainted.update(item.id for item in ast.walk(target) if isinstance(item, ast.Name))
            # collected.append(text) taints the collection.
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"append", "extend", "add", "insert"}
                    and isinstance(node.func.value, ast.Name)
                    and any(_names(arg) & tainted for arg in node.args)):
                tainted.add(node.func.value.id)
    return frozenset(tainted)


def _guarded(test: ast.AST, text_names: frozenset[str] = _TEXT_NAMES) -> bool:
    """Whether an exit under ``test`` requires user text or authorship.

    ADK attributes tool-result events to the agent, and a function-response part
    has no text, so ``author == "user"`` or a non-empty text value already
    rejects them. Polarity matters: ``if not text: return`` exits *at* a tool
    result, which is the defect, so a negated check is never a guard.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return False
    if isinstance(test, ast.BoolOp):
        parts = [_guarded(value, text_names) for value in test.values]
        return all(parts) if isinstance(test.op, ast.Or) else any(parts)
    return bool(_names(test) & text_names)


def _unguarded_exits(loop: ast.AST, text_names: frozenset[str]) -> list[ast.AST]:
    """Break/return in ``loop`` not nested under a text/author/thought check.

    ``for part in parts: if part.text: return part.text`` skips tool results,
    whose parts have no text, and continues to older content: not this defect.
    """
    exits = []

    def visit(node, guarded):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, (ast.Break, ast.Return)) and not guarded:
                exits.append(child)
            inner = guarded
            if isinstance(child, (ast.If, ast.While)) and _guarded(child.test, text_names):
                inner = True
            if isinstance(child, (ast.For, ast.AsyncFor)) and child is not loop:
                # A nested loop's own break exits only that loop.
                visit_nested(child, inner)
                continue
            visit(child, inner)

    def visit_nested(nested, guarded):
        for child in ast.walk(nested):
            if (isinstance(child, ast.Return) and not guarded
                    and not _under_guard(nested, child, text_names)):
                exits.append(child)

    visit(loop, False)
    return exits


def _under_guard(root: ast.AST, target: ast.AST, text_names: frozenset[str]) -> bool:
    for node in ast.walk(root):
        if isinstance(node, (ast.If, ast.While)) and _guarded(node.test, text_names):
            if any(item is target for item in ast.walk(node)):
                return True
    return False


def _mentions_function_parts(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and node.attr in _FUNCTION_PARTS:
            return True
        if isinstance(node, ast.Name) and node.id in _FUNCTION_PARTS:
            return True
        if isinstance(node, ast.Constant) and node.value in _FUNCTION_PARTS:
            return True
    return False


def role_user_scans(source: str) -> list[tuple[int, str]]:
    """(line, function) for history scans that stop on any role=user content."""
    tree = ast.parse(source)
    lines = source.splitlines()
    hits = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _mentions_function_parts(function):
            continue
        # Report the outermost loop once; nested functions report themselves.
        for loop in ast.walk(function):
            if not isinstance(loop, (ast.For, ast.AsyncFor)):
                continue
            # ADK history only: session events or request contents. AG-UI
            # messages carry tool results under their own "tool" role.
            if not _names(loop.iter) & {"events", "contents"}:
                continue
            tests = [node.test for node in ast.walk(loop) if isinstance(node, (ast.If, ast.IfExp))
                     and any(_is_role_user(part) for part in ast.walk(node.test))]
            if not tests or all(_guarded(test) for test in tests):
                continue
            hit = next(part.lineno for test in tests if not _guarded(test)
                       for part in ast.walk(test) if _is_role_user(part))
            # Filtering to role=user and then exiting the loop treats the first
            # such content as the user's turn: `if role != "user": continue`
            # followed by a return is the same defect as `if role == "user": break`.
            exits = _unguarded_exits(loop, _text_variables(loop))
            if not exits:
                continue
            ignored = _IGNORE.search(lines[hit - 1]) if hit - 1 < len(lines) else None
            if not (ignored and "HISTORY_ROLE_USER_SCAN" in ignored.group(1)):
                hits.append((hit, function.name))
            break
    return sorted(set(hits))


def collect_host_code(root: Path):
    """Return (raw findings, note, coverage); coverage is None when unconfigured."""
    root = Path(root).resolve()
    python = _config(root)
    if python is None:
        return [], f"Opt in with {CONFIG}; host Python patterns not checked.", None
    findings, unparsed, scanned = [], 0, 0
    coverage = []
    for path in _python_files(root, python):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        coverage.append(relative)
        if len(data) > FILE_BYTES:
            unparsed += 1
            continue
        scanned += 1
        try:
            hits = role_user_scans(data.decode("utf-8-sig", errors="replace"))
        except (SyntaxError, ValueError, RecursionError):
            unparsed += 1
            continue
        for line, function in hits:
            if len(findings) >= FINDING_LIMIT:
                break
            findings.append({
                "rule": "HISTORY_ROLE_USER_SCAN", "severity": "WARN", "skill": None,
                "source": relative, "line": line,
                "message": (f"{function}() stops a history loop on role == \"user\" "
                            "without excluding function responses. ADK records tool "
                            "results as role=\"user\"; skip parts that carry only "
                            "function_response before treating content as the user's turn."),
            })
    note = (f"AST-scanned {scanned} host Python files from {CONFIG}; no host code imported."
            + (f" {unparsed} files skipped (too large or unparsable)." if unparsed else "")
            + " Heuristic: review each site; tests and dynamic history access are not covered.")
    return findings, note, coverage
