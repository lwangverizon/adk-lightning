#!/usr/bin/env python3
"""adkl check — static analysis for retrieved-skill agents.

Rule 1 (TOOL_UNRESOLVED) detects frontmatter references to missing tools.
Silently dropping a misspelled tool name leaves skill instructions asking the
agent to call a tool it does not have; report that mismatch before execution.

Run standalone; no ADK import required (so it works in CI without the wheel).
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from collections import defaultdict

import yaml

_ABSENT = object()   # distinguishes an unset annotation from an empty value
# ADK's Frontmatter validator rejects longer descriptions, so load_skill_from_dir
# raises and a host that loads skills at import fails to start (google.adk.skills
# models._validate_description). Measured like ADK: characters of the parsed value.
DESCRIPTION_MAX_CHARS = 1024


def description_too_long(desc) -> str | None:
    """Message when ADK's loader would reject this description, else None."""
    if not isinstance(desc, str) or len(desc) <= DESCRIPTION_MAX_CHARS:
        return None
    return (f"description is {len(desc)} characters; ADK rejects more than "
            f"{DESCRIPTION_MAX_CHARS}, so loading this skill raises")


def _conflict():
    """Import the conflict rules under both package and script invocation.

    `check_one` needs one of its constants and runs on every caller's path, so
    the import cannot sit at module scope: this file is also run directly, where
    a relative import fails. Same idiom as `cross_layer_findings`, factored out
    rather than written twice.
    """
    if __package__:
        from . import lint_conflict
    else:  # pragma: no cover -- script mode
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from adkl import lint_conflict
    return lint_conflict


# ---------------------------------------------------------------- extraction


def parse_frontmatter(path: pathlib.Path) -> tuple[dict, str]:
    """Split a SKILL.md file into (frontmatter dict, body)."""
    return parse_frontmatter_text(path.read_text(encoding="utf-8", errors="replace"))


def parse_frontmatter_text(text: str) -> tuple[dict, str]:
    """Split SKILL.md *source* into (frontmatter dict, body).

    Text-based so the console can lint an unsaved editor buffer through the
    exact same rules CI runs on the file. Tolerant by design: a malformed skill
    should produce a lint finding, not a traceback.
    """
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not match:
        return {}, text
    raw = match[1]
    body = text[match.end():]
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        return {"__yaml_error__": str(exc)}, body
    return (fm if isinstance(fm, dict) else {}), body


_FUNCTION_TOOL_WRAPPERS = {"FunctionTool", "LongRunningFunctionTool"}


