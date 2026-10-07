#!/usr/bin/env python3
"""adkl serve — HTTP API behind the Flutter console.

stdlib http.server only: no fastapi/uvicorn install, so `python3 -m adkl.server`
works on a clean machine. Not a production server — threaded with serialized
workspace operations, dev-only, and it says so on startup.

The invariant this enforces: **text on disk is canonical.** Every read parses
the SKILL.md / agent.yaml tree fresh, and every write goes to those files and is
immediately re-linted. There is no server-side model of a skill that could drift
from the file. If the console can express something the validator rejects, the
write is refused rather than persisted — that is what keeps this from becoming a
one-way door.

Endpoints
  GET  /api/health
  GET  /api/agent                     agent.yaml + validation (?env=uat)
  GET  /api/catalog                   tool catalog for the picker
  GET  /api/skills                    skill list w/ per-skill token cost + findings
  GET  /api/skills/{name}             frontmatter, body, declared tools, findings
  PUT  /api/skills/{name}             save; lints first, refuses on ERROR
  POST /api/skills/{name}/lint        dry-run lint of unsaved buffer
  GET  /api/agents                    every agent found, and which is selected
  POST /api/agents/select             switch agents: {id} -> {selected, agent}
  GET  /api/chat                      preflight + history
  POST /api/chat                      one turn: {text} -> {reply, trace}
  POST /api/chat/clear                reset the conversation
"""
from __future__ import annotations

import argparse
import atexit
import json
import pathlib
import re
import sys
import textwrap
import traceback
import urllib.parse
from http.server import ThreadingHTTPServer

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import agent_schema, features, instruction, skill_doc, skill_lint  # noqa: E402
from adkl import capabilities, execution  # noqa: E402
from adkl import _server_routes  # noqa: E402
from adkl._server_http import JsonHandler, RequestError  # noqa: E402

from adkl._server_workspace import ServerWorkspace  # noqa: E402

# Internal process-wide owner. The CLI and HTTP contract remain single-user.
_workspace = ServerWorkspace()


def reset_chat():
    _workspace.reset_chat()


atexit.register(reset_chat)

# Scan depth for agent.yaml. Two levels reaches `workspace/` and `projects/*/`
# from the repo root; deeper would pick up test fixtures and generated output and
# offer them as if they were real agents.
_SCAN_DEPTH = 2


def discover_agents(base: pathlib.Path) -> list[dict]:
    """Find every agent under `base`, nearest first.

    Reads only what the picker needs, and never raises on a broken agent.yaml: a
    misconfigured agent should still be listed so the UI can show it as broken.
    "I can see it is misconfigured" and "my agent vanished" are very different
    experiences, and only one of them is debuggable.
    """
    found: list[dict] = []
    seen: set = set()
    base = base.resolve()

    candidates = [base / "agent.yaml"]
    for depth in range(1, _SCAN_DEPTH + 1):
        candidates.extend(
            sorted(base.glob("/".join(["*"] * depth) + "/agent.yaml")))

    for path in candidates:
        if not path.is_file():
            continue
        root = path.parent.resolve()
        if root in seen:
            continue
        seen.add(root)

        entry = {
            "id": "." if root == base else str(root.relative_to(base)),
            "path": str(root),
            "name": root.name,
            "skills": 0,
            "model": None,
            "error": None,
        }
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            agent = doc.get("agent") or {}
            entry["name"] = agent.get("name") or root.name
            entry["model"] = agent.get("model")
            entry["skills"] = len(((doc.get("skills") or {}).get("include") or []))
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        found.append(entry)
    return found


def select_agent(agent_id: str) -> dict | None:
    """Select a discovered agent and discard its predecessor's conversation."""
    return _workspace.select_agent(agent_id)


# ------------------------------------------------------------------ helpers

def _load_agent(env: str | None):
    path = _workspace.root / "agent.yaml"
    if not path.is_file():
        return None, [f"agent.yaml not found in {_workspace.root}"], []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"invalid YAML: {exc}"], []
    errors, warnings = agent_schema.validate(doc, _workspace.root, env)
    return doc, errors, warnings


def _skills_dir(doc: dict | None) -> pathlib.Path:
    return _workspace.root / (((doc or {}).get("skills") or {}).get("dir") or "skills")


def _catalog(doc: dict | None) -> dict:
    from adkl.presentation import extend_catalog
    rel = ((doc or {}).get("tools") or {}).get("catalog")
    if not rel:
        return extend_catalog({}, doc or {})
    p = _workspace.root / rel
    if not p.is_file():
        return {}
    try:
        return extend_catalog(json.loads(p.read_text(encoding="utf-8")), doc or {})
    except ValueError:
        return {}


