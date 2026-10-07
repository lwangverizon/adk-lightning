"""Differential test: adkl.render vs ADK's real _render_with_regex.

adkl.instruction.render reimplements ADK's interpolation engine, because a
callable instruction sets bypass_state_injection=True and ADK then skips its
own injection entirely. Reimplementing from a source read is exactly where a
subtle divergence hides, so this runs BOTH engines over the same inputs and
diffs the output.

Not a unit test -- it needs the adk-python checkout, so it lives in tests/tools/
and is run deliberately (e.g. after an ADK upgrade) rather than in the normal
suite. Result as of ADK d637d1b4: 20 cases, 0 divergences.

Setup: extracts _render_with_regex + _is_valid_state_name + the placeholder
regex verbatim into a shim, so it runs without pydantic. Two ADK calls to
removesuffix/removeprefix are backported to slices because this interpreter is
3.8 -- that is an interpreter-compatibility edit, NOT a logic change.

Usage:
  python3 tests/tools/differential_render.py [path/to/adk-python]
Exit code is the number of divergences.
"""
import asyncio
import pathlib
import re
import sys
import tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

_ADK = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    pathlib.Path.home() / "Documents/Git/adk-python")
_SHIM = pathlib.Path(tempfile.mkdtemp()) / "adk_engine.py"


def _build_shim() -> None:
    """Lift ADK's engine verbatim so it imports without pydantic."""
    src = _ADK / "src/google/adk/utils/instructions_utils.py"
    if not src.is_file():
        sys.exit(f"adk-python not found at {_ADK}; pass the path as argv[1]")
    t = src.read_text()
    body = re.search(
        r"async def _render_with_regex\(.*?(?=\nasync def |\ndef _render_with_jinja2)",
        t, re.S)
    valid = re.search(r"def _is_valid_state_name\(.*?(?=\ndef |\nasync def |\Z)", t, re.S)
    pattern = re.search(r"_TEMPLATE_VAR_PATTERN = re\.compile\([^\n]+\)", t)
    if not (body and valid and pattern):
        sys.exit("could not extract ADK engine; instructions_utils.py has changed shape")
    shim = (
        "import logging, re\n"
        "logger = logging.getLogger(__name__)\n"
        f"{pattern.group(0)}\n"
        "class State:\n"
        "    APP_PREFIX = 'app:'\n"
        "    USER_PREFIX = 'user:'\n"
        "    TEMP_PREFIX = 'temp:'\n\n"
        + valid.group(0) + "\n\n"
        + body.group(0).replace("readonly_context: ReadonlyContext,", "readonly_context,")
    )
    # Interpreter compatibility only -- this box is 3.8, ADK targets 3.9+.
    shim = shim.replace("var_name.removesuffix('?')", "var_name[:-1]")
    shim = shim.replace("var_name.removeprefix('artifact.')", "var_name[len('artifact.'):]")
    _SHIM.write_text(shim)
    sys.path.insert(0, str(_SHIM.parent))


_build_shim()

from adkl.instruction import render as adkl_render  # noqa: E402
from adk_engine import _render_with_regex as adk_render  # noqa: E402

class FakeSession:
    def __init__(self, state): self.state = state
    app_name="a"; user_id="u"; id="s"
class FakeInv:
    def __init__(self, state):
        self.session = FakeSession(state); self.artifact_service = None
class FakeCtx:
    def __init__(self, state):
        self._invocation_context = FakeInv(state)
        self.agent_name = "agent"
        self.state = state

CASES = [
    ("Hi {name}", {"name": "Lu"}),
    ("Hi {name?}!", {}),
    ("Hi {name}!", {"name": None}),
    ("a {my-key} b", {}),
    ("{has space}", {}),
    ("{app:tier}/{user:id}/{temp:x}", {"app:tier":"g","user:id":"u1","temp:x":"t"}),
    ("no placeholders", {}),
    ("{n} {b}", {"n": 42, "b": True}),
    ("{a} {a?} {a}", {"a": "x"}),
    ("{ spaced }", {"spaced": "v"}),
    ("{{double}}", {"double": "d"}),
    ("{empty}", {"empty": ""}),
    ("{zero}", {"zero": 0}),
    ("{list}", {"list": [1,2]}),
    ("trailing {", {}),
    ("} leading", {}),
    ("{a}{b}", {"a":"1","b":"2"}),
    ("{9bad}", {}),
    ("{_ok}", {"_ok":"y"}),
    ("{temp:}", {}),
]

async def main():
    diffs = 0
    for tmpl, state in CASES:
        try:
            mine = await adkl_render(tmpl, state)
        except Exception as e:
            mine = f"<{type(e).__name__}>"
        try:
            theirs = await adk_render(tmpl, FakeCtx(state))
        except Exception as e:
            theirs = f"<{type(e).__name__}>"
        flag = "OK " if mine == theirs else "DIFF"
        if mine != theirs: diffs += 1
        print(f"{flag} {tmpl!r:34} mine={mine!r:22} adk={theirs!r}")
    print(f"\n{len(CASES)} cases, {diffs} divergence(s)")
    return diffs

sys.exit(asyncio.get_event_loop().run_until_complete(main()))
