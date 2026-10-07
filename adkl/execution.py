"""Project execution profiles and isolated local workers (stdlib + PyYAML).

Processes isolate imports and configuration, not hostile code. Remote targets
fail closed until an authenticated customer-hosted transport is implemented.
"""
from __future__ import annotations

import atexit
from copy import deepcopy
import hashlib
import json
import os
import pathlib
import re
import selectors
import subprocess
import threading
import time

import yaml

PLATFORM = pathlib.Path(__file__).resolve().parent.parent
SAFE_ENV = {"LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
FORBIDDEN = {"HOME", "PATH", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}


class ExecutionError(RuntimeError):
    status = 503
    code = "execution_failed"
    action = None

    def payload(self):
        return {"error": str(self), "code": self.code,
                "recoverable": self.action is not None, "action": self.action}


class StaleConversationError(ExecutionError):
    """The worker still owns the previous project revision; Clear replaces it."""

    status = 409
    code = "stale_conversation"
    action = "clear_conversation"


SOURCE_EXCLUDED = {"__pycache__", "node_modules", "vendor", "releases", "build", "dist"}
CODE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".lock"}


def _source_paths(values, field):
    """Project-relative files or directories, with no way to name anything else."""
    paths = []
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value:
            raise ExecutionError(f"{field} requires project-relative paths")
        path = pathlib.Path(value)
        if path.is_absolute() or not path.parts or any(
            p == ".." or p.startswith(".") or p in SOURCE_EXCLUDED for p in path.parts
        ) or any(c in value for c in "*?[]"):
            raise ExecutionError(f"{field} paths must stay inside the project and exclude private/runtime directories; globs are unsupported")
        paths.append(path)
    return paths


def _content_paths(doc):
    """None preserves legacy Markdown tracking; a list opts into explicit content."""
    source = doc.get("source")
    if source is None:
        return None
    if not isinstance(source, dict) or not source or set(source) - {"content", "exclude"}:
        raise ExecutionError("execution.source may declare content and exclude lists of project-relative files or directories")
    if "content" not in source:
        return None                     # exclude alone leaves Markdown tracking as it was
    if not isinstance(source["content"], list):
        raise ExecutionError("execution.source.content must be a list of project-relative files or directories")
    return _source_paths(source["content"], "execution.source.content")


def _excluded_paths(doc):
    """Bytes a project writes into its own tree that are state, not identity.

    The walk already skips dot-prefixed names, dependency directories, anything
    carrying a `pyvenv.cfg`, and each profile's `env_file`. What it cannot infer
    is a credential or cache file the application rewrites at an ordinary-looking
    path. One host refreshes an OIDC token into `oidc_token.json` at its project
    root: a `.json`, not dot-prefixed, so the walk hashed it, and any `uv run` in
    another terminal changed project identity mid-session and made the console
    refuse to reuse its worker -- the same churn `pyvenv.cfg` already exempts, one
    file instead of a tree. Declaring it here is the project owner making the
    claim `env_file` already makes for a dotenv: these bytes are not source.
    """
    source = doc.get("source")
    if source is None or "exclude" not in source:
        return []
    if not isinstance(source["exclude"], list):
        raise ExecutionError("execution.source.exclude must be a list of project-relative files or directories")
    return _source_paths(source["exclude"], "execution.source.exclude")


def _declared_content(doc):
    """Prompts and skill sources remain dependencies even in explicit mode."""
    instruction = (doc.get("agent") or {}).get("instruction") or {}
    result = []
    if isinstance(instruction, dict):
        if instruction.get("template"):
            result.append(instruction["template"])
        for slot in (instruction.get("slots") or {}).values():
            if isinstance(slot, str):
                result.append(slot)
            elif isinstance(slot, dict):
                result.extend((slot.get("variants") or {}).values())
    for value in ((doc.get("authoring") or {}).get("skills") or {}).values():
        if isinstance(value, str):
            result.append(str(pathlib.Path(value).parent))
    # The skill tree includes native references, assets and override directories.
    if doc.get("skills"):
        result.append(doc["skills"].get("dir") or "skills")
    slots = ((doc.get("features") or {}).get("prompt_state_injection") or {}).get("slots") or {}
    for slot in slots.values():
        if isinstance(slot, dict) and slot.get("from_file"):
            result.append(slot["from_file"])
    return [pathlib.Path(value) for value in result if isinstance(value, str)]