def _include(doc: dict | None, env: str | None) -> dict[str, str]:
    """Logical skill name -> relative dir, after applying env overrides."""
    skills = (doc or {}).get("skills") or {}
    active = {n: n for n in (skills.get("include") or [])}
    if env:
        for logical, replacement in ((skills.get("overrides") or {}).get(env) or {}).items():
            if logical in active:
                active[logical] = replacement
    return active


def _lint(doc: dict | None, env: str | None, known: set[str],
          override: tuple[str, str] | None = None):
    """Lint the agent's active skill set. `override` injects an unsaved buffer.

    Reuses skill_lint's rule functions rather than reimplementing them, so the
    console and CI can never disagree about what is a finding.

    A host-factory descriptor declares no skills, tools or instruction template
    on disk -- the factory owns them in Python -- so there is nothing here to
    lint. Returning early also keeps `_read_prompt` from compiling an
    `instruction` block the inspection schema forbids, which raised KeyError
    and took /api/skills (and with it the whole console load) down with it.
    """
    if ((doc or {}).get("agent") or {}).get("factory"):
        return []
    sdir = _skills_dir(doc)
    active = _include(doc, env)
    findings: list[dict] = []
    parsed = []

    for logical, rel in sorted(active.items()):
        md = sdir / rel / "SKILL.md"
        if override and override[0] == logical:
            fm, body = skill_lint.parse_frontmatter_text(override[1])
        elif md.is_file():
            fm, body = skill_lint.parse_frontmatter(md)
        else:
            findings.append({"severity": "ERROR", "rule": "SKILL_MISSING",
                             "skill": logical, "message": f"{rel}/SKILL.md not found"})
            continue
        for sev, rule, msg in skill_lint.check_one(logical, fm, body, known):
            findings.append({"severity": sev, "rule": rule, "skill": logical,
                             "message": msg})
        if "__yaml_error__" not in fm:
            declared = (fm.get("metadata") or {}).get("adk_additional_tools") or []
            parsed.append((logical, md.parent, fm, body,
                           list(declared) if isinstance(declared, list) else []))

    # These need the complete selected skill set, catalog, and prompt. Calling the
    # shared function is what keeps unsaved console buffers and `adkl check`
    # consistent instead of giving authors two different answers.
    prompt = skill_lint._read_prompt(_workspace.root)
    for sev, rule, skill, message in skill_lint.cross_layer_findings(
            parsed, _catalog(doc), prompt, ((doc or {}).get("tools") or {}).get("core") or []):
        findings.append({"severity": sev, "rule": rule, "skill": skill,
                         "message": message})
    return findings


def _tool_cost(catalog: dict) -> dict[str, int]:
    return {t["name"]: t.get("estimated_input_tokens_per_turn", 0)
            for t in catalog.get("tools", [])}


def _desc_block(md: pathlib.Path) -> str | None:
    """Raw indented text between `description: >` and the next top-level key.

    Returned verbatim so an unchanged description can be written back
    byte-identical instead of re-wrapped.
    """
    if not md.is_file():
        return None
    m = re.search(r"^description:\s*[>|][-+]?\s*\n((?:[ \t]+.*\n|\n)+)",
                  md.read_text(encoding="utf-8", errors="replace"), re.M)
    return m.group(1) if m else None


def skill_doc_slot(name: str, path: str):
    """Shorthand for the simple `SLOT: path.md` form."""
    return instruction.Slot(name, pathlib.Path(path))


def _declared(fm: dict) -> list[str]:
    tools = (fm.get("metadata") or {}).get("adk_additional_tools") or []
    return list(tools) if isinstance(tools, list) else []


def declared_feature_config(doc: dict | None) -> dict:
    """The features block AS WRITTEN, minus `pipeline`. What an editor writes back.

    `features.resolve().config` is the wrong thing to round-trip and the console
    was round-tripping it: it is defaults-merged and drops `enabled`, so writing
    it back laundered every inherited default into a declared one -- freezing the
    project against future registry changes -- and erased `enabled: false` ops
    kill switches outright. `pipeline` is excluded because the toggle that reads
    this owns the pipeline itself and supplies a fresh one.
    """
    return {k: v for k, v in (((doc or {}).get("features")) or {}).items()
            if k != "pipeline"}


# ------------------------------------------------------------------ handler

