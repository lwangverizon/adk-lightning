"""Explicit local setup/build jobs, separate from preview requests."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import threading

_jobs = {}
_lock = threading.Lock()


def status(root):
    with _lock:
        return dict(_jobs.get(str(root), {"state": "idle"}))


def start(root, command, wheel=None):
    root = pathlib.Path(root).resolve()
    if command in {"baseline", "candidate"}:
        from adkl import verification
        verification.snapshot(root)
        args = None
    elif command == "prepare":
        if wheel is None or not pathlib.Path(wheel).is_file():
            raise ValueError("Start the console with --sdk-wheel pointing to a built ADKL wheel.")
        args = [sys.executable, "-m", "adkl.projects", "prepare", str(root), "--wheel", str(wheel)]
    elif command == "release":
        # A unique filename prevents replacing previously reviewed evidence.
        import uuid
        output = root / ".adkl" / "releases" / f"{uuid.uuid4().hex}.zip"
        if not output.resolve().is_relative_to(root):
            raise ValueError("Release output must remain inside the project.")
        args = [sys.executable, "-m", "adkl.release", "build", str(root), "--output", str(output)]
    else:
        raise ValueError("Unknown project operation.")
    with _lock:
        if _jobs.get(str(root), {}).get("state") == "running":
            raise ValueError("A project operation is already running.")
        _jobs[str(root)] = {"state": "running", "operation": command}

    def run():
        try:
            if command in {"baseline", "candidate"}:
                record = verification.run(root, command)
                outcome = {"state": "succeeded" if record["state"] in {"passed", "baseline_recorded"}
                           else "failed", "operation": command}
                with _lock:
                    _jobs[str(root)] = outcome
                return
            result = subprocess.run(args, cwd=pathlib.Path(__file__).resolve().parent.parent,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            outcome = {"state": "succeeded" if result.returncode == 0 else "failed", "operation": command}
            if result.returncode:
                outcome["message"] = ("Operation failed. Run the documented CLI command locally for details; "
                                      "check uv availability, package access, and project validation.")
            elif command == "release":
                outcome["artifact"] = output.name
        except Exception as exc:
            outcome = {"state": "failed", "operation": command, "message": type(exc).__name__}
        with _lock:
            _jobs[str(root)] = outcome
    threading.Thread(target=run, daemon=True).start()
    return status(root)