def _tracked_target(resolved_root: pathlib.Path, path: pathlib.Path):
    """Where a symlink points, when this walk hashes that target's bytes anyway.

    Leaving the project is what the guard exists to refuse: it would let content
    outside the project decide the digest. A link that stays inside is a
    content-sharing idiom instead -- hosts share one skill's assets and
    references across channel variants that way -- and refusing it makes such a
    project un-inspectable with no recovery. Nothing is lost by not following
    it, because the target is hashed at its own canonical path during the same
    walk. A broken or looping link, or one reaching a directory this walk
    excludes, has no such canonical copy: return None so the caller refuses it
    rather than recording an identity that cannot see its own content change.
    """
    try:
        target = path.resolve(strict=True)
    except OSError:
        return None
    if not target.is_relative_to(resolved_root):
        return None
    relative = target.relative_to(resolved_root)
    if any(part.startswith(".") or part in SOURCE_EXCLUDED for part in relative.parts):
        return None
    return relative


def _file_stamp(path):
    stat = path.lstat()
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def source_digest(root: pathlib.Path, *, _metadata_only: bool = False,
                  _inventory: dict | None = None) -> str:
    """Identity of project code/content; excludes runtime state and secret files.

    The private metadata mode is a browsing-cache hint, never an execution
    identity. It walks the same tracked paths without reopening every file.
    """
    def read(name):
        path = root / name
        if path.is_symlink():
            raise ExecutionError(f"{name} must be a regular file, not a symlink")
        try:
            doc = yaml.safe_load(path.read_text()) if path.is_file() else {}
        except (yaml.YAMLError, OSError):
            raise ExecutionError(f"Cannot read {name} for runtime source tracking") from None
        if doc is not None and not isinstance(doc, dict):
            raise ExecutionError(f"{name} must be a mapping")
        return doc or {}

    execution_doc = read("execution.yaml")
    explicit = _content_paths(execution_doc)
    excluded = _excluded_paths(execution_doc)
    content = [*(explicit or []), *_declared_content(read("agent.yaml"))]
    secrets = {pathlib.Path(entry.get("env_file", ".env")) for entry in
               execution_doc.get("profiles", {}).values() if isinstance(entry, dict)}
    # Two declarations disagreeing about the same bytes cannot both be honoured,
    # and silently preferring one would let identity turn on which check ran first.
    contradictions = sorted(path.as_posix() for path in content
                            if any(path == excluded_path or path in excluded_path.parents
                                   or excluded_path in path.parents for excluded_path in excluded))
    if contradictions:
        raise ExecutionError("execution.source declares the same path as content and exclude: "
                             + ", ".join(contradictions))
    resolved_root = root.resolve()
    aliases = []
    for path in content:
        if path.is_absolute() or path in secrets or any(
            p.startswith(".") or p in SOURCE_EXCLUDED for p in path.parts
        ):
            raise ExecutionError("Runtime content must stay inside the project and exclude credential files and private/runtime directories")
        if any((root / parent).is_symlink() for parent in [path, *path.parents]):
            target = _tracked_target(resolved_root, root / path)
            if target is None:
                raise ExecutionError("Declared runtime content must resolve to tracked project files: "
                                     f"{path.as_posix()} does not")
            # The walk reaches these bytes by their real path, not the alias.
            aliases.append(target)
    content += aliases
    content = set(content)
    excluded = set(excluded)
    digest = hashlib.sha256()
    tracked = set()
    links = []

    def link(path):
        """Track the link itself; re-pointing it still changes project identity."""
        target = _tracked_target(resolved_root, path)
        if target is None:
            raise ExecutionError("Project source symlinks must resolve to tracked project files: "
                                 f"{path.relative_to(root).as_posix()} does not")
        links.append((path.relative_to(root), target))
        if _inventory is not None:
            _inventory[path] = _file_stamp(path)
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(target.as_posix().encode())

    def is_excluded(relative):
        return relative in excluded or not excluded.isdisjoint(relative.parents)

    for directory, dirs, files in os.walk(root):
        if _inventory is not None:
            folder = pathlib.Path(directory)
            _inventory[folder] = _file_stamp(folder)
            # Removing pyvenv.cfg makes a previously skipped tree source again.
            for name in dirs:
                child = folder / name
                if not name.startswith('.') and name not in SOURCE_EXCLUDED:
                    marker = child / 'pyvenv.cfg'
                    if marker.is_file():
                        _inventory[marker] = _file_stamp(marker)
        tracked.add(pathlib.Path(directory).relative_to(root))
        # A virtualenv contains installed dependencies, not project source.
        # Detect the environment marker, not just the conventional `.venv` name:
        # hashing dependencies is expensive and makes source identity unstable.
        #
        # `pyvenv.cfg` is the marker PEP 405 requires at an environment root, so
        # this recognises an env under any name without a configurable list.
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SOURCE_EXCLUDED
                         and not (pathlib.Path(directory) / d / "pyvenv.cfg").is_file()
                         and not is_excluded((pathlib.Path(directory) / d).relative_to(root)))
        for name in dirs:
            path = pathlib.Path(directory) / name
            if path.is_symlink():
                link(path)
        # Unmapped native skills still execute: always track their complete resources.
        if "SKILL.md" in files:
            content.add(pathlib.Path(directory).relative_to(root))
        for name in sorted(files):
            path = pathlib.Path(directory) / name
            relative = path.relative_to(root)
            if name.startswith(".") or relative in secrets or is_excluded(relative):
                continue
            if (path.suffix not in CODE_SUFFIXES and not (explicit is None and path.suffix == ".md")
                    and relative not in content and content.isdisjoint(relative.parents)):
                continue
            if path.is_symlink():
                link(path)
                continue
            tracked.add(relative)
            digest.update(path.relative_to(root).as_posix().encode())
            if _metadata_only:
                stamp = _file_stamp(path)
                if _inventory is not None:
                    _inventory[path] = stamp
                digest.update(str(stamp).encode())
            else:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    for alias, target in links:
        if target not in tracked:
            raise ExecutionError("Project source symlinks must resolve to tracked project files: "
                                 f"{alias.as_posix()} does not")
    return digest.hexdigest()


