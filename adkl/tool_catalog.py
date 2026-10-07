#!/usr/bin/env python3
"""adkl catalog — generate a machine-readable tool catalog for the console.

The console uses this artifact to resolve declared tool names against Python
functions without importing application code.

Design decisions, and why:

1. AST-only, never import. The tool modules can pull in service clients, pandas,
   GCP credentials, and network config at import time. A console that imports
   them to build a picker is a console that cannot start offline, and in CI it
   would need production secrets. Everything here comes from the syntax tree.

2. Registration is authoritative, not the filesystem. tools/*.py contains 191
   module-level defs but the prospect agent registers 40 tools. Scanning files
   would offer the user private helpers as if they were bindable.

3. Side-effect class is a first-class field, inferred and always labeled with
   its basis. The console must show write/auth-gated tools differently from
   reads -- that is the deterministic-guard layer, and SENSITIVE_TOOL_NO_AUTH
   in the linter depends on it. Inference is never silently presented as fact.

4. Token cost is estimated per tool. Research finding: attaching a tool is a
   free gesture in a UI but a recurring per-turn charge (one measured report:
   4 tools => ~3,200 input tokens/turn, re-billed every loop). The picker shows
   this next to every checkbox.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

# ------------------------------------------------------------------ inference

# Ordered: first match wins, so specific patterns precede generic verbs.
_SIDE_EFFECTS: list[tuple[str, str, str]] = [
    # Auth first, and matched anywhere in the name -- not just as a prefix.
    # Caught by running against the real prospect agent: six passwordless tools
    # (initiate_passwordless_setup, complete_passwordless_auth, ...) fell through
    # to "unknown" because the auth marker sits mid-name. Under-classifying an
    # auth tool is the expensive direction of error: SENSITIVE_TOOL_NO_AUTH_GATE
    # depends on this field, so a miss here silently disables a security lint.
    (r"(twofa|2fa|passwordless|otp|password_reset|poll_password|userlookup|"
     r"sign_?in|log_?in|authenticat|credential|verify_identity)", "auth",
     "name contains an authentication marker"),
    (r"^(add_to|remove_from|update_|delete_|submit_|cancel_|create_|set_|"
     r"place_|apply_|activate_|disconnect_|waive_)", "write",
     "name begins with a mutating verb"),
    (r"^render_", "render", "name begins with render_ (emits UI, no backend mutation)"),
    (r"^(get_|list_|search_|check_|load_|fetch_|lookup_|is_|has_|find_)", "read",
     "name begins with a retrieval verb"),
]

_AUTH_HINTS = re.compile(
    r"\b(authenticat|signed[- ]in|sign[- ]in|logged[- ]in|login|mdn|account\s*number|"
    r"token|credential|session\s*id|verified|2fa|otp|entitle)\b", re.I)


def classify(name: str, doc: str) -> tuple[str, str]:
    for pat, cls, why in _SIDE_EFFECTS:
        if re.search(pat, name, re.I):
            return cls, why
    return "unknown", "no recognized verb prefix; classify by hand"


def estimate_tokens(name: str, doc: str, params: list[dict]) -> int:
    """Rough input-token cost of this tool's declaration, per turn.

    ADK sends the name, the docstring, and a JSON Schema for the parameters on
    every request the tool is bound for. ~4 chars/token is the usual heuristic;
    schema punctuation inflates the parameter share, hence the multiplier.
    Deliberately labeled an estimate everywhere it surfaces.
    """
    chars = len(name) + len(doc)
    for p in params:
        chars += len(p["name"]) + len(p.get("type") or "") + len(p.get("description") or "") + 24
    return max(1, round(chars / 4 * 1.15))


# ------------------------------------------------------------------ docstrings

def split_docstring(doc: str) -> tuple[str, dict[str, str], str]:
    """Return (summary, {param: description}, returns).

    Handles Google style (Args:/Returns:), which is what this codebase uses and
    what ADK itself parses for the LLM-visible declaration.
    """
    if not doc:
        return "", {}, ""
    lines = doc.strip().splitlines()
    summary_parts: list[str] = []
    args: dict[str, str] = {}
    returns: list[str] = []
    section = "summary"
    current: str | None = None

    for raw in lines:
        line = raw.strip()
        low = line.lower().rstrip(":")
        if low in ("args", "arguments", "parameters", "params"):
            section, current = "args", None
            continue
        if low in ("returns", "return", "yields"):
            section, current = "returns", None
            continue
        if low in ("raises", "note", "notes", "example", "examples", "warning"):
            section, current = "other", None
            continue

        if section == "summary":
            summary_parts.append(line)
        elif section == "args":
            m = re.match(r"^([*\w]+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$", line)
            if m:
                current = m.group(1).lstrip("*")
                args[current] = m.group(3).strip()
            elif current and line:
                args[current] = (args[current] + " " + line).strip()
        elif section == "returns":
            returns.append(line)

    summary = " ".join(p for p in summary_parts if p).strip()
    return summary, args, " ".join(returns).strip()


# --------------------------------------------------------------- ast walking

_SKIP_PARAMS = {"tool_context", "self", "cls"}

# Two ways a tool emits an A2UI widget, and both have to be checked.
#
#   direct  tool_context.render_ui_widget(UiWidget(...)) inside the tool
#   staged  the tool registers a spec with staged_widget_state /
#           staged_widget_framework and a turn-boundary callback emits it
#
# Checking only the direct call is what a name-prefix guess gets wrong in the
# same direction: on the prospect agent's 11 tools, `get_cart_summary`,
# `get_self_serve_flow`, `rerank_product_cta` and `search_supports` all emit
# through the staged path with no `render_`/`stage_` prefix and no direct call.
# Four of eleven is too high a miss rate to guess, which is why this is read off
# the module source instead.
_WIDGET_DIRECT = "render_ui_widget"
_WIDGET_STAGED = ("staged_widget_state", "staged_widget_framework")


def widget_emission(module: str) -> dict:
    """Whether a tool's module emits an A2UI widget, and on what evidence.

    Source-text search, not AST: the emitting call is often several helpers deep
    inside the module rather than in the tool function itself, so a walk of the
    tool's own body would report no widget for tools that plainly render one.
    That makes this module-level, and therefore an over-report when a file holds
    several tools and only one renders -- recorded in `basis` so a reader can see
    which signal fired rather than trusting the boolean alone.
    """
    path = pathlib.Path(module)
    if not path.is_file():
        return {"value": False, "basis": "module not readable", "inferred": True}
    src = path.read_text(encoding="utf-8", errors="replace")
    direct = _WIDGET_DIRECT in src
    staged = any(s in src for s in _WIDGET_STAGED)
    if direct and staged:
        basis = "calls render_ui_widget and registers a staged widget"
    elif direct:
        basis = "calls render_ui_widget"
    elif staged:
        basis = "registers a staged widget emitted at the turn boundary"
    else:
        basis = "no widget-emitting call found in the module"
    return {"value": direct or staged, "basis": basis, "inferred": True}


def annotation_str(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def find_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def describe(fn, module: str) -> dict:
    doc = ast.get_docstring(fn) or ""
    summary, arg_docs, returns = split_docstring(doc)

    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    defaults_start = len(positional) - len(a.defaults)
    params: list[dict] = []

    for i, arg in enumerate(positional):
        if arg.arg in _SKIP_PARAMS:
            continue
        required = i < defaults_start
        default = None
        if not required:
            d = a.defaults[i - defaults_start]
            try:
                default = ast.literal_eval(d)
            except Exception:
                default = annotation_str(d)
        params.append({
            "name": arg.arg,
            "type": annotation_str(arg.annotation),
            "required": required,
            "default": default,
            "description": arg_docs.get(arg.arg, ""),
        })

    for arg in a.kwonlyargs:
        if arg.arg in _SKIP_PARAMS:
            continue
        params.append({
            "name": arg.arg, "type": annotation_str(arg.annotation),
            "required": False, "default": None,
            "description": arg_docs.get(arg.arg, ""),
        })

    cls, basis = classify(fn.name, doc)
    needs_auth = bool(_AUTH_HINTS.search(doc)) or cls == "auth"

    undocumented = [p["name"] for p in params if not p["description"]]

    return {
        "name": fn.name,
        "module": module,
        "summary": summary,
        "returns": returns,
        "params": params,
        "is_async": isinstance(fn, ast.AsyncFunctionDef),
        "uses_tool_context": any(x.arg == "tool_context"
                                 for x in list(a.args) + list(a.kwonlyargs)),
        "side_effect": {"class": cls, "basis": basis, "inferred": True},
        "emits_widget": widget_emission(module),
        "requires_auth": {"value": needs_auth,
                          "basis": "docstring mentions an identity/credential term"
                                   if needs_auth else "no identity terms found",
                          "inferred": True},
        "estimated_input_tokens_per_turn": estimate_tokens(fn.name, doc, params),
        "quality": {
            "has_docstring": bool(doc),
            "has_summary": bool(summary),
            "undocumented_params": undocumented,
        },
    }


_AGENT_CTORS = ("Agent", "LlmAgent")


def bare_tool_names(tree: ast.Module) -> tuple[set, list]:
    """Names passed to an agent's `tools=` as plain callables.

    ADK accepts a bare function in `tools=[...]` -- no FunctionTool wrapper --
    and this is a common registration pattern. Recognising only the
    wrapper made a mainstream agent catalogue as zero tools, which reads as "this
    agent has none" rather than "this reader cannot see them".

    Two shapes, both real:

        tools=[save_progress, render_quiz]      a list literal
        tools=_text_tools                       a name built by .append()

    The second is not a stylistic quirk: clawtutor builds the list that way
    because some entries are conditional. Following the name is what makes its
    11 tools visible.

    This stays a *registration site* reader, which is the invariant the module
    docstring rests on -- it never falls back to scanning files for defs, since
    there are 191 module-level defs against 39 registrations in the prospect
    agent alone.
    """
    names: set = set()
    warns: list = []

    def element(node) -> None:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        # A Call here is FunctionTool/AgentTool/Toolset, already handled by the
        # caller's own walk. Anything else is an expression we cannot name.

    def expand(value, module: ast.Module) -> None:
        if isinstance(value, (ast.List, ast.Tuple)):
            for el in value.elts:
                element(el)
            return
        if not isinstance(value, ast.Name):
            warns.append("tools= is neither a list nor a name; "
                         "bare callables not recovered")
            return
        # Follow the name: its initial assignment plus every append/extend.
        target = value.id
        for node in ast.walk(module):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == target:
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for el in node.value.elts:
                                element(el)
            elif isinstance(node, ast.Call):
                f = node.func
                if not isinstance(f, ast.Attribute) or not isinstance(f.value, ast.Name):
                    continue
                if f.value.id != target:
                    continue
                if f.attr == "append":
                    for a in node.args:
                        element(a)
                elif f.attr == "extend":
                    for a in node.args:
                        if isinstance(a, (ast.List, ast.Tuple)):
                            for el in a.elts:
                                element(el)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname not in _AGENT_CTORS:
            continue
        for kw in node.keywords:
            if kw.arg == "tools":
                expand(kw.value, tree)
    return names, warns


def registrations(agent_py: pathlib.Path) -> tuple[dict[str, str], list[str]]:
    """Map tool name -> the symbol imported for it, plus warnings.

    Mirrors skill_lint.registered_tool_names so the catalog and the linter can
    never disagree about what is bindable.
    """
    out: dict[str, str] = {}
    warns: list[str] = []
    tree = ast.parse(agent_py.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname in ("FunctionTool", "LongRunningFunctionTool"):
            for arg in node.args:
                sym = getattr(arg, "id", None) or getattr(arg, "attr", None)
                if sym:
                    out[sym] = sym
                else:
                    warns.append(f"{fname}(...) line {node.lineno}: name not recovered")
        elif fname == "AgentTool":
            for kw in node.keywords:
                if kw.arg != "agent":
                    continue
                v = kw.value
                f = (getattr(v.func, "id", None) or getattr(v.func, "attr", None)) \
                    if isinstance(v, ast.Call) else \
                    (getattr(v, "id", None) or getattr(v, "attr", None))
                if f:
                    n = re.sub(r"^(build|create|make|get|_)+_?", "", f)
                    out[n] = f
        elif fname and fname.endswith("Toolset"):
            warns.append(f"{fname} line {node.lineno}: contributes tools at runtime; "
                         "not statically catalogable")

    # Bare callables in `tools=` need no wrapper and must also be catalogued.
    bare, bare_warns = bare_tool_names(tree)
    for sym in bare:
        out.setdefault(sym, sym)
    warns.extend(bare_warns)
    return out, warns


def build(agent_py: pathlib.Path, search: list[pathlib.Path]) -> dict:
    regs, warns = registrations(agent_py)

    # Index every module-level def once, so lookup is by symbol not by filename.
    index: dict[str, tuple[ast.AST, str]] = {}
    for root in search:
        for py in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = str(py)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    index.setdefault(node.name, (node, rel))

    tools, missing = [], []
    for tool_name, symbol in sorted(regs.items()):
        hit = index.get(symbol) or index.get(tool_name)
        if not hit:
            missing.append(tool_name)
            continue
        fn, mod = hit
        entry = describe(fn, mod)
        entry["name"] = tool_name  # registration name wins (AgentTool renames)
        tools.append(entry)

    return {
        "agent": agent_py.stem if agent_py.stem != "agent" else agent_py.parent.name,
        "source": str(agent_py),
        "tool_count": len(tools),
        "tools": tools,
        "unresolved_registrations": missing,
        "extraction_warnings": warns,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a tool catalog from an ADK agent module.")
    ap.add_argument("agent", type=pathlib.Path)
    ap.add_argument("--search", type=pathlib.Path, nargs="+", required=True,
                    help="directories to scan for tool implementations")
    ap.add_argument("-o", "--out", type=pathlib.Path)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    cat = build(args.agent, args.search)

    if args.out:
        args.out.write_text(json.dumps(cat, indent=2), encoding="utf-8")
        print(f"wrote {args.out}  ({cat['tool_count']} tools)")

    if args.summary or not args.out:
        print(f"\ncatalog: {cat['agent']} — {cat['tool_count']} tools")
        by_class: dict[str, int] = {}
        total = 0
        for t in cat["tools"]:
            by_class[t["side_effect"]["class"]] = by_class.get(t["side_effect"]["class"], 0) + 1
            total += t["estimated_input_tokens_per_turn"]
        print("  side-effect class (inferred): "
              + ", ".join(f"{k}={v}" for k, v in sorted(by_class.items())))
        print(f"  est. input tokens if ALL bound: ~{total:,}/turn")
        nodoc = [t["name"] for t in cat["tools"] if not t["quality"]["has_summary"]]
        if nodoc:
            print(f"  no docstring summary ({len(nodoc)}): {', '.join(nodoc[:6])}"
                  + (" ..." if len(nodoc) > 6 else ""))
        undoc = [t["name"] for t in cat["tools"] if t["quality"]["undocumented_params"]]
        if undoc:
            print(f"  undocumented params ({len(undoc)}): {', '.join(undoc[:6])}"
                  + (" ..." if len(undoc) > 6 else ""))
        if cat["unresolved_registrations"]:
            print(f"  UNRESOLVED ({len(cat['unresolved_registrations'])}): "
                  + ", ".join(cat["unresolved_registrations"]))
        for w in cat["extraction_warnings"]:
            print(f"  note: {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
