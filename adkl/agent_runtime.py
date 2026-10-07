"""Shared native ADK construction for SDK applications and console preview.

ADK imports are deferred until construction; authoring remains lightweight.
"""
from __future__ import annotations

import importlib
import pathlib
import time
from dataclasses import replace
from typing import Any

import yaml

from adkl import features as features_mod
from adkl import capabilities
from adkl import gateway as gw
from adkl import instruction as instruction_mod
from adkl import retrieval as retrieval_mod
from adkl import runtime as runtime_mod
from adkl import skill_lint
from adkl import widgets as widgets_mod
from adkl import presentation

# Injected as a final instruction block during a console turn. Deliberately
# explicit about not claiming a tool ran: a plausible invented lookup is worse
# than an admission, because it is what a reviewer would mistake for real.
TEST_MODE_NOTE = """

<console_test_mode>
Tools are not executable in this session. Answer from general knowledge, and say
plainly when live data (pricing, availability, account state, offers) could not be
looked up. Never claim a tool was called or invent a tool result.
</console_test_mode>"""


from adkl.bindings import NativeLifecycle


class AgentRuntime(NativeLifecycle):
    """Resolved project and native runtime; no development server or session store."""

    def __init__(self, root: pathlib.Path, env: dict | None = None, *, model=None, embedder=None,
                 prepare_skill=None, widget_sink=None, companion_sink=None, request_id=None,
                 seed_today=True):
        if not isinstance(seed_today, bool):
            raise TypeError("seed_today must be a boolean")
        self._seed_today = seed_today
        self.root = root.resolve()
        if (self.root / "execution.yaml").is_file():
            from adkl import execution
            import os
            execution.require_local(self.root, os.environ.get("ADKL_PROFILE"))
        self.doc = yaml.safe_load(
            (root / "agent.yaml").read_text(encoding="utf-8")) or {}
        # The provider is a project property, declared in agent.yaml; the .env
        # beside it carries only the credential.
        self.settings = gw.GatewaySettings.from_env(
            env if env is not None else _read_env(root),
            provider=(self.doc.get("agent") or {}).get("provider"))
        self._agent = None
        self._retrieval = None
        self._last_emit = None
        self._last_resolution = None
        self._skills: dict[str, Any] = {}
        self._callbacks: dict[str, list] = {}
        self._output_findings: list[str] = []
        self._presentation_config = {}
        self._presentation_tools = {}

        for name, hook in (("prepare_skill", prepare_skill), ("widget_sink", widget_sink),
                           ("companion_sink", companion_sink), ("request_id", request_id)):
            if hook is not None and not callable(hook):
                raise TypeError(f"{name} must be callable")
        self._prepare_skill = prepare_skill
        self._widget_sink = widget_sink
        self._companion_sink = companion_sink
        self._request_id = request_id
        self._model_override = model
        self._embedder_override = embedder
        retrieval_options = (self.doc.get("features") or {}).get("skill_retrieval") or {}
        self._embedding_settings = replace(self.settings, **{
            {"dimensions": "embedding_dimensions"}.get(key, key): retrieval_options[key]
            for key in ("embedding_model", "dimensions") if key in retrieval_options})

    def _record_output_findings(self, findings: list[str]) -> None:
        """Keep after-model diagnostics attached to the active turn."""
        self._output_findings.extend(findings)

    # ------------------------------------------------------------------ setup

    def preflight(self) -> list[str]:
        """Problems that would make a turn fail, reported before one is tried."""
        problems: list[str] = []

        try:
            _catalog(self.root, self.doc)
        except (presentation.PresentationError, features_mod.FeatureError) as exc:
            problems.append(str(exc))

        if not self.settings.configured and self._model_override is None:
            where = "Configure this project’s declared secrets"
            problems.append(
                f"provider {self.settings.provider!r} not configured: missing "
                + ", ".join(self.settings.missing())
                + f". {where} in the selected profile's env_file.")

        agent = self.doc.get("agent") or {}
        if not agent.get("model"):
            problems.append("agent.model is not set in agent.yaml")

        try:
            resolved = features_mod.resolve(self.doc.get("features"))
        except features_mod.FeatureError as exc:
            problems.append(f"features: {exc}")
            return problems

        blockers = capabilities.manifest(resolved)["blockers"]
        problems.extend(blockers)
        if blockers:
            return problems

        if "skill_retrieval" in resolved.active:
            cfg = resolved.config["skill_retrieval"]
            vpath = self.root / cfg.get("vectors", "embeddings/skills.json")
            if not vpath.is_file():
                problems.append(
                    f"vector file {vpath.name} not found. Run `adkl embed` -- "
                    f"without it retrieval cannot rank and every skill is "
                    f"exposed.")
            else:
                vf = retrieval_mod.load_vectors(vpath)
                problems.extend(gw.check_vectors(vf, self._embedding_settings))
                missing = [s for s in (self.doc.get("skills") or {}).get(
                    "include", []) if s not in vf.vectors]
                if missing:
                    problems.append(
                        f"no vectors for {', '.join(missing)} -- these skills "
                        f"can never be retrieved. Run `adkl embed`.")
        _callbacks, callback_problems = _load_callbacks(self.root, self.doc)
        problems.extend(callback_problems)
        return problems

    def _build(self) -> None:
        """Construct the agent once, lazily, so preflight can report instead."""
        if (self.doc.get("agent") or {}).get("factory"):
            raise ValueError("This is a host inspection descriptor; call its existing factory in your SDK application.")
        # Enforce here too: direct callers need not have called preflight.
        capabilities.require_supported(features_mod.resolve(self.doc.get("features")))
        self._presentation_config = presentation.configuration(self.doc)
        if self._agent is not None:
            return

        from google.adk.agents import LlmAgent
        from google.adk.apps import App

        agent_cfg = self.doc.get("agent") or {}
        self._compiled = instruction_mod.compile_config(self.root, self.doc)

        resolved = features_mod.resolve(self.doc.get("features"))
        if "skill_retrieval" in resolved.active:
            cfg = resolved.config["skill_retrieval"]
            vf = retrieval_mod.load_vectors(
                self.root / cfg.get("vectors", "embeddings/skills.json"))
            vector_problems = gw.check_vectors(vf, self._embedding_settings)
            if vector_problems and self._embedder_override is None:
                raise ValueError("; ".join(vector_problems))
            include = set((self.doc.get("skills") or {}).get("include") or [])
            vectors = {k: v for k, v in vf.vectors.items()
                       if not include or k in include}
            rcfg = retrieval_mod.config_from_dict(
                {**{k: v for k, v in cfg.items() if k != "vectors"},
                 "dimensions": vf.dimensions})
            self._retrieval = retrieval_mod.SkillRetrieval(
                rcfg, vectors,
                self._embedder_override or gw.GatewayEmbedder(self._embedding_settings, task_type="RETRIEVAL_QUERY"),
                vf.descriptions)

        self._tools_by_skill = _tools_by_skill(self.root, self.doc)
        self._catalog = _catalog_names(self.root, self.doc)

        # Prepare the host import path and resolve hooks before tool imports.
        # Import order matters when host dependencies initialize shared modules.
        repo_problem = _prepare_tool_repo(self.root, self.doc)
        self._widget_specs, spec_problems = _load_widget_specs(self.doc)
        if repo_problem:
            spec_problems = [repo_problem] + spec_problems
        self._widgets_active = "staged_widgets" in resolved.active
        self._widget_cfg = resolved.config.get("staged_widgets", {})

        # Real callables, when tools.repo names an agent repo to import from.
        # Absent that, the window stays prompt-only and says so.
        self._tool_fns, self._tool_problems = _load_tool_fns(self.root, self.doc)
        if self._presentation_config:
            from adkl.presentation_runtime import tools
            self._presentation_tools = tools(self._presentation_config, self._tool_fns)
            self._tool_fns.update(self._presentation_tools)
        self._tool_problems = list(self._tool_problems) + spec_problems
        self._callbacks, callback_problems = _load_callbacks(self.root, self.doc)
        if callback_problems:
            raise RuntimeError("; ".join(callback_problems))

        # Native ADK skills are the execution path.  Retrieval chooses a bounded
        # subset for each turn below; SkillToolset then supplies its procedures,
        # resources and only that skill's declared additional tools.
        self._skills = _load_skills(self.root, self.doc)
        required_tools = {name for names in self._tools_by_skill.values() for name in names}
        required_tools.update((self.doc.get("tools") or {}).get("core") or [])
        missing = sorted(required_tools - set(self._tool_fns))
        if missing:
            raise ValueError(f"Declared tools are not executable: {', '.join(missing)}")
        if spec_problems:
            raise ValueError("; ".join(spec_problems))


        # Declared specs with the feature off is the silent failure this whole
        # pipeline exists to prevent, so say it here rather than leaving the
        # reader to notice an absent widget.
        if self._widget_specs and not self._widgets_active:
            self._tool_problems.append(
                f"{len(self._widget_specs)} widget spec(s) declared but "
                f"'staged_widgets' is not in features.pipeline -- registers will "
                f"be staged and never drained.")

        name = agent_cfg.get("name", "agent")

        # The test-mode note goes in ONLY when nothing was bound. See the module
        # docstring: a prompt saying "answer from sources" with no working tool
        # returns empty parts, which reads as a broken agent. With real tools the
        # note would be a lie that suppresses the tool calls we want.
        note = "" if self._tool_fns else TEST_MODE_NOTE
        self._compiled_for_turn = instruction_mod.CompiledInstruction(
            text=self._compiled.text + note,
            slots_filled=self._compiled.slots_filled,
            state_keys=self._compiled.state_keys,
            artifact_keys=self._compiled.artifact_keys,
            passthrough=self._compiled.passthrough,
        )

        hooks = self._compose_callbacks(self._callbacks)
        name = agent_cfg.get("name", "agent")
        self._agent = LlmAgent(
            name=name,
            model=self._model_override or gw.build_model(self.settings, agent_cfg.get("model")),
            instruction=instruction_mod.build_provider(self._compiled_for_turn,
                                                       agent_name=name),
            tools=[self._selection, *self._global_presentation_tools()],
            **hooks,
            **_generation_kwargs(agent_cfg.get("generation") or {}),
        )
        self._app = App(name=name, root_agent=self._agent)


    @property
    def agent(self):
        self._build()
        return self._agent

    @property
    def app(self):
        self._build()
        return self._app