def inside(root: pathlib.Path, value: str) -> pathlib.Path:
    path = root / value
    if not path.resolve().is_relative_to(root.resolve()):
        raise ExecutionError("Execution paths must stay inside the project")
    return path


def profiles(root: pathlib.Path) -> dict:
    path = root / "execution.yaml"
    if not path.is_file():
        raise ExecutionError("No execution.yaml: this project is available for authoring only")
    try:
        doc = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        raise ExecutionError("Cannot parse execution.yaml; check its YAML syntax and permissions") from None
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise ExecutionError("execution.yaml must declare version: 1")
    _content_paths(doc)
    _excluded_paths(doc)
    entries = doc.get("profiles")
    if not isinstance(entries, dict) or not entries:
        raise ExecutionError("execution.yaml needs profiles")
    if doc.get("default") not in entries:
        raise ExecutionError("execution.yaml default must name a profile")
    for name, entry in entries.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ExecutionError("Invalid execution profile name")
        if not isinstance(entry, dict) or entry.get("target") not in {"local", "remote"}:
            raise ExecutionError(f"{name}: target must be local or remote")
        if set(entry) - {"target", "environment", "python", "env_file", "env", "secrets", "inherit", "worker"}:
            raise ExecutionError(f"{name}: unknown execution profile field")
        if not isinstance(entry.get("environment"), str):
            raise ExecutionError(f"{name}: environment is required")
        if "worker" in entry and not isinstance(entry["worker"], str):
            raise ExecutionError(f"{name}: worker must be a name")
        for field in ("python", "env_file"):
            if field in entry:
                if not isinstance(entry[field], str):
                    raise ExecutionError(f"{name}: {field} must be a project-relative path")
                # Python launchers are commonly symlinks outside the venv.
                if field == "python":
                    p = pathlib.Path(entry[field])
                    if p.is_absolute() or ".." in p.parts or p.parts[:1] != (".venv",):
                        raise ExecutionError(f"{name}: python must be under .venv/")
                else:
                    inside(root, entry[field])
        for field in ("secrets", "inherit"):
            values = entry.get(field, [])
            if not isinstance(values, list) or any(
                not isinstance(k, str) or not ENV_NAME.fullmatch(k)
                or k in FORBIDDEN or k.startswith(("PYTHON", "LD_", "DYLD_", "ADKL_"))
                for k in values
            ):
                raise ExecutionError(f"{name}: invalid {field} names")
        env = entry.get("env", {})
        if not isinstance(env, dict) or any(
            not isinstance(k, str) or not ENV_NAME.fullmatch(k)
            or k in FORBIDDEN or k.startswith(("PYTHON", "LD_", "DYLD_", "ADKL_"))
            or not isinstance(v, str) for k, v in env.items()
        ):
            raise ExecutionError(f"{name}: env must contain string settings with safe names")
        if set(env) & set(entry.get("secrets", [])):
            raise ExecutionError(f"{name}: secrets must be references, not inline env values")
        if set(entry.get("inherit", [])) - SAFE_ENV - set(entry.get("secrets", [])):
            raise ExecutionError(f"{name}: inherit must name declared secrets or transport settings")
    return doc