def registered_tool_names(agent_py: pathlib.Path) -> tuple[set[str], list[str]]:
    """Extract bindable tool names from an agent module via AST.

    Returns (names, warnings). Warnings matter: a wrapper form this function
    does not understand produces FALSE TOOL_UNRESOLVED errors, which is the
    worst possible failure for a linter. Surface the uncertainty instead of
    silently under-reporting the tool set.

    Deliberately NOT a scan of tools/*.py: those modules contain many private
    helpers (191 module-level defs across the tree vs 39 FunctionTool
    registrations in the prospect agent), so file scanning wildly over-reports
    what is actually bindable. The registration site is authoritative.

    Forms handled:
      FunctionTool(fn)              -> "fn"
      LongRunningFunctionTool(fn)   -> "fn"
      AgentTool(agent=build_x())    -> "x"      (ADK names an AgentTool after
                                                 its agent, not the factory)
    """
    names: set[str] = set()
    warnings: list[str] = []
    try:
        tree = ast.parse(agent_py.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        return names, [f"could not parse {agent_py.name}: {exc}"]

    def agent_tool_name(call: ast.Call) -> str | None:
        """Recover the agent name from AgentTool(agent=<expr>).

        The tool's LLM-visible name is the *agent's* name. Common idiom is a
        factory: AgentTool(agent=build_google_search_agent()) -> the agent is
        named 'google_search_agent'. Strip a build_/create_/get_ prefix.
        """
        for kw in call.keywords:
            if kw.arg != "agent":
                continue
            v = kw.value
            if isinstance(v, ast.Call):
                f = getattr(v.func, "id", None) or getattr(v.func, "attr", None)
                if f:
                    return re.sub(r"^(build|create|make|get|_)+_?", "", f)
            if isinstance(v, ast.Name):
                return v.id
            if isinstance(v, ast.Attribute):
                return v.attr
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname in _FUNCTION_TOOL_WRAPPERS:
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    names.add(arg.id)
                elif isinstance(arg, ast.Attribute):
                    names.add(arg.attr)
                else:
                    warnings.append(
                        f"{fname}(...) with a non-name argument at line "
                        f"{node.lineno}; tool name not recovered")
        elif fname == "AgentTool":
            n = agent_tool_name(node)
            if n:
                names.add(n)
            else:
                warnings.append(
                    f"AgentTool(...) at line {node.lineno}; agent name not recovered")
        elif fname and fname.endswith("Toolset"):
            # Toolsets contribute tools only at runtime (get_tools_with_prefix),
            # so their contents are invisible to static analysis.
            warnings.append(
                f"{fname} at line {node.lineno} contributes tools dynamically; "
                "its tools cannot be verified statically")

    # Bare callables in `tools=`, the wrapper-free form ADK also accepts. Shared
    # with the catalog rather than reimplemented: the docstring above promises
    # these two never disagree about what is bindable, and two copies of an AST
    # walker is how that promise gets broken.
    try:
        if __package__:
            from . import tool_catalog
        else:  # pragma: no cover -- script mode
            from adkl import tool_catalog
        bare, bare_warns = tool_catalog.bare_tool_names(tree)
        names |= bare
        warnings.extend(bare_warns)
    except ImportError:  # pragma: no cover
        warnings.append("tool_catalog unavailable; bare `tools=` callables "
                        "were not recovered")

    return names, warnings


# --------------------------------------------------------------------- rules

STOP = set("""a an the to for of and or with use this skill when user asks about
is are it its on in at be by from as that if then not do does can could would
you your our their they them he she his her i me my we us""".split())

# Polarity, for TOOL_USED_NOT_DECLARED. Verified against real skills: three
# distinct usages look identical to a naive grep --
#   "call `render_primary_action_cta`"            -> real bug
#   "Do NOT call `render_product_cta`"            -> correct as written
#   "If `add_to_checkout_v2` was already called"  -> guard on history
# Only the imperative form is a finding; the others are properly *absent* from
# the tool list. Reporting them would train users to ignore the rule.
_NEGATED = re.compile(r"\b(do\s+not|don't|never|avoid|without|instead\s+of|"
                      r"rather\s+than|no\s+need\s+to)\b", re.I)
_ALREADY = re.compile(r"\b(already|was|were|has\s+been|had\s+been|previously|"
                      r"prior\s+turn|earlier)\b", re.I)

# Both polarity checks need a window that is a *clause*, not a physical line.
# Skill bodies may be hard-wrapped, so the negation
# routinely lands on the line above the backticked tool:
#
#     2. After address submission, call `check_service_availability`—never
#        `check_home_internet_availability`. Qualified -> Step 3.
#
# Split per line, `before` for the second tool is just three spaces and the rule
# reports a correctly-written instruction. Reflowing each block and splitting on
# sentence punctuation makes the window independent of where the author's editor
# happened to wrap, while still stopping at the sentence boundary -- so "Do not
# call `a`. Call `b`." keeps reporting `b`.
_SENTENCE = re.compile(r"(?<=[.!?;:])\s")


def _clauses(body: str):
    """Yield sentence-ish clauses with intra-block line wrapping removed."""
    for block in re.split(r"\n\s*\n", body):
        flat = re.sub(r"\s+", " ", block).strip()
        if flat:
            yield from _SENTENCE.split(flat)

# Write/auth-sensitive tool bound with no authentication signal in the skill.
# Sierra's deterministic-guard layer, as a lint.
_SENSITIVE = re.compile(r"^(add_to|remove_|update_|delete_|submit_|cancel_|"
                        r"twofa_|poll_password|userlookup|.*_payment.*)", re.I)
_AUTH_SIGNAL = re.compile(
    r"\b(?:auth(?:entication|enticated|orization|orized)?|sign[ -]in|"
    r"signed[ -]in|logged[ -]in|verify|verified|2fa|otp)\b", re.I)


def _non_call_mention(clause: str, tool: str) -> bool:
    """Recognize local negative capability prose without silencing later calls."""
    plain = re.sub(r"\*{1,2}|(?<!\w)_(?!\w)", "", clause)
    before, _, after = plain.partition(f"`{tool}`")
    if _NEGATED.search(before) or _ALREADY.search(plain):
        return True
    if re.search(r"(?:,\s*not|\bno)\s*$", before, re.I):
        return True
    # "No `a`, `b`, or `c`" prohibits the whole enumerated list, not just a.
    # Do not consume an imperative following it: "No `a`, call `b`" is a call.
    if re.search(r"\bno\s+(?:`[\w]+`\s*[,/]\s*(?:(?:or|and)\s+)?)+$",
                 before, re.I):
        return True
    # A parenthetical list of capabilities is not an instruction to invoke them.
    # Only skip the narrow "have a ... path (`a` / `b`)" explanatory form.
    if re.search(r"\bhave\s+(?:a\s+)?[\w -]*path\s*\([^)]*$", before, re.I):
        if not re.search(r"\b(call|use|invoke|run)\b", before, re.I):
            return True
    if re.match(r"\s+(?:is|are)\s+not\s+(?:used|available|called)\b", after, re.I):
        return True
    # Capability explanations are not imperatives. Keep explicit Call/Use
    # instructions flaggable even if their output is described as empty.
    if not re.search(r"\b(call|use|invoke|run)\b", before, re.I):
        if re.match(r"\s+returns?\s+(?:an?\s+)?empty\b", after, re.I):
            return True
        if re.match(r"\s+answers?\b.*\bwith\s+unrelated\b", after, re.I):
            return True
    return False


def check_one(dir_name: str, fm: dict, body: str,
              known_tools: set[str], core_tools=()) -> list[tuple[str, str, str]]:
    """Per-skill rules. Returns [(severity, rule, message)].

    Single implementation shared by the CLI and the console API, so an editor
    buffer and a CI run can never disagree about what counts as a finding.
    Rules needing cross-skill context (DESCRIPTION_OVERLAP) live in lint().
    """
    out: list[tuple[str, str, str]] = []

    if "__yaml_error__" in fm:
        return [("ERROR", "FRONTMATTER_INVALID",
                 f"unparseable YAML: {fm['__yaml_error__'][:120]}")]

    name = fm.get("name")
    desc = fm.get("description") or ""
    declared = (fm.get("metadata") or {}).get("adk_additional_tools") or []
    if isinstance(declared, str):
        out.append(("ERROR", "TOOLS_NOT_A_LIST",
                    "metadata.adk_additional_tools is a string, not a list; "
                    "it will not resolve to any tool"))
        declared = []

    # A misspelled opt-out must fail loudly. Read as data it would be "not
    # text_only" and the coverage rule would keep firing, but an author who wrote
    # it believes the skill is annotated -- so the value is checked here, on the
    # path every caller runs, rather than inferred where it is consumed.
    conflict = _conflict()
    annotation = (fm.get("metadata") or {}).get(conflict.ANNOTATION, _ABSENT)
    if annotation is not _ABSENT and annotation != conflict.TEXT_ONLY:
        out.append(("ERROR", "PRESENTATION_METADATA_INVALID",
                    f"metadata.{conflict.ANNOTATION} is {annotation!r}; the only "
                    f"supported value is {conflict.TEXT_ONLY!r}. It does not "
                    "record a text-only skill"))

    if not name:
        out.append(("ERROR", "NAME_MISSING", "frontmatter has no `name`"))
    elif name != dir_name:
        out.append(("WARN", "NAME_DIR_MISMATCH",
                    f"frontmatter name {name!r} != directory {dir_name!r}; "
                    "retrieval and load_skill() may disagree"))
    if (too_long := description_too_long(desc)) is not None:
        out.append(("ERROR", "DESCRIPTION_TOO_LONG", too_long))
    if not desc.strip():
        out.append(("ERROR", "DESCRIPTION_MISSING",
                    "no description — skill is unreachable by semantic retrieval"))

    # Rule 1 — the silent no-op (context_aware_skill_toolset.py:1146).
    if known_tools:
        for tool in declared:
            if tool not in known_tools:
                out.append(("ERROR", "TOOL_UNRESOLVED",
                            f"declares tool {tool!r}, not registered with the agent — "
                            "silently dropped at bind time"))

    # Rule 2 — body instructs calling a tool the frontmatter never bound.
    declared_set = set(declared)
    seen: set[str] = set()
    for clause in _clauses(body):
        for tool in sorted(set(re.findall(r"`([a-z][a-z0-9_]{4,})`", clause))):
            if tool in seen or tool not in known_tools or tool in declared_set or tool in core_tools:
                continue
            if _non_call_mention(clause, tool):
                continue
            seen.add(tool)
            out.append(("WARN", "TOOL_USED_NOT_DECLARED",
                        f"body instructs calling `{tool}` but frontmatter does not "
                        "declare it — the model will be told to use a tool it lacks"))

    # Rule 4 — sensitive tool with no authentication precondition.
    hot = [t for t in declared if isinstance(t, str) and _SENSITIVE.match(t)]
    if hot:
        blob = (desc + body).lower()
        if not _AUTH_SIGNAL.search(blob):
            out.append(("WARN", "SENSITIVE_TOOL_NO_AUTH_GATE",
                        f"name-based sensitive-tool candidate(s): "
                        f"{', '.join(sorted(hot)[:3])}; no authentication signal "
                        "found in skill text. Host routing, tool guards and "
                        "backend authorization were not inspected; this is not "
                        "proof of a missing runtime gate"))
    return out


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()) if w not in STOP}