def build_agent(root: pathlib.Path | str, *, env=None, model=None, embedder=None,
                prepare_skill=None, widget_sink=None, companion_sink=None, request_id=None,
                seed_today=True):
    """Build a native ADK LlmAgent with shared ADKL execution contracts.

    Attach it to your own App/Runner/session service. Each host must isolate
    project imports and provide trusted identity and tool authorization.
    """
    return AgentRuntime(pathlib.Path(root), env, model=model, embedder=embedder,
                        prepare_skill=prepare_skill, widget_sink=widget_sink,
                        companion_sink=companion_sink, request_id=request_id,
                        seed_today=seed_today).agent

# ------------------------------------------------------------------- helpers

def _read_env(root: pathlib.Path) -> dict:
    """Delegates to gateway.read_env, which owns this so embed.py can use it too."""
    return gw.read_env(root)


def _generation_kwargs(gen: dict) -> dict:
    """Translate the generation block into LlmAgent kwargs."""
    if not gen:
        return {}
    from google.genai import types

    out: dict[str, Any] = {}
    budget = gen.get("max_output_tokens")
    if budget:
        out["generate_content_config"] = types.GenerateContentConfig(
            max_output_tokens=int(budget))

    level = (gen.get("thinking_level") or "").upper()
    if gen.get("planner") == "builtin" and level:
        from google.adk.planners import BuiltInPlanner
        try:
            out["planner"] = BuiltInPlanner(
                thinking_config=types.ThinkingConfig(
                    include_thoughts=bool(gen.get("include_thoughts")),
                    thinking_level=getattr(types.ThinkingLevel, level),
                ))
        except AttributeError:
            # An unknown level should not stop the agent from building; the
            # validator already rejects bad values at config time.
            pass
    return out


