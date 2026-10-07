"""Native ADK bindings for the shared display-only presentation packs."""
import inspect

from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.skill_toolset import SkillToolset

from adkl import presentation as P


def render_greeting(tool_context: ToolContext, greeting: str, subtitle: str,
                    emoji: str = "👋", stars: int = 0, streak: int = 0) -> dict:
    """Stage a greeting. The project's agent policy determines when it is appropriate."""
    return _stage(tool_context, "greeting", greeting=greeting, subtitle=subtitle,
                  emoji=emoji, stars=stars, streak=streak)


def render_fact_card(tool_context: ToolContext, title: str, text: str, icon: str = "✨") -> dict:
    """Stage a fact or definition card with brief companion text."""
    return _stage(tool_context, "fact", title=title, text=text, icon=icon)


def render_steps(tool_context: ToolContext, steps: list[str]) -> dict:
    """Stage two to six ordered teaching or procedural steps."""
    return _stage(tool_context, "step", steps=steps)


def render_celebration(tool_context: ToolContext, message: str) -> dict:
    """Stage a celebration for a verified achievement or correct answer."""
    return _stage(tool_context, "celebration", message=message)


def render_cpa_lesson(tool_context: ToolContext, topic: str, standard: str, stages: list[dict]) -> dict:
    """Stage concrete, pictorial, abstract stages using retrieved curriculum data.

    stages must contain exactly three objects in this order, with stage values
    "concrete", "pictorial", "abstract". Each needs nonempty string explanation
    and visual fields (use text or emoji, not nested objects), and may include
    teacherTip. topic and standard must also be nonempty strings.
    """
    return _stage(tool_context, "cpa_lesson", topic=topic, standard=standard, stages=stages)


def present_quiz(tool_context: ToolContext, question: str, options: list[dict]) -> dict:
    """Stage a display-only choice question; options contain label and text.

    This tool does not select bank questions, grade answers, or record progress.
    Prefer a configured domain quiz tool when available.
    """
    return _stage(tool_context, "quiz", question=question, options=options)


def present_navigation(tool_context: ToolContext, title: str, text: str, label: str, url: str) -> dict:
    """Stage a navigation preview using an approved destination from domain data."""
    return _stage(tool_context, "navigation", title=title, text=text, label=label, url=url)


def present_products(tool_context: ToolContext, title: str, products: list[dict]) -> dict:
    """Stage retrieved products, each with name, description, and display price.

    Never invent prices, availability, or eligibility. This does not purchase.
    """
    return _stage(tool_context, "product_list", title=title, products=products)


BUILTINS = {
    "greeting": render_greeting, "fact": render_fact_card, "step": render_steps,
    "celebration": render_celebration, "cpa_lesson": render_cpa_lesson,
    "quiz": present_quiz, "navigation": present_navigation, "product_list": present_products,
}


def _stage(context, kind, **payload):
    try:
        return P.stage(context, {"type": kind, **payload}, {kind})
    except P.PresentationError as exc:
        return {"status": "error", "agent_instruction": str(exc)}


class _CaptureContext:
    """A bound tool still owns domain state; ADKL owns its presentation output."""
    def __init__(self, context):
        self._context = context

    def __getattr__(self, name):
        return getattr(self._context, name)

    def render_ui_widget(self, widget):
        # Delivery is taken from the explicit successful result binding below.
        # A copied tool may register before returning; do not deliver it twice.
        pass


class BoundResultTool(FunctionTool):
    def __init__(self, fn, binding, kinds):
        super().__init__(fn)
        self.binding = binding
        self.kinds = kinds

    async def run_async(self, *, args, tool_context):
        result = await super().run_async(args=args, tool_context=_CaptureContext(tool_context))
        if not isinstance(result, dict) or result.get("status") not in {"rendered", "success"}:
            return result
        payload = result.get(self.binding["result_field"])
        try:
            if not isinstance(payload, dict) or payload.get("type") != self.binding["kind"]:
                raise P.PresentationError("bound tool returned the wrong presentation kind")
            staged = P.stage(tool_context, payload, self.kinds)
        except P.PresentationError as exc:
            return {"status": "error", "agent_instruction": f"Presentation failed: {exc}"}
        # A bound legacy tool's skip flag must not eliminate the companion pass.
        tool_context.actions.skip_summarization = False
        # Replace the source field too: a binding may use a name other than
        # "card", and must not return an unsanitized parallel copy.
        return {**result, self.binding["result_field"]: staged["card"], **staged}


class PresentationSkillToolset(SkillToolset):
    """Deduplicate agent-level pack tools from the dynamically loaded skill set."""
    def __init__(self, *, presentation_names, **kwargs):
        super().__init__(**kwargs)
        self.presentation_names = presentation_names

    async def get_tools(self, readonly_context=None):
        return [t for t in await super().get_tools(readonly_context)
                if t.name not in self.presentation_names]


def tools(config, domain_tools):
    """Build explicit pack tools and adapters. No ambient/global registry state."""
    result = {}
    bound_kinds = {b["kind"] for b in config["bindings"].values()}
    for kind in sorted(config["kinds"]):
        if kind in bound_kinds:
            continue
        fn = BUILTINS[kind]
        result[fn.__name__] = FunctionTool(fn)
    for name, binding in config["bindings"].items():
        fn = domain_tools.get(name)
        if fn is None or not inspect.isfunction(fn):
            raise P.PresentationError(f"presentation binding tool {name!r} is unavailable")
        result[name] = BoundResultTool(fn, binding, config["kinds"])
    return result
