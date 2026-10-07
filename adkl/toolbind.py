#!/usr/bin/env python3
"""Import project tools inside a dedicated execution worker.

Imports and cwd are process-global. The authoring server never calls this module;
workers own one project each. Credentials are resolved before worker startup.
load_env remains an explicit legacy helper; prepare never loads credentials.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import pathlib
import sys


class ToolBindError(RuntimeError):
    """Raised when a repo path or a tool symbol cannot be resolved."""


def load_env(repo: pathlib.Path) -> list[str]:
    """Load `repo/.env` into os.environ without overwriting existing values.

    Returns the names it set, so a caller can report what came from the file
    rather than leaving the operator to guess, or None when there is no file.
    """
    env_file = repo / ".env"
    if not env_file.is_file():
        # Tools may need no environment file. Report missing credentials at the
        # import/execution boundary only if the tool actually requires them.
        return None

    applied: list[str] = []
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        # Strip one layer of matching quotes, which the source .env uses
        # inconsistently -- some values are quoted, some are bare.
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    return applied


def prepare(repo: pathlib.Path) -> pathlib.Path:
    """Put the repo on sys.path. Credentials are supplied by the worker.

    Split out because two callers need it: tool imports, and widget spec hooks
    that must resolve *before* any tool module is imported. Doing it in one place
    keeps path preparation identical for both. Managed workers supply declared
    environment settings before any project import.

    Note this does NOT chdir; only `_in_repo` does, for the duration of an
    import. A permanent chdir would break every later relative path in the host
    process.
    """
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise ToolBindError(f"tool repo not found: {repo}")

    # Deliberately NOT load_env(repo), though a merge briefly put it back here.
    # `prepare` runs in whatever process asks for it, including the long-lived
    # authoring server that serves every project, so loading one project's .env
    # here copies that project's secrets into a process shared with the others.
    # Credentials belong to the worker, which gets them at startup from
    # execution.worker_environment, and test_tool_preparation_does_not_change_
    # parent_environment pins that boundary.
    #
    # In-process callers that need a repo's environment must call load_env
    # explicitly, making the environment mutation visible at that call site.

    # Drop adkl/ from sys.path before adding the repo.
    #
    # In script mode (`python3 adkl/widget_lint.py`) Python puts the SCRIPT's own
    # directory at sys.path[0], which makes every `adkl/*.py` importable as a
    # top-level module. `adkl/a2ui.py` then shadows the agent repo's `a2ui`
    # PACKAGE, and the failure is unrecognisable: "No module named 'a2ui.schema';
    # 'a2ui' is not a package" -- naming a module the caller never mentioned. Any
    # future adkl module sharing a name with an agent-repo package would do the
    # same, so this removes the whole directory rather than special-casing a2ui.
    #
    # The repo entry is dropped too before re-inserting, so a second prepare()
    # cannot leave a stale duplicate ahead of the fresh one.
    here = str(pathlib.Path(__file__).resolve().parent)
    sys.path[:] = [p for p in sys.path if p not in ("", here, str(repo))]
    sys.path.insert(0, str(repo))
    return repo


def _dotted(module_path: pathlib.Path, repo: pathlib.Path) -> str:
    """`/repo/tools/search_supports.py` -> `tools.search_supports`."""
    try:
        rel = module_path.resolve().relative_to(repo.resolve())
    except ValueError as exc:
        raise ToolBindError(
            f"{module_path} is not inside {repo}; the catalog was generated "
            f"against a different checkout") from exc
    return str(rel.with_suffix("")).replace(os.sep, ".")


@contextlib.contextmanager
def _in_repo(repo: pathlib.Path):
    """Import with the repo as cwd and on sys.path.

    Both are required: sys.path for `import tools.x`, and cwd because some
    modules open data files by relative path while being imported.
    """
    prev_cwd = pathlib.Path.cwd()
    added = str(repo) not in sys.path
    if added:
        sys.path.insert(0, str(repo))
    os.chdir(repo)
    try:
        yield
    finally:
        os.chdir(prev_cwd)
        # sys.path is left alone on purpose: modules already imported hold
        # references that would break if the path disappeared underneath them.


def load(repo: pathlib.Path, catalog: dict, names: list[str] | None = None
         ) -> tuple[dict, list[str]]:
    """Import tool callables named in `catalog`.

    Returns `({name: callable}, [problems])`. A tool that fails to import is
    reported rather than raised: one broken module should not cost the whole
    session, and the console shows what is missing per turn.
    """
    had_env = (repo.expanduser().resolve() / ".env").is_file()
    repo = prepare(repo)

    wanted = set(names) if names else None
    resolved: dict = {}
    problems: list[str] = []

    with _in_repo(repo):
        for entry in catalog.get("tools", []):
            name = entry.get("name")
            if wanted is not None and name not in wanted:
                continue
            module = entry.get("module")
            if module == "":
                # An EMPTY module is a deliberate marker: the tool exists in the
                # agent but is not statically importable, e.g.
                # AgentTool(agent=build_x()) constructed at runtime. Catalogued so
                # skills declaring it are not flagged, skipped here so it does not
                # report an import failure on every turn. A MISSING key is still a
                # malformed entry -- see below.
                continue
            if not module:
                problems.append(f"{name}: catalog entry has no module path")
                continue
            try:
                dotted = _dotted(pathlib.Path(module), repo)
                mod = importlib.import_module(dotted)
            except ToolBindError as exc:
                problems.append(f"{name}: {exc}")
                continue
            except Exception as exc:
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
                continue

            fn = getattr(mod, name, None)
            if fn is None or not callable(fn):
                # The registration name can differ from the symbol (the prospect
                # agent swaps in narrowed views under the same function name), so
                # say which module was searched rather than just "not found".
                problems.append(f"{name}: not a callable in {dotted}")
                continue
            resolved[name] = fn

    # Only now is a missing .env worth mentioning: tools that import fine did
    # not need one, and saying so up front was how this turned "no env vars
    # required" into "no tools at all".
    if problems and not had_env:
        problems.insert(0, f"no .env in {repo} — tool modules that read "
                            f"configuration at import time (GCP_PROJECT, ENV, "
                            f"DATABASE_URL) will fail below until it exists.")

    return resolved, problems