def _tools_by_skill(root: pathlib.Path, doc: dict) -> dict:
    sdir = root / ((doc.get("skills") or {}).get("dir") or "skills")
    out: dict[str, list] = {}
    for name in (doc.get("skills") or {}).get("include") or []:
        md = sdir / name / "SKILL.md"
        if not md.is_file():
            continue
        fm, _ = skill_lint.parse_frontmatter(md)
        tools = (fm.get("metadata") or {}).get("adk_additional_tools") or []
        out[name] = list(tools) if isinstance(tools, list) else []
    return out


def _catalog(root: pathlib.Path, doc: dict) -> dict:
    import json
    rel = (doc.get("tools") or {}).get("catalog")
    if not rel or not (root / rel).is_file():
        return presentation.extend_catalog({}, doc)
    return presentation.extend_catalog(json.loads((root / rel).read_text(encoding="utf-8")), doc)


def _catalog_names(root: pathlib.Path, doc: dict) -> list:
    return [t["name"] for t in _catalog(root, doc).get("tools", [])]


def _load_tool_fns(root: pathlib.Path, doc: dict) -> tuple[dict, list]:
    """Import real tool callables if `tools.repo` names an agent repo.

    Returns `({}, [])` when no repo is configured -- that is the standalone case,
    not an error, and the window runs prompt-only.
    """
    repo = _repo_path(root, doc)
    if repo is None:
        return {}, []
    from adkl import toolbind
    try:
        catalog = _catalog(root, doc)
        catalog = {**catalog, "tools": [t for t in catalog.get("tools", [])
                                       if t.get("provider") != "adkl/presentation"]}
        return toolbind.load(repo, catalog)
    except toolbind.ToolBindError as exc:
        # Config error, not a crash: the turn still runs prompt-only and the
        # console shows why nothing was executable.
        return {}, [str(exc)]