def cross_layer_findings(skills: list, catalog: dict | None = None,
                         prompt_text: str = "", core_tools=()) -> list[tuple[str, str, str, str]]:
    """Return catalog- and prompt-aware findings for already parsed skills.

    Kept separate from ``check_one`` because these rules require the whole agent
    surface. The server calls this with an unsaved editor buffer, while the CLI
    calls it with files on disk; one implementation keeps those results aligned.
    """
    if not catalog and not prompt_text:
        return []
    if __package__:
        from . import lint_conflict as conflict
        from . import lint_cost as cost_lint
    else:  # pragma: no cover -- script mode
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        from adkl import lint_conflict as conflict
        from adkl import lint_cost as cost_lint

    findings: list[tuple[str, str, str, str]] = []
    tools = (catalog or {}).get("tools", [])
    cost = {t["name"]: t["estimated_input_tokens_per_turn"] for t in tools
            if isinstance(t.get("estimated_input_tokens_per_turn"), (int, float))}
    # Absent `emits_widget` maps to None, not False: it means the inventory that
    # produced this catalog never scanned module source (the native/host-factory
    # path reports name+description+scope only). Defaulting it to False told
    # `check_widget_promises` that every tool provably emits nothing.
    emits = {t["name"]: (t.get("emits_widget") or {}).get("value")
             for t in tools}
    # The stricter map: emission the catalog *declares* rather than infers.
    # `tool_catalog.widget_emission` searches module text and always marks the
    # result inferred, so only a presentation pack or a result binding lands here.
    # `check_widget_coverage` needs that distinction to arm on a configured
    # project instead of any project whose domain module mentions a render call.
    configured = {t["name"]: bool((t.get("emits_widget") or {}).get("value"))
                  and not (t.get("emits_widget") or {}).get("inferred", True)
                  for t in tools}
    summaries = {t["name"]: f"{t.get('summary', '')} {t.get('returns', '')}"
                 for t in tools}

    for name, directory, fm, body, declared in skills:
        refs = sum(path.stat().st_size for path in directory.rglob("*.md")
                   if path.name != "SKILL.md") if directory.is_dir() else 0
        for sev, rule, message in cost_lint.check_skill_cost(
                name, body, declared, cost, refs):
            findings.append((sev, rule, name, message))
        for sev, rule, message in (
                conflict.check_widget_promises(name, body, declared, emits)
                + conflict.check_widget_coverage(name, fm, declared, emits,
                                                configured)
                + conflict.check_tool_order(name, body, declared, summaries)
                + conflict.check_prompt_prohibitions(name, body, prompt_text)):
            findings.append((sev, rule, name, message))

    triples = [(name, body, declared) for name, _d, _fm, body, declared in skills]
    from adkl.lint_prompt import check_prompt

    findings.extend(check_prompt(prompt_text, triples, catalog, core_tools))
    findings.extend(conflict.check_cross_skill(triples, summaries))
    findings.extend(conflict.check_prompt_tool_scope(prompt_text, triples, catalog or {}, core_tools))
    if tools:
        findings.extend(cost_lint.check_tool_cost(tools))
        findings.extend(conflict.check_unused_tools(
            [tool for tool in tools if tool["name"] not in core_tools], triples))
    return findings