def profile(root: pathlib.Path, name: str | None = None) -> tuple[str, dict]:
    doc = profiles(root)
    name = name or doc["default"]
    if name not in doc["profiles"]:
        raise ExecutionError(f"Unknown execution profile: {name}")
    return name, doc["profiles"][name]


def require_local(root: pathlib.Path, name: str | None = None) -> None:
    _, entry = profile(root, name)
    if entry["target"] != "local":
        raise ExecutionError(
            f"{entry.get('worker', 'Customer')} execution environment unavailable. "
            "This profile requires an approved customer-hosted worker; local execution is disabled.")
    doc = yaml.safe_load((root / "agent.yaml").read_text()) or {}
    repo = (doc.get("tools") or {}).get("repo")
    if repo:
        inside(root, repo)


def dotenv(path: pathlib.Path) -> dict[str, str]:
    """Literal dotenv values only: no expansion, shell execution or inheritance."""
    result = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key.strip()] = value
    return result


def worker_environment(root: pathlib.Path, name: str, entry: dict,
                       parent: dict | None = None) -> dict[str, str]:
    parent = os.environ if parent is None else parent
    state = inside(root, f".adkl/{name}")
    for folder in (state, state / "home", state / "tmp"):
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = {"PATH": str(root / ".venv/bin") + os.pathsep + os.defpath,
           "HOME": str(state / "home"), "TMPDIR": str(state / "tmp"),
           "PYTHONUNBUFFERED": "1", "ADKL_WORKER": "1",
           "ADKL_PROFILE": name, "ADKL_STATE_DIR": str(state)}
    env.update(entry.get("env", {}))
    local = dotenv(inside(root, entry.get("env_file", ".env")))
    for key in entry.get("secrets", []):
        if key in local:
            env[key] = local[key]
    # Only named values may be inherited, including optional corporate CA/proxy
    # settings. Credentials in the launching shell otherwise cannot override a project.
    allowed = set(entry.get("inherit", []))
    for key in allowed:
        if key not in SAFE_ENV and key not in entry.get("secrets", []):
            raise ExecutionError(f"inherit name {key} must be a declared secret or transport setting")
        if key in parent:
            env[key] = parent[key]
    return env