_CALLBACK_KINDS = frozenset({
    "before_agent_callback", "after_agent_callback", "before_model_callback",
    "after_model_callback", "before_tool_callback", "after_tool_callback",
    "on_model_error_callback", "on_tool_error_callback",
})


def _load_callbacks(root: pathlib.Path, doc: dict) -> tuple[dict[str, list], list[str]]:
    """Resolve explicitly declared ADK callbacks from the agent's tool repo.

    Callbacks are executable runtime configuration, not a feature declaration.
    A missing callback therefore blocks preflight rather than silently leaving a
    state placeholder or safety guard unwired.
    """
    declared = ((doc.get("agent") or {}).get("callbacks") or {})
    if not declared:
        return {}, []
    if not isinstance(declared, dict):
        return {}, ["agent.callbacks must be a mapping"]

    repo = _repo_path(root, doc)
    if repo is None:
        return {}, ["agent.callbacks requires tools.repo"]
    from adkl import toolbind
    try:
        toolbind.prepare(repo)
    except toolbind.ToolBindError as exc:
        return {}, [f"agent.callbacks: {exc}"]

    resolved: dict[str, list] = {}
    problems: list[str] = []
    for kind, refs in declared.items():
        if kind not in _CALLBACK_KINDS:
            problems.append(f"agent.callbacks.{kind}: unsupported ADK callback kind")
            continue
        if not isinstance(refs, list) or not refs:
            problems.append(f"agent.callbacks.{kind}: must be a non-empty list")
            continue
        for ref in refs:
            if not isinstance(ref, str) or "." not in ref:
                problems.append(f"agent.callbacks.{kind}: {ref!r} must be module.symbol")
                continue
            module, symbol = ref.rsplit(".", 1)
            try:
                fn = getattr(importlib.import_module(module), symbol)
            except Exception as exc:
                problems.append(
                    f"agent.callbacks.{kind}: {ref}: {type(exc).__name__}: {exc}")
                continue
            if not callable(fn):
                problems.append(f"agent.callbacks.{kind}: {ref} is not callable")
                continue
            resolved.setdefault(kind, []).append(fn)
    return resolved, problems