def lint(skills_dir: pathlib.Path, agent_py: pathlib.Path | None,
         allow: set[str] | None = None, catalog: dict | None = None,
         prompt_text: str = "", core_tools=(), *, logical_names=None):
    findings: list[tuple[str, str, str, str]] = []  # sev, rule, skill, msg

    known_tools: set[str] = set()
    extract_warnings: list[str] = []
    if catalog:
        known_tools.update(t["name"] for t in catalog.get("tools", []))
    if agent_py:
        known_tools, extract_warnings = registered_tool_names(agent_py)

    skills = []  # (name, dir, fm, body, declared_tools)
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        d = skill_md.parent
        if allow is not None and d.name not in allow:
            continue
        fm, body = parse_frontmatter(skill_md)
        name = (logical_names or {}).get(d.name, d.name)
        for sev, rule, msg in check_one(name, fm, body, known_tools, core_tools):
            findings.append((sev, rule, name, msg))
        if "__yaml_error__" in fm:
            continue
        declared = (fm.get("metadata") or {}).get("adk_additional_tools") or []
        if isinstance(declared, str):
            declared = []
        skills.append((fm.get("name") or d.name, d, fm, body, list(declared)))

    # Rule 3 — description overlap. Retrieval cannot separate near-duplicates.
    # Recognize an explicit disambiguation convention:
    # an explicit `NOT for ... -> other-skill` clause naming the peer.
    eligible = [s for s in skills
                if (s[2].get("metadata") or {}).get("adk_exclude_from_matching") is not True]
    descs = {n: tokens(fm.get("description") or "") for n, _d, fm, _b, _t in eligible}
    raw_desc = {n: (fm.get("description") or "") for n, _d, fm, _b, _t in eligible}
    names = sorted(descs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ta, tb = descs[a], descs[b]
            if len(ta) < 5 or len(tb) < 5:
                continue
            jac = len(ta & tb) / len(ta | tb)
            if jac >= 0.42:
                mutual = b in raw_desc[a] or a in raw_desc[b]
                findings.append((
                    "INFO" if mutual else "WARN", "DESCRIPTION_OVERLAP", a,
                    f"{jac:.0%} token overlap with {b}"
                    + ("; exclusion clause present" if mutual
                       else "; no exclusion clause — both may match the same turn")))

    findings.extend(cross_layer_findings(skills, catalog, prompt_text, core_tools))

    # Rules 1, 2 and 4 live in check_one() — shared with the console API.

    return findings, skills, known_tools, extract_warnings


def _read_prompt(where: pathlib.Path) -> str:
    """Read the prompt template and blocks that can constrain skill behavior."""
    if where.is_file():
        return where.read_text(encoding="utf-8", errors="replace")
    if (where / "agent.yaml").is_file():
        from adkl.instruction import compile_config
        doc = yaml.safe_load((where / "agent.yaml").read_text(encoding="utf-8")) or {}
        return compile_config(where, doc).text
    parts = []
    for candidate in (where / "instruction.md", where / "identity.md"):
        if candidate.is_file():
            parts.append(candidate.read_text(encoding="utf-8", errors="replace"))
    blocks = where / "blocks"
    if blocks.is_dir():
        parts.extend(item.read_text(encoding="utf-8", errors="replace")
                     for item in sorted(blocks.glob("*.md")))
    return "\n".join(parts)


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Lint retrieved-skill agent definitions.")
    ap.add_argument("skills_dir", type=pathlib.Path)
    ap.add_argument("--agent", type=pathlib.Path, default=None,
                    help="agent.py whose FunctionTool(...) calls define the bindable tool set")
    ap.add_argument("--catalog", type=pathlib.Path, default=None,
                    help="generated tool catalog JSON (adkl catalog output). Preferred "
                         "over --agent: it is what the console and agent.yaml use, so "
                         "the linter checks the same tool set the runtime will bind.")
    ap.add_argument("--prompt", type=pathlib.Path, default=None,
                    help="agent workspace or instruction file to check against; "
                         "defaults to the skills directory's parent")
    ap.add_argument("--severity", choices=["ERROR", "WARN", "INFO"], default="WARN")
    ap.add_argument("--only", type=pathlib.Path, default=None,
                    help="file of skill dir names, one per line: lint just this agent's set. "
                         "Without it, every skill in the tree is checked against one agent's "
                         "tool list, which over-reports TOOL_UNRESOLVED for other agents' skills.")
    args = ap.parse_args()

    allow = None
    if args.only:
        allow = {ln.strip() for ln in args.only.read_text().splitlines() if ln.strip()}

    findings, skills, known, extract_warnings = lint(args.skills_dir, args.agent, allow)

    # A catalog is authoritative: it is generated from the registrations and is
    # what agent.yaml points at. Using it also drops the AST-extraction caveat,
    # since nothing was inferred.
    if args.catalog:
        import json
        project_doc = {}
        try:
            cat = json.loads(args.catalog.read_text(encoding="utf-8"))
            project_config = args.skills_dir.parent / "agent.yaml"
            if project_config.is_file():
                import yaml
                from adkl.presentation import extend_catalog
                project_doc = yaml.safe_load(project_config.read_text()) or {}
                cat = extend_catalog(cat, project_doc)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: could not read {args.catalog}: {exc}", file=sys.stderr)
            return 2
        known = {t["name"] for t in cat.get("tools", [])}
        # The catalog's tool set is exact, so the AST-inference caveat does not
        # apply. Its own notes still do -- a runtime toolset contributes tools
        # no static pass can see, in either mode.
        extract_warnings = [f"(from catalog) {w}" for w in cat.get("extraction_warnings", [])]
        findings, skills, _k, _w = lint(
            args.skills_dir, None, allow, catalog=cat,
            prompt_text=_read_prompt(args.prompt or args.skills_dir.parent),
            core_tools=(project_doc.get("tools") or {}).get("core") or [])

    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    cutoff = order[args.severity]
    shown = [f for f in findings if order[f[0]] <= cutoff]

    by_rule: dict[str, list] = defaultdict(list)
    for sev, rule, skill, msg in shown:
        by_rule[rule].append((sev, skill, msg))

    print(f"adkl check — {len(skills)} skills, {len(known)} bindable tools\n")

    # Print BEFORE the findings: these caveat every TOOL_UNRESOLVED below.
    if extract_warnings:
        exact = bool(getattr(args, "catalog", None))
        print(f"[NOTE] {len(extract_warnings)} caveat(s)"
              + ("" if exact else
                 "; tool extraction was incomplete, so TOOL_UNRESOLVED may "
                 "include false positives"))
        for w in extract_warnings[:8]:
            print(f"    {w}")
        if len(extract_warnings) > 8:
            print(f"    ... {len(extract_warnings) - 8} more")
        print()
    for rule in sorted(by_rule, key=lambda r: (order[by_rule[r][0][0]], r)):
        rows = by_rule[rule]
        print(f"[{rows[0][0]}] {rule}  ({len(rows)})")
        for sev, skill, msg in rows[:12]:
            print(f"    {skill}: {msg}")
        if len(rows) > 12:
            print(f"    ... {len(rows) - 12} more")
        print()

    errs = sum(1 for f in findings if f[0] == "ERROR")
    warns = sum(1 for f in findings if f[0] == "WARN")
    print(f"{errs} error(s), {warns} warning(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