class Worker:
    """Serialized JSON-line RPC to one project's interpreter and event loop."""

    def __init__(self, root: pathlib.Path, name: str | None = None, timeout: float = 120):
        self.root = root.resolve()
        self.name, self.entry = profile(self.root, name)
        self.timeout = timeout
        self.process = None
        self.lock = threading.RLock()
        self.buffer = b""
        self.secrets = []
        self._inspection = None
        self._inspection_inventory = {}
        self.identity = {
            "project": self.root.name, "profile": self.name,
            "environment": self.entry["environment"], "target": self.entry["target"],
            "lockDigest": self._digest("uv.lock"),
            "profileDigest": self._digest("execution.yaml"),
            "agentDigest": self._digest("agent.yaml"),
            "sourceDigest": source_digest(self.root),
        }

    def _digest(self, name):
        path = self.root / name
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    def _start(self):
        require_local(self.root, self.name)
        python = self.root / self.entry.get("python", ".venv/bin/python")
        if not python.is_file() or not (self.root / "uv.lock").is_file():
            raise ExecutionError(f"Runtime not prepared: run uv sync --locked --project {self.root}")
        env = worker_environment(self.root, self.name, self.entry)
        self.secrets = [env[k] for k in self.entry.get("secrets", []) if env.get(k)]
        self.process = subprocess.Popen(
            [str(python), "-I", str(PLATFORM / "adkl/worker.py"), str(self.root)],
            cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        atexit.register(self.close)

    def request(self, operation: str, **payload) -> dict:
        if operation == "stream":
            raise ExecutionError("Use Worker.stream for streaming operations")
        return next(self._exchange(operation, payload))

    def browse_inspection(self) -> dict:
        """Reuse declarations for browsing, not for execution or edit validation.

        Every read checks tracked file metadata (including ctime and inode).
        A changed stamp goes through the normal byte-level stale-worker guard.
        No TTL hides ordinary external edits, additions, deletions or renames.
        """
        with self.lock:
            if self._inspection is not None and self.process is not None and self.process.poll() is None:
                try:
                    unchanged = all(_file_stamp(path) == stamp
                                    for path, stamp in self._inspection_inventory.items())
                except OSError:
                    unchanged = False
                if unchanged:
                    return deepcopy(self._inspection)
            self._inspection = None
            stamp = source_digest(self.root, _metadata_only=True)
            report = self.request("inspect")
            inventory = {}
            after = source_digest(self.root, _metadata_only=True, _inventory=inventory)
            if after != stamp:
                raise StaleConversationError(
                    "Project changed during inspection; clear the conversation to reload it")
            self._inspection = deepcopy(report)
            self._inspection_inventory = inventory
            return report

    def stream(self, text: str, state=None):
        """Versioned frames; progress contains metadata, text is a complete reply."""
        yield from self._exchange("stream", {"version": 1, "text": text, "state": state})

    def _exchange(self, operation, payload):
        streaming = operation == "stream"
        completed = False
        with self.lock:
            self._inspection = None
            self._inspection_inventory = {}
            try:
                if any(self._digest(file) != self.identity[key] for file, key in (
                    ("uv.lock", "lockDigest"), ("execution.yaml", "profileDigest"),
                    ("agent.yaml", "agentDigest"),
                )):
                    raise StaleConversationError("Project configuration changed; clear the conversation to reload it")
                if source_digest(self.root) != self.identity["sourceDigest"]:
                    raise StaleConversationError("Project code or content changed; clear the conversation to reload it")
                if self.process is None:
                    self._start()
                if self.process.poll() is not None:
                    raise ExecutionError("Project worker exited; retry with a new conversation")
                wire = json.dumps({"operation": operation, **payload}).encode() + b"\n"
                self.process.stdin.write(wire)
                self.process.stdin.flush()
                deadline = time.monotonic() + self.timeout
                total = 0
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdout, selectors.EVENT_READ)
                    while True:
                        while b"\n" not in self.buffer:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise ExecutionError("Project worker timed out; conversation was stopped")
                            if not selector.select(min(remaining, 1) if streaming else remaining):
                                if streaming:
                                    yield {"version": 1, "type": "ping"}
                                    continue
                                raise ExecutionError("Project worker timed out; conversation was stopped")
                            chunk = os.read(self.process.stdout.fileno(), 65536)
                            if not chunk:
                                raise ExecutionError("Project worker exited before responding; check its locked runtime dependencies")
                            self.buffer += chunk
                            total += len(chunk)
                            if len(self.buffer) > 8 * 1024 * 1024 or total > 32 * 1024 * 1024:
                                raise ExecutionError("Project worker response exceeded size limit")
                        if time.monotonic() >= deadline:
                            raise ExecutionError("Project worker timed out; conversation was stopped")
                        line, self.buffer = self.buffer.split(b"\n", 1)
                        result = self._redact(json.loads(line))
                        if not isinstance(result, dict):
                            raise ExecutionError("Invalid worker response")
                        if "workerError" in result:
                            raise ExecutionError(result["workerError"])
                        if streaming:
                            if result.get("version") != 1 or result.get("type") not in {"progress", "text", "done", "error"}:
                                raise ExecutionError("Invalid worker stream frame")
                            if result["type"] == "error":
                                raise ExecutionError(result.get("error", "Worker stream failed"))
                            completed = result["type"] == "done"
                            if completed:
                                if not isinstance(result.get("result"), dict):
                                    raise ExecutionError("Invalid worker terminal result")
                                result["result"]["execution"] = {**self.identity, "pid": self.process.pid}
                        else:
                            completed = True
                            result["execution"] = {**self.identity, "pid": self.process.pid}
                        yield result
                        if completed:
                            return
            except ExecutionError:
                raise
            except Exception:
                raise ExecutionError("Project worker communication failed") from None
            finally:
                # Also runs on GeneratorExit: disconnect/cancel discards the
                # worker and its unfinished session, so no stale frames survive.
                if not completed:
                    self.close()

    def _redact(self, value):
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
        elif isinstance(value, dict):
            value = {self._redact(k): self._redact(v) for k, v in value.items()}
        elif isinstance(value, list):
            value = [self._redact(v) for v in value]
        return value

    def close(self):
        with self.lock:
            self._inspection = None
            self._inspection_inventory = {}
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                self.process.stdin.close()
                self.process.stdout.close()
                self.process = None
            self.buffer = b""
            atexit.unregister(self.close)
