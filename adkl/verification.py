"""Run a host evaluator against declared, saved inputs and retain comparison evidence.

Host callables own agent execution and grading. This local workflow neither
certifies omitted inputs nor publishes an agent. Reports remain under .adkl/.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

import yaml

from adkl import evaluation, execution


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _private(parts):
    return any(part.startswith(".") or part in {"__pycache__", "node_modules", "build", "dist"}
               or part.lower().endswith((".key", ".pem", ".p12", ".pfx", ".pyc")) for part in parts)


def _path(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Verification inputs must be project-relative paths")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError("Verification inputs must stay inside the project")
    if _private(path.parts):
        raise ValueError("Private and generated files cannot be verification inputs")
    target = root / path
    if any((root / parent).is_symlink() for parent in [path, *path.parents]):
        # A link within the project names files this project already declares.
        # One that leaves it would retain evidence for bytes nothing here owns.
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            raise ValueError(f"Missing verification input: {name}") from None
        if not resolved.is_relative_to(root):
            raise ValueError("Verification inputs must resolve to files inside the project")
        if _private(resolved.relative_to(root).parts):
            raise ValueError("Private and generated files cannot be verification inputs")
    settings = execution.profiles(root) if (root / "execution.yaml").is_file() else {"profiles": {}}
    private = [Path(entry.get("env_file", ".env")) for entry in settings["profiles"].values()]
    private += execution._excluded_paths(settings)
    if any(target.resolve().is_relative_to((root / item).resolve()) for item in private):
        raise ValueError("Credential and excluded files cannot be verification inputs")
    if not target.is_file() and not target.is_dir():
        raise ValueError(f"Missing verification input: {name}")
    return target


def _files(root, names):
    if not isinstance(names, list) or not names:
        raise ValueError("Declare a nonempty list of verification input paths")
    result = {}
    ancestors = set()

    def visit(name):
        path = _path(root, name)
        if path.is_dir():
            canonical = path.resolve()
            if canonical in ancestors:
                raise ValueError("Verification directory symlink cycle")
            ancestors.add(canonical)
            try:
                for child in sorted(path.iterdir()):
                    # Python imports create caches; they are never source evidence.
                    if child.name in {"__pycache__", ".gitkeep"}:
                        continue
                    visit(child.relative_to(root).as_posix())
            finally:
                ancestors.remove(canonical)
        else:
            if path.stat().st_size > 100 * 1024 * 1024:
                raise ValueError("Verification input exceeds 100 MiB")
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()

    for name in names:
        visit(name)
    if not result:
        raise ValueError("Verification input list contains no files")
    return dict(sorted(result.items()))


def snapshot(root):
    """Read configuration and identities without importing the host or secrets."""
    root = Path(root).resolve()
    config = yaml.safe_load(_path(root, "verification.yaml").read_text())
    required = {"version", "evaluator", "evaluator_files", "subject_files", "suite"}
    if (not isinstance(config, dict) or not required <= config.keys()
            or config.keys() - required - {"profile", "timeout_seconds", "policy", "contracts", "request_costs"}
            or type(config["version"]) is not int or config["version"] != 1):
        raise ValueError("verification.yaml needs version, evaluator, evaluator_files, subject_files and suite")
    if not isinstance(config["evaluator"], str) or not re.fullmatch(
            r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", config["evaluator"]):
        raise ValueError("evaluator must name a host module:function")
    if "profile" in config and not isinstance(config["profile"], str):
        raise ValueError("profile must name an execution profile")
    name, entry = execution.profile(root, config.get("profile"))
    execution.require_local(root, name)
    timeout = config.get("timeout_seconds", 120)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
        raise ValueError("timeout_seconds must be between 1 and 600")
    policy = config.get("policy", {})
    if not isinstance(policy, dict) or policy.keys() - {
            "min_pass_rate", "max_latency_ratio", "max_cost_ratio"}:
        raise ValueError("Unknown verification comparison policy")
    for key, value in policy.items():
        evaluation._number(value, key)
        if key == "min_pass_rate" and value > 1:
            raise ValueError("min_pass_rate must be <= 1")
    if type(config.get("request_costs", False)) is not bool:
        raise ValueError("request_costs must be a boolean")
    subject = _files(root, config["subject_files"])
    # These inputs are required even when omitted from the author's file list.
    subject.update(_files(root, ["agent.yaml", "execution.yaml", "pyproject.toml", "uv.lock"]))
    agent = yaml.safe_load((root / "agent.yaml").read_text()) or {}
    if not isinstance(agent, dict):
        raise ValueError("agent.yaml must contain a mapping")
    authoring, skills = agent.get("authoring") or {}, agent.get("skills") or {}
    if not isinstance(authoring, dict) or not isinstance(skills, dict):
        raise ValueError("Agent authoring and skills must be mappings")
    sources = authoring.get("skills") or {}
    if not isinstance(sources, dict):
        raise ValueError("Native skill source mappings must be a mapping")
    mapped = list(sources.values())
    if skills.get("include"):
        mapped.append(skills.get("dir") or "skills")
    if mapped:
        subject.update(_files(root, mapped))
    evaluator = _files(root, config["evaluator_files"])
    module_path = config["evaluator"].split(":")[0].replace(".", "/")
    if not {module_path + ".py", module_path + "/__init__.py"} & evaluator.keys():
        raise ValueError("evaluator_files must include the evaluator module")
    suite_path = _path(root, config["suite"])
    suite = json.loads(suite_path.read_bytes(), object_pairs_hook=evaluation._unique_pairs)
    if (not isinstance(suite, dict) or set(suite) != {"cases", "repetitions"}
            or type(suite["repetitions"]) is not int or not 1 <= suite["repetitions"] <= 100
            or not isinstance(suite["cases"], list) or not 1 <= len(suite["cases"]) <= 1000):
        raise ValueError("Suite needs 1–1000 cases and 1–100 repetitions")
    from adkl.contract_probes import CONTRACTS

    def contract_names(value):
        if (not isinstance(value, list) or any(not isinstance(v, str) or v not in CONTRACTS for v in value)
                or len(set(value)) != len(value)):
            raise ValueError("contracts must be a list of unique supported contract names")

    contract_names(config.get("contracts", []))
    ids = []
    for case in suite["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"].strip():
            raise ValueError("Every case needs a nonempty id")
        contract_names(case.get("adkl_contracts", []))
        ids.append(case["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("Suite case ids must be unique")
    identity = {"subject_files": subject, "evaluator_files": evaluator,
                "suite_sha256": _hash(suite), "profile": name,
                "evaluator": config["evaluator"], "policy": policy,
                "timeout_seconds": timeout}
    # Lock changes can change the grader too: require a new baseline.
    identity["evaluator_sha256"] = _hash({"files": evaluator, "callable": config["evaluator"],
        "lock": subject["uv.lock"], "execution": subject["execution.yaml"], "profile": name})
    identity["artifact_sha256"] = _hash(subject)
    identity["configuration_sha256"] = _hash(config)
    return {"identity": identity, "suite": suite, "entry": entry,
            "contracts": config.get("contracts", []),
            "request_costs": config.get("request_costs", False)}


def _storage(root):
    root = Path(root).resolve()
    path = root
    for part in (".adkl", "verification"):
        path /= part
        if path.is_symlink():
            raise ValueError("Verification storage must not traverse symlinks")
    return path


def _read(path):
    if path.is_symlink():
        raise ValueError("Verification evidence must not be a symlink")
    return json.loads(path.read_bytes(), object_pairs_hook=evaluation._unique_pairs)


def _write(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _record(folder, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid verification run identity")
    return _read(folder / f"{run_id}.json")


def changes(before, after):
    return [{"path": name, "change": "added" if name not in before else
             "removed" if name not in after else "modified"}
            for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]


def status(root):
    root = Path(root).resolve()
    if not (root / "verification.yaml").exists():
        return {"state": "unconfigured", "message": "Configure a host evaluator to verify saved skill changes."}
    try:
        current = snapshot(root)
        folder = _storage(root)
        state = _read(folder / "state.json") if (folder / "state.json").exists() else {}
        result = {"state": "not_run", "configured": True, "profile": current["identity"]["profile"],
                  "baseline": state.get("baseline"), "latest": state.get("latest")}
        if state.get("baseline"):
            baseline = _record(folder, state["baseline"])
            result["changes"] = changes(baseline["identity"]["subject_files"], current["identity"]["subject_files"])
        if state.get("latest"):
            latest = _record(folder, state["latest"])
            result.update({"state": latest["state"], "mode": latest["mode"],
                           "comparison": latest.get("comparison"), "message": latest.get("message")})
            if latest["identity"] != current["identity"]:
                result.update(state="stale", message="Saved inputs changed. Run verification again.")
        if (folder / "running").exists():
            result.update(state="running", comparison=None, latest=None,
                          message="Running the configured evaluator against saved inputs.")
        return result
    except (ValueError, OSError, execution.ExecutionError, yaml.YAMLError, KeyError, TypeError):
        return {"state": "unavailable", "message": "Verification configuration or evidence is invalid. Check the CLI for details."}


def run(root, mode):
    """Execute every declared observation in a fresh worker; retain failures too."""
    if mode not in {"baseline", "candidate"}:
        raise ValueError("Verification mode must be baseline or candidate")
    root = Path(root).resolve()
    before = snapshot(root)
    identity = before["identity"]
    folder = _storage(root)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = folder / "running"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("Verification is already running; inspect an interrupted CLI run before removing its running directory") from None
    worker = None
    try:
        state = _read(folder / "state.json") if (folder / "state.json").exists() else {}
        run_id = uuid.uuid4().hex
        record = {"version": 1, "id": run_id, "mode": mode, "identity": identity,
                  "baseline": state.get("baseline"), "state": "failed"}
        try:
            if mode == "candidate" and not state.get("baseline"):
                raise ValueError("Record a baseline before verifying saved changes")
            baseline = _record(folder, state.get("baseline")) if mode == "candidate" else None
            if baseline:
                for key in ("suite_sha256", "evaluator_sha256"):
                    if baseline["identity"][key] != identity[key]:
                        raise ValueError("Suite or evaluator changed; record a new baseline before comparing")
            worker = execution.Worker(root, identity["profile"], timeout=identity["timeout_seconds"])
            response = worker.request("verify", identity=identity)
            report = response["report"]
            evaluation.validate(report)
            expected = {(case["id"], n) for case in before["suite"]["cases"]
                        for n in range(1, before["suite"]["repetitions"] + 1)}
            if evaluation.validate(report) != expected or report["subject"] != {
                    "revision": identity["artifact_sha256"], "artifact_sha256": identity["artifact_sha256"]}:
                raise ValueError("Evaluator returned incomplete or mismatched observations")
            for key in ("suite_sha256", "evaluator_sha256"):
                if report[key] != identity[key]:
                    raise ValueError("Evaluator returned a different suite or evaluator identity")
            if snapshot(root)["identity"] != identity:
                raise ValueError("Declared inputs changed during verification; run again")
            if before["request_costs"]:
                from adkl.request_costs import validate_records
                validate_records(response.get("request_costs"), expected)
                record["request_costs"] = response["request_costs"]
            record["report"] = report
            record["execution"] = response["execution"]
            comparison = evaluation.compare(baseline["report"] if baseline else report,
                                            report, **identity["policy"])
            record["comparison"] = comparison
            record["state"] = ("baseline_recorded" if mode == "baseline" else
                               "passed" if comparison["passed"] else "regressed")
            if mode == "baseline":
                state["baseline"] = run_id
        except Exception as exc:
            # Host exception text never crosses the worker boundary. Local error
            # messages identify configuration failures without storing transcripts.
            record["message"] = (str(exc) if isinstance(exc, (ValueError, OSError, execution.ExecutionError,
                KeyError, TypeError, yaml.YAMLError)) else f"{type(exc).__name__} during verification; inspect the host setup")
        finally:
            if worker is not None:
                worker.close()
        _write(folder / f"{run_id}.json", record)
        state["latest"] = run_id
        _write(folder / "state.json", state)
        return record
    finally:
        lock.rmdir()


async def evaluate(root, identity):
    """Private worker operation. The host returns measured, graded observations."""
    import sys
    current = snapshot(root)
    if current["identity"] != identity:
        raise ValueError("Verification inputs changed before execution")
    sys.path.insert(0, str(root))
    module, name = identity["evaluator"].split(":")
    imported = importlib.import_module(module)
    origin = Path(imported.__file__).resolve().relative_to(root).as_posix()
    if origin not in identity["evaluator_files"]:
        raise ValueError("Evaluator imported from outside its declared files")
    callback = getattr(imported, name)
    results = []
    costs = []
    for case in current["suite"]["cases"]:
        for repetition in range(1, current["suite"]["repetitions"] + 1):
            observation = callback(case=case, repetition=repetition)
            if inspect.isawaitable(observation):
                observation = await observation
            fields = ("passed", "latency_ms", "cost_usd")
            evaluation._fields(observation, (*fields, "request_costs") if current["request_costs"]
                               else fields, "host observation")
            if current["request_costs"]:
                from adkl.request_costs import validate
                costs.append({"case_id": case["id"], "repetition": repetition,
                              "requests": validate(observation["request_costs"])})
            results.append({**{key: observation[key] for key in fields},
                            "case_id": case["id"], "repetition": repetition})
    report = {"version": 1, "subject": {"revision": identity["artifact_sha256"],
              "artifact_sha256": identity["artifact_sha256"]}, "results": results,
              "suite_sha256": identity["suite_sha256"], "evaluator_sha256": identity["evaluator_sha256"]}
    evaluation.validate(report)
    return {"report": report, **({"request_costs": costs} if current["request_costs"] else {})}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "baseline", "candidate"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = status(args.root) if args.command == "status" else run(args.root, args.command)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result["state"] in {"passed", "baseline_recorded", "not_run", "unconfigured"} else 1
    except (ValueError, OSError, execution.ExecutionError, yaml.YAMLError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