class Handler(JsonHandler):
    """Workspace endpoints behind the local-origin and serialized-access guard."""

    # -- routing
    def _guard(self, fn):
        """Turn an unhandled exception into a 500 with a traceback.

        Without this, any bug drops the TCP connection mid-response and the
        browser reports nothing useful -- which is exactly how the blank-page
        bug hid: three assets were failing, but curl showed only `000`.
        """
        try:
            origin = self.headers.get("Origin")
            host = urllib.parse.urlparse("http://" + self.headers.get("Host", "localhost")).hostname
            if host not in {"localhost", "127.0.0.1", "::1"} or (
                origin and urllib.parse.urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                return self._send(403, {"error": "Use the local console origin"})
            if getattr(self, "command", None) in {"POST", "PUT"}:
                # Network reads must not hold the selected-workspace lock.
                self._body()
            with _workspace.lock:
                return fn()
        except RequestError as exc:
            self.close_connection = True
            self._send(exc.status, {"error": str(exc)})
        except execution.ExecutionError as exc:
            self._send(exc.status, exc.payload())
        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            try:
                self._send(500, {"error": "server exception", "traceback": tb})
            except Exception:
                pass

    def do_GET(self):
        self._guard(self._route_get)

    def _route_get(self):
        return _server_routes.get(self)

    def _health(self):
        return self._send(200, {"ok": True, "root": str(_workspace.root)})

    def _project_status(self):
        from adkl import project_jobs, verification
        return self._send(200, {"path": str(_workspace.root), "projectsPath": str(_workspace.projects),
            "exists": (_workspace.root / "agent.yaml").is_file(),
            "prepared": (_workspace.root / ".venv/bin/python").is_file() and (_workspace.root / "uv.lock").is_file(),
            "canPrepare": _workspace.sdk_wheel is not None,
            "job": project_jobs.status(_workspace.root), "verification": verification.status(_workspace.root)})

    def _project_artifact(self):
        from adkl import project_jobs
        job = project_jobs.status(_workspace.root)
        if job.get("state") != "succeeded" or not job.get("artifact"):
            return self._send(404, {"error": "No completed release bundle"})
        artifact = execution.inside(_workspace.root, ".adkl/releases/" + job["artifact"])
        return self._send(200, artifact.read_bytes(), "application/zip")

    def _audit(self):
        from adkl.audit import audit
        return self._send(200, audit(_workspace.root))

    def _findings(self, env):
        native = self._native_workspace(browse=True)
        if native:
            return self._send(200, {"findings": native.diagnostic_findings(),
                                    "context": {"source": "workspace", "inspection": "native_declarations"}})
        doc, _e, _w = _load_agent(env)
        return self._send(200, {"findings": _lint(doc, env, {t["name"] for t in _catalog(doc).get("tools", [])}),
                                "context": {"source": "workspace", "environment": env}})

    def do_PUT(self):
        self._guard(self._route_put)

    def _route_put(self):
        return _server_routes.put(self)

    def do_POST(self):
        self._guard(self._route_post)

    def _route_post(self):
        return _server_routes.post(self)

    def _project_post(self, path):
        # Local UI/CLI only: do not accept cross-site web pages triggering package installs.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return self._send(403, {"error": "Project operations require a local console origin"})
        try:
            from adkl import projects, project_jobs
            if path == "/api/project/doctor":
                from adkl import doctor
                body = self._body()
                inspect = body.get("inspect", False)
                selected = body.get("selected")
                if not isinstance(inspect, bool) or (selected is not None and (
                        not isinstance(selected, list) or any(not isinstance(v, str) for v in selected))):
                    raise ValueError("inspect must be boolean and selected must be a list of finding IDs")
                report = doctor.diagnose(_workspace.root, inspect=inspect, profile=_workspace.profile, previous=body.get("previous"))
                return self._send(200, {"report": report, "brief": doctor.render(report, selected)})
            if path == "/api/project/credentials":
                import os
                key = self._body().get("key")
                if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(c in key for c in "\r\n\x00"):
                    raise ValueError("Enter a nonempty API key on one line")
                _, profile = execution.profile(_workspace.root, _workspace.profile)
                if profile["target"] != "local" or "GOOGLE_API_KEY" not in profile.get("secrets", []):
                    raise ValueError("This profile does not accept a local Google API key")
                path = execution.inside(_workspace.root, profile.get("env_file", ".env"))
                if path.is_symlink():
                    raise ValueError("Credential file must not be a symlink")
                prior = path.read_text() if path.exists() else ""
                lines = [line for line in prior.splitlines() if not line.strip().startswith("GOOGLE_API_KEY=")]
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write("\n".join([*lines, "GOOGLE_API_KEY=" + key.strip()]) + "\n")
                reset_chat()
                return self._send(200, {"saved": True})
            if path == "/api/agents":
                if _workspace.projects is None:
                    raise ValueError("Project creation workspace is not configured")
                body = self._body()
                root = projects.create(_workspace.projects, body.get("name"), body.get("purpose"),
                                       model=body.get("model", "gemini-3.6-flash"))
                entry = discover_agents(root)[0]
                entry["id"] = "created/" + root.name
                _workspace.agents.append(entry)
                select_agent(entry["id"])
                return self._send(201, {"selected": entry["id"], "path": str(root)})
            if path == "/api/project/inspect":
                worker = self._chat()
                status = worker.request("status")
                if not status.get("available"):
                    raise ValueError("; ".join(status.get("problems") or ["Runtime unavailable"]))
                return self._send(200, worker.request("inspect"))
            if path in {"/api/project/baseline", "/api/project/candidate"}:
                if self._query().get("env"):
                    raise ValueError("Verification uses its configured execution profile; switch the editor to base configuration")
                body = self._body()
                if body:
                    raise ValueError("Verification executes only the evaluator configured in verification.yaml")
            reset_chat()
            return self._send(202, project_jobs.start(_workspace.root, path.rsplit("/", 1)[-1], _workspace.sdk_wheel))
        except execution.ExecutionError as exc:
            return self._send(exc.status, exc.payload())
        except (ValueError, OSError) as exc:
            return self._send(409, {"error": str(exc)})

    # -- endpoints
    def _agent(self, env):
        doc, errors, warnings = _load_agent(env)

        # Compile the instruction so the console can show the resolved prompt
        # and its state requirements without needing a live session.
        compiled = None
        inst = ((doc or {}).get("agent") or {}).get("instruction") or {}
        if inst.get("template"):
            try:
                slots = []
                for nm, spec in (inst.get("slots") or {}).items():
                    if isinstance(spec, str):
                        slots.append(skill_doc_slot(nm, spec))
                    else:
                        slots.append(instruction.Slot(
                            nm,
                            variants={k: pathlib.Path(v) for k, v
                                      in (spec.get("variants") or {}).items()},
                            select=spec.get("select")))
                c = instruction.compile_instruction(
                    _workspace.root / inst["template"], _workspace.root, slots)
                compiled = {
                    "text": c.text,
                    "chars": len(c.text),
                    "estTokens": len(c.text) // 4,
                    "slotsFilled": c.slots_filled,
                    "requiredState": c.required_state,
                    "optionalState": c.optional_state,
                    "passthrough": c.passthrough,
                }
            except Exception as exc:   # surfaced, not swallowed
                compiled = {"error": str(exc)}

        try:
            resolved = features.resolve((doc or {}).get("features"))
            feature_state = {
                "active": resolved.active,
                "declared": resolved.declared,
                "config": resolved.config,
                "declaredConfig": declared_feature_config(doc),
                "warnings": resolved.warnings,
                "runtime": capabilities.manifest(resolved),
                "export": capabilities.export_report(doc or {}),
                "error": None,
            }
        except features.FeatureError as exc:
            feature_state = {"active": [], "declared": [], "config": {},
                             "declaredConfig": {}, "warnings": [], "error": str(exc)}

        if ((doc or {}).get("agent") or {}).get("factory"):
            feature_state = {"active": [], "declared": [], "config": {},
                             "declaredConfig": {}, "warnings": [],
                             "error": "Host factory owns runtime settings. Inspect the running factory in Project; adopt shared callbacks in Python."}

        return self._send(200, {
            "agent": (doc or {}).get("agent", {}),
            "retrieval": (doc or {}).get("retrieval", {}),
            "generation": ((doc or {}).get("agent") or {}).get("generation", {}),
            "mode": "factory" if ((doc or {}).get("agent") or {}).get("factory") else "declarative",
            "skillCount": None if ((doc or {}).get("agent") or {}).get("factory") else len(_include(doc, env)),
            "toolCount": None if ((doc or {}).get("agent") or {}).get("factory") else len(_catalog(doc).get("tools", [])),
            "instruction": inst,
            "compiled": compiled,
            "featureRegistry": features.describe(),
            "features": feature_state,
            "env": env,
            "environments": sorted(((doc or {}).get("skills") or {})
                                   .get("overrides", {}).keys()),
            "validation": {"errors": errors, "warnings": warnings},
        })

    def _catalog_route(self):
        native = self._native_workspace(browse=True)
        if native:
            return self._send(200, native.catalog())
        doc, _e, _w = _load_agent(None)
        cat = _catalog(doc)
        tools = [{
            "name": t["name"],
            "summary": t.get("summary", ""),
            "sideEffect": (t.get("side_effect") or {}).get("class", "unknown"),
            "sideEffectInferred": (t.get("side_effect") or {}).get("inferred", True),
            "requiresAuth": (t.get("requires_auth") or {}).get("value", False),
            "estTokens": t.get("estimated_input_tokens_per_turn", 0),
            "params": [{"name": p["name"], "type": p.get("type", ""),
                        "required": p.get("required", False),
                        "description": p.get("description", "")}
                       for p in t.get("params", [])],
        } for t in cat.get("tools", [])]
        return self._send(200, {"tools": sorted(tools, key=lambda x: x["name"]),
                                "warnings": cat.get("extraction_warnings", [])})

    def _workflow_list(self):
        native = self._native_workspace(browse=True)
        return self._send(200, {"workflows": native.workflows if native else []})

    def _skill_list(self, env):
        native = self._native_workspace(browse=True)
        if native:
            return self._send(200, {"skills": [native.detail(name) for name in native.skills], "env": env})
        doc, _e, _w = _load_agent(env)
        known = {t["name"] for t in _catalog(doc).get("tools", [])}
        findings = _lint(doc, env, known)
        by_skill: dict[str, list] = {}
        for f in findings:
            by_skill.setdefault(f["skill"], []).append(f)

        sdir = _skills_dir(doc)
        out = []
        for logical, rel in sorted(_include(doc, env).items()):
            md = sdir / rel / "SKILL.md"
            fm, _body = skill_lint.parse_frontmatter(md) if md.is_file() else ({}, "")
            declared = _declared(fm)
            fs = by_skill.get(logical, [])
            out.append({
                "name": logical,
                "path": rel,
                "overridden": rel != logical,
                "description": (fm.get("description") or "").strip(),
                "tools": declared,
                "estTokens": len(_body) // 4,
                "errors": sum(1 for f in fs if f["severity"] == "ERROR"),
                "warnings": sum(1 for f in fs if f["severity"] == "WARN"),
            })
        return self._send(200, {"skills": out, "env": env})

    def _skill_get(self, name, env):
        native = self._native_workspace(browse=True)
        if native:
            if name not in native.skills:
                return self._send(404, {"error": "Native skill not found"})
            return self._send(200, native.detail(name))
        doc, _e, _w = _load_agent(env)
        rel = _include(doc, env).get(name)
        if rel is None:
            return self._send(404, {"error": f"{name!r} is not in skills.include"})
        md = _skills_dir(doc) / rel / "SKILL.md"
        if not md.is_file():
            return self._send(404, {"error": f"{rel}/SKILL.md not found"})

        text = md.read_text(encoding="utf-8")
        fm, body = skill_lint.parse_frontmatter(md)
        known = {t["name"] for t in _catalog(doc).get("tools", [])}
        declared = _declared(fm)
        return self._send(200, {
            "name": name,
            "path": rel,
            "overridden": rel != name,
            "raw": text,
            "description": (fm.get("description") or "").strip(),
            "body": body.strip(),
            # Structured view so the console can offer one field per section
            # instead of a raw-markdown textarea. `body` stays for the eject
            # path and for anything the parser does not model.
            "doc": skill_doc.SkillDoc.parse(body).to_json(),
            "tools": declared,
            "estTokens": len(body) // 4,
            "findings": [f for f in _lint(doc, env, known) if f["skill"] == name],
        })

    def _compose(self, name: str, description: str, tools: list[str], body: str,
                 prior_desc_block: str | None = None,
                 extra_metadata: dict | None = None) -> str:
        """Rebuild SKILL.md from console fields.

        Block scalar (`>`) for description, and re-wrapped to ~76 columns.

        Both details are load-bearing for reviewability. YAML folds a `>` block
        into one logical string on read, so a naive write-back collapses a
        10-line description into a single 900-character line -- every future
        edit then shows as one whole-line diff, which makes review useless.
        Caught by round-tripping a real skill through the API.
        """
        desc = " ".join((description or "").split())
        # If the text is unchanged, reuse the exact original block. Re-wrapping
        # identical content shifts line breaks and shows as a description edit
        # that never happened.
        if prior_desc_block is not None and \
                " ".join(prior_desc_block.split()) == desc:
            indented = prior_desc_block.rstrip("\n")
        else:
            indented = "\n".join(f"  {ln}" for ln in textwrap.wrap(desc, width=76)) \
                if desc else "  "
        lines = [
            "---",
            f"name: {name}",
            "description: >",
            indented,
            "metadata:",
            "  adk_additional_tools:",
        ]
        lines += [f"    - {t}" for t in tools] or ["    []"]
        # Preserve metadata keys this writer does not model. prospect-public-qa
        # carries `adk_inject_state: true`; rebuilding frontmatter from a fixed
        # template dropped it, silently changing runtime behaviour.
        for k, v in sorted((extra_metadata or {}).items()):
            lines.append(f"  {k}: {json.dumps(v) if not isinstance(v, str) else v}")
        # The body is emitted verbatim -- SkillDoc.render() already preserves the
        # blank lines after the frontmatter fence and whether the file ended with
        # a newline. Stripping and re-padding here undid that work and showed as
        # a diff on the first and last lines of every save.
        return "\n".join(lines + ["---"]) + (body or "")

    def _skill_put(self, name, env):
        payload = self._body()
        native = self._native_workspace()
        if native:
            try:
                result = native.edit(name, payload, save=True)
                if result.get("saved"):
                    reset_chat()
                return self._send(200 if result.get("saved") else 409, result)
            except (ValueError, KeyError) as exc:
                return self._send(409, {"saved": False, "error": str(exc)})
        doc, _e, _w = _load_agent(env)
        rel = _include(doc, env).get(name)
        if rel is None:
            return self._send(404, {"error": f"{name!r} is not in skills.include"})

        # Preserve the on-disk order of tools the user kept, appending only what
        # is new. Alphabetizing would rewrite every line of an 11-tool list to
        # add one, burying the real change in the diff.
        md_existing = _skills_dir(doc) / rel / "SKILL.md"
        prior: list[str] = []
        if md_existing.is_file():
            prior = _declared(skill_lint.parse_frontmatter(md_existing)[0])
        requested = payload.get("tools")
        if isinstance(requested, list):
            keep = [t for t in prior if t in requested]
            payload["tools"] = keep + [t for t in requested if t not in keep]

        body_text = payload.get("body", "")
        if payload.get("doc") is not None:
            # Re-render from structure. Diffed against the on-disk parse so a
            # section the user did not touch is written back byte-identical --
            # otherwise every save reflows the whole file and review dies.
            prior_body = skill_lint.parse_frontmatter(md_existing)[1] \
                if md_existing.is_file() else ""
            original_doc = skill_doc.SkillDoc.parse(prior_body)
            body_text = skill_doc.SkillDoc.from_json(
                payload["doc"], original_doc).render()

        prior_fm = skill_lint.parse_frontmatter(md_existing)[0] \
            if md_existing.is_file() else {}
        extra = {k: v for k, v in (prior_fm.get("metadata") or {}).items()
                 if k != "adk_additional_tools"}
        text = payload.get("raw") or self._compose(
            name, payload.get("description", ""),
            payload.get("tools") or [], body_text,
            prior_desc_block=_desc_block(md_existing),
            extra_metadata=extra)

        known = {t["name"] for t in _catalog(doc).get("tools", [])}
        findings = _lint(doc, env, known, override=(name, text))
        mine = [f for f in findings if f["skill"] == name]
        errors = [f for f in mine if f["severity"] == "ERROR"]

        # Refuse to persist a state the validator rejects. The console shows the
        # reason; the file on disk stays valid.
        if errors and not payload.get("force"):
            return self._send(409, {"saved": False, "findings": mine,
                                    "error": "refused: lint errors (send force:true to override)"})

        md = _skills_dir(doc) / rel / "SKILL.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(text, encoding="utf-8")
        reset_chat()
        return self._send(200, {"saved": True, "path": str(md.relative_to(_workspace.root)),
                                "findings": mine,
                                "staleEmbeddings": True})

    # ---------------------------------------------------------------- chat

    # ---------------------------------------------------------------- agents

    def _agents(self):
        """Every agent this server can switch to, and which one is selected."""
        current = str(_workspace.root)
        return self._send(200, {
            "agents": _workspace.agents,
            "selected": next((a["id"] for a in _workspace.agents
                              if a["path"] == current), None),
        })

    def _agents_select(self):
        """Switch agents. Returns the new selection, or 404 for an unknown id.

        A 404 rather than a silent no-op: the console would otherwise show the
        new agent as selected while every endpoint kept serving the old one,
        which is the worst of both -- wrong data under a confident label.
        """
        body = self._body()
        agent_id = (body or {}).get("id")
        if not agent_id:
            return self._send(400, {"error": "id is required"})
        entry = select_agent(agent_id)
        if entry is None:
            return self._send(404, {
                "error": f"unknown agent {agent_id!r}",
                "available": [a["id"] for a in _workspace.agents]})
        # `chatReset` is stated rather than implied: the transcript belonged to
        # the previous agent, and the UI has to say so instead of leaving a reply
        # on screen that the new agent never produced.
        return self._send(200, {"selected": entry["id"], "agent": entry,
                                "chatReset": True})

    def _execution_status(self):
        try:
            doc = execution.profiles(_workspace.root)
            selected = _workspace.profile or doc["default"]
            return self._send(200, {"selected": selected, "profiles": [
                {"name": name, "environment": item["environment"], "target": item["target"]}
                for name, item in doc["profiles"].items()]})
        except execution.ExecutionError as exc:
            return self._send(200, {"profiles": [], "error": str(exc)})

    def _execution_select(self):
        name = self._body().get("profile")
        try:
            _workspace.select_profile(name)
        except execution.ExecutionError as exc:
            return self._send(400, {"error": str(exc)})
        return self._send(200, {"selected": name, "chatReset": True})

    def _chat(self):
        return _workspace.worker()

    def _native_workspace(self, *, browse=False):
        doc, _e, _w = _load_agent(None)
        if not (doc.get("agent") or {}).get("factory"):
            return None
        from adkl.native_authoring import Workspace
        worker = self._chat()
        report = worker.browse_inspection() if browse else worker.request("inspect")
        if "agent" not in report:
            raise ValueError("Native factory inspection unavailable")
        return Workspace(_workspace.root, doc, report)

    def _chat_status(self):
        try:
            return self._send(200, self._chat().request("status"))
        except execution.ExecutionError as exc:
            return self._send(200, {"available": False, "problems": [str(exc)],
                                    "history": [], **exc.payload()})

    def _chat_send(self):
        payload = self._body()
        raw_text = payload.get("text")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        if not text:
            return self._send(400, {"error": "text is required"})
        state = payload.get("state")
        if state is not None and not isinstance(state, dict):
            return self._send(400, {"error": "state must be an object"})
        try:
            doc = yaml.safe_load((_workspace.root / "agent.yaml").read_text()) or {}
            capabilities.require_supported(features.resolve(doc.get("features")))
        except features.FeatureError as exc:
            return self._send(409, {"error": str(exc)})
        if "text/event-stream" in self.headers.get("Accept", ""):
            return self._chat_stream(text, state)
        try:
            return self._send(200, self._chat().request("send", text=text, state=state))
        except execution.ExecutionError as exc:
            return self._send(exc.status, exc.payload())

    def _chat_stream(self, text, state):
        from contextlib import closing
        try:
            worker = self._chat()
            with closing(worker.stream(text, state)) as stream:
                # Validate/start the worker before committing HTTP headers.
                first = next(stream)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.close_connection = True
                # Bound a stalled client's write; heartbeats detect disconnects.
                self.connection.settimeout(5)

                def emit(frame):
                    self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    emit(first)
                    for frame in stream:
                        emit(frame)
                except execution.ExecutionError as exc:
                    emit({"version": 1, "type": "error", **exc.payload()})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except execution.ExecutionError as exc:
            return self._send(exc.status, exc.payload())

    def _chat_clear(self):
        reset_chat()
        return self._send(200, {"cleared": True})

    def _features_put(self):
        """Replace the `features:` block in agent.yaml.

        Validated before writing: the registry rejects a pipeline whose order
        would degrade silently, so the console cannot persist one. Round-tripping
        through ruamel would preserve comments, but PyYAML is the only dependency
        here -- so the block is spliced textually and the rest of the file is
        left byte-identical.
        """
        payload = self._body()
        doc, _, _ = _load_agent(None)
        if ((doc or {}).get("agent") or {}).get("factory"):
            return self._send(409, {"saved": False, "error": "Configure shared callbacks in the host factory; this descriptor is inspection-only."})
        block = payload.get("features")
        if not isinstance(block, dict):
            return self._send(400, {"error": "features must be a mapping"})

        try:
            resolved = features.resolve(block)
        except features.FeatureError as exc:
            return self._send(409, {"saved": False, "error": str(exc)})

        path = _workspace.root / "agent.yaml"
        text = path.read_text(encoding="utf-8")

        rendered = yaml.safe_dump({"features": block}, sort_keys=False,
                                  default_flow_style=False).rstrip() + "\n"
        # Replace the existing top-level `features:` block, or append one.
        pattern = re.compile(r"^features:\n(?:[ \t].*\n|\n)*", re.M)
        if pattern.search(text):
            text = pattern.sub(rendered, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + rendered
        path.write_text(text, encoding="utf-8")
        reset_chat()

        return self._send(200, {"saved": True, "active": resolved.active,
                                "warnings": resolved.warnings,
                                "runtime": capabilities.manifest(resolved),
                                "chatReset": True})

    def _skill_lint_dry(self, name, env):
        payload = self._body()
        native = self._native_workspace()
        if native:
            try:
                return self._send(200, native.edit(name, payload))
            except (ValueError, KeyError) as exc:
                return self._send(409, {"error": str(exc), "findings": []})
        doc, _e, _w = _load_agent(env)
        if name not in _include(doc, env):
            return self._send(404, {"error": f"{name!r} is not in skills.include"})
        body_text = payload.get("body", "")
        if payload.get("doc") is not None:
            rel = _include(doc, env).get(name, name)
            md = _skills_dir(doc) / rel / "SKILL.md"
            prior_body = skill_lint.parse_frontmatter(md)[1] if md.is_file() else ""
            body_text = skill_doc.SkillDoc.from_json(
                payload["doc"], skill_doc.SkillDoc.parse(prior_body)).render()

        text = payload.get("raw") or self._compose(
            name, payload.get("description", ""),
            payload.get("tools") or [], body_text)
        known = {t["name"] for t in _catalog(doc).get("tools", [])}
        cost = _tool_cost(_catalog(doc))
        tools = payload.get("tools") or []
        return self._send(200, {
            "findings": [f for f in _lint(doc, env, known, override=(name, text))
                         if f["skill"] == name],
            "estTokens": sum(cost.get(t, 0) for t in tools),
        })

    # -- static (built Flutter web bundle, if present)
    def _static(self, path):
        if _workspace.web is None:
            return self._send(404, {"error": "no web bundle; run the Flutter dev server"})
        rel = path.lstrip("/") or "index.html"

        # Flutter registers a service worker that caches the whole bundle. On a
        # dev loop that means a rebuilt app keeps serving the previous build even
        # after a hard refresh -- the single most confusing failure mode here,
        # because the code is correct and the screen is stale. Refuse to serve
        # the worker at all so the browser always fetches live files.
        if rel in ("flutter_service_worker.js",):
            return self._send(200, b"// disabled by adkl dev server\n",
                              "application/javascript")

        target = (_workspace.web / rel).resolve()
        if not str(target).startswith(str(_workspace.web.resolve())) or not target.is_file():
            target = _workspace.web / "index.html"
        if not target.is_file():
            return self._send(404, {"error": "not built"})
        ctypes = {".html": "text/html", ".js": "application/javascript",
                  ".css": "text/css", ".json": "application/json",
                  ".png": "image/png", ".ttf": "font/ttf", ".otf": "font/otf",
                  ".wasm": "application/wasm", ".svg": "image/svg+xml"}
        self._send(200, target.read_bytes(),
                   ctypes.get(target.suffix, "application/octet-stream"))


def main() -> int:
    ap = argparse.ArgumentParser(description="adkl console API (dev only).")
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd(),
                    help="workspace containing agent.yaml, or a directory to "
                         "scan for several (see --agents)")
    ap.add_argument("--agents", type=pathlib.Path, default=None,
                    help="directory to scan for agent.yaml files, up to two "
                         "levels deep. Defaults to --root, or its parent when "
                         "--root is itself an agent, so pointing at one agent "
                         "still offers its siblings in the picker.")
    ap.add_argument("--web", type=pathlib.Path, default=None,
                    help="optional built Flutter web bundle to serve")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--projects", type=pathlib.Path, help="Directory for projects created in the UI")
    ap.add_argument("--sdk-wheel", type=pathlib.Path, help="Trusted ADKL wheel used by explicit Prepare runtime jobs")
    args = ap.parse_args()

    _workspace.root = args.root.resolve()
    _workspace.web = args.web.resolve() if args.web else None
    bundled = pathlib.Path(__file__).resolve().parent / "web"
    if _workspace.web is None and (bundled / "index.html").is_file():
        _workspace.web = bundled
    _workspace.sdk_wheel = args.sdk_wheel.resolve() if args.sdk_wheel else None
    if _workspace.sdk_wheel:
        from adkl.release import validate_wheel
        validate_wheel(_workspace.sdk_wheel)

    # Where to look for switchable agents. When --root IS an agent, walk up to
    # the enclosing project so sibling examples and user workspaces appear.
    #
    # "Enclosing project" = nearest ancestor holding a marker. Bounded by
    # _SCAN_DEPTH from there, so this cannot wander up to the home directory.
    root_is_agent = (_workspace.root / "agent.yaml").is_file()
    if args.agents:
        base = args.agents.resolve()
    elif root_is_agent:
        base = _workspace.root.parent
        for ancestor in [_workspace.root.parent, *_workspace.root.parents]:
            if any((ancestor / m).exists()
                   for m in (".git", "run_tests.sh", "adkl")):
                base = ancestor
                break
    else:
        base = _workspace.root
    _workspace.agents = discover_agents(base)
    _workspace.projects = args.projects.resolve() if args.projects else base / "projects"
    known_paths = {a["path"] for a in _workspace.agents}
    if _workspace.projects.is_dir():
        for entry in discover_agents(_workspace.projects):
            if entry["path"] not in known_paths:
                entry["id"] = "created/" + entry["id"]
                _workspace.agents.append(entry)

    if not root_is_agent:
        # --root was a directory to scan. Select the first agent found so the
        # console opens on something rather than an error.
        if not _workspace.agents:
            print(f"Empty workspace: create an agent in the console under {_workspace.projects}")
        else:
            select_agent(_workspace.agents[0]["id"])
    elif not any(a["path"] == str(_workspace.root) for a in _workspace.agents):
        # The scan missed the explicitly requested agent (e.g. --agents points
        # elsewhere). Add it rather than serving an agent nobody asked for.
        _workspace.agents = discover_agents(_workspace.root) + _workspace.agents

    print(f"adkl dev server — NOT for production (single process, no auth)")
    print(f"  workspace: {_workspace.root}")
    print(f"  agents:    {len(_workspace.agents)} found under {base}")
    for a in _workspace.agents:
        mark = "*" if a["path"] == str(_workspace.root) else " "
        broken = f"  [{a['error']}]" if a["error"] else ""
        print(f"    {mark} {a['id']:34} {a['name']:20} "
              f"{a['skills']} skills{broken}")
    print(f"  api:       http://127.0.0.1:{args.port}/api/health")
    if _workspace.web:
        print(f"  web:       http://127.0.0.1:{args.port}/")
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    finally:
        reset_chat()
    return 0


if __name__ == "__main__":
    sys.exit(main())