def _load_skills(root: pathlib.Path, doc: dict) -> dict[str, Any]:
    """Load the configured skills through ADK's native on-disk contract."""
    from google.adk.skills import load_skill_from_dir

    skills_cfg = doc.get("skills") or {}
    skills_dir = root / (skills_cfg.get("dir") or "skills")
    loaded: dict[str, Any] = {}
    for name in skills_cfg.get("include") or []:
        skill = load_skill_from_dir(skills_dir / name)
        if skill.name != name:
            raise ValueError(
                f"skill directory {name!r} declares name {skill.name!r}; names must match")
        loaded[name] = skill
    return loaded


def _repo_path(root: pathlib.Path, doc: dict) -> pathlib.Path | None:
    """Resolve `tools.repo` to a directory, or None when unset.

    A relative path is resolved against the agent.yaml, NOT the process CWD.
    That is what lets a project carry its own tool code: `repo: .` means "this
    project", and stays true whichever directory the server was started from.
    Low-level SDK callers may resolve absolute and `~` paths. Console execution
    separately requires tool source to stay inside its project boundary.
    """
    repo = (doc.get("tools") or {}).get("repo")
    if not repo:
        return None
    path = pathlib.Path(repo).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _prepare_tool_repo(root: pathlib.Path, doc: dict) -> str | None:
    """Make `tools.repo` importable, so spec hooks resolve. Returns a problem.

    Separated from `_load_tool_fns` because widget hooks need the repo importable
    *before* any tool module is imported, and both callers should go through one
    place rather than each doing their own sys.path surgery.
    """
    repo = _repo_path(root, doc)
    if repo is None:
        return None
    from adkl import toolbind
    try:
        toolbind.prepare(repo)
    except toolbind.ToolBindError as exc:
        return f"tools.repo: {exc}"
    return None


def _load_widget_specs(doc: dict) -> tuple[list, list]:
    """Compile declared widget specs. Returns (specs, problems).

    Problems are returned rather than raised: one malformed spec should cost its
    own widget, not the session, and the console shows what was dropped.
    """
    try:
        specs = widgets_mod.specs_from_config(doc)
    except widgets_mod.WidgetError as exc:
        return [], [f"widget specs: {exc}"]
    return specs, []


def _widget_tools(root: pathlib.Path, doc: dict, tools: list) -> list:
    """Which of the bound tools would have emitted an A2UI widget.

    Read from the catalog's `emits_widget`, which is derived from the tool's
    module source. A name-prefix guess gets this wrong on four of this agent's
    eleven tools -- `get_cart_summary`, `get_self_serve_flow`,
    `rerank_product_cta` and `search_supports` all emit through the staged path
    with no `render_`/`stage_` prefix -- so it is read, not inferred.
    """
    emits = {t["name"] for t in _catalog(root, doc).get("tools", [])
             if (t.get("emits_widget") or {}).get("value")}
    return [t for t in tools if t in emits]


def _tool_cost(root: pathlib.Path, doc: dict, tools: list) -> int:
    cost = {t["name"]: t.get("estimated_input_tokens_per_turn", 0)
            for t in _catalog(root, doc).get("tools", [])}
    return sum(cost.get(t, 0) for t in tools)
