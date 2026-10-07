"""An external-style native constructor: synthetic model, real SDK lifecycle."""
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import load_skill_from_dir
from pathlib import Path
from google.adk.tools import ToolContext
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.widgets import WidgetSpec, Role, stage


async def build_card(context, items, request_id):
    return items


CARD = WidgetSpec(key="demo_card", register="temp:demo_card", role=Role.SUPPORTING,
                  widget_id="demo", payload_type="a2ui",
                  build=build_card)


def show_steps(version: str, tool_context: ToolContext) -> dict:
    """Stage a sample card. version must be v0.9 or v1.0; no external side effects."""
    if version not in {"v0.9", "v1.0"}:
        return {"status": "error", "message": "Use v0.9 or v1.0."}
    operations = [
        {"version": version, "createSurface": {
            "surfaceId": "demo", "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json"}},
        {"version": version, "updateComponents": {"surfaceId": "demo", "components": [
            {"id": "root", "component": "Card", "child": "steps"},
            {"id": "steps", "component": "Column", "children": ["title", "first", "second"]},
            {"id": "title", "component": "Text", "text": "Staged UI demo"},
            {"id": "first", "component": "Text", "text": "1. A domain tool stages data."},
            {"id": "second", "component": "Text", "text": "2. ADKL emits and renders the card."},
        ]}},
    ]
    stage(tool_context.state, CARD, [{"type": "a2ui", "operations": operations}])
    return {"status": "staged", "version": version}


class DemoModel(BaseLlm):
    """Scripted fixture; demonstrates execution, not language-model quality."""
    model: str = "offline-demo"

    async def generate_content_async(self, request, stream=False):
        last = request.contents[-1]
        if any(part.function_response for part in last.parts or []):
            part = types.Part(text="The card was staged by the tool and emitted by ADKL.")
        else:
            text = " ".join(part.text or "" for part in last.parts or [])
            part = (types.Part(text="No new card this turn.") if "thanks" in text.lower()
                    else types.Part(function_call=types.FunctionCall(name="render_steps",
                        args={"steps": ["Gather facts", "Review facts"]})) if "pack" in text.lower()
                    else types.Part(function_call=types.FunctionCall(
                        name="show_steps", args={"version": "v1.0" if "1.0" in text else "v0.9"})))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


class DemoEmbedder:
    async def embed(self, text):
        return [1.0, 0.0]  # Deterministic single-skill fixture, not calibrated retrieval.


async def prepare_context(*, skill_name, context, reason):
    context.state["demo_source"] = "Synthetic example data"


def build():
    skill = load_skill_from_dir(Path(__file__).parent / "skills/display")
    binding = RuntimeBindings(
        skills=[skill], tools=[show_steps], widget_specs=[CARD],
        presentation={"version": 1, "packs": ["adkl/common@1"]},
        vectors={"display": [1.0, 0.0]}, embedder=DemoEmbedder(),
        prepare_skill=prepare_context,
        feature_config={
            "pipeline": ["skill_retrieval", "history_slicing", "skill_state_injection", "staged_widgets"],
            # This synthetic demo exercises selector-driven activation even
            # with one skill. Normal small catalogs use prompt descriptions.
            "skill_retrieval": {"auto_activate": True, "min_skills_for_retrieval": 0},
            "history_slicing": {"recent_turns": 2},
            "skill_state_injection": {"skills": {"display": {
                "source": {"from_state": "demo_source", "type": "string", "required": True}}}},
        })
    return LlmAgent(name="native_agent", model=DemoModel(),
                    instruction="Demonstrate shared runtime behavior with synthetic data.",
                    **binding.agent_kwargs())
