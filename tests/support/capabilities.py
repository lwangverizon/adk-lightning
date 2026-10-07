"""Synthetic native capabilities, not test cases or host business policy."""
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.skills import Frontmatter, Skill
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel, Field

from adkl.capability_bindings import CapabilityBindings


class Inputs(BaseModel):
    query: str


class ChoiceModel(BaseLlm):
    choice: str
    requests: list = Field(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        self.requests.append(request.model_copy(deep=True))
        if self.choice == "specialist":
            part = types.Part(text="Specialist answer")
        elif len(self.requests) > 1:
            part = types.Part(text="Parent answer")
        else:
            name, args = {
                "skill": ("load_skill", {"skill_name": "policy-help"}),
                "workflow": ("device_discovery", {"query": "camera phones"}),
                "agent": ("transfer_to_agent", {"agent_name": "specialist"}),
            }[self.choice]
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def fixtures(choice, *, deny=False):
    calls = []
    def search(ctx, node_input: Inputs):
        calls.append(node_input.query)
        return {"devices": ["phone-a"]}
    flow = Workflow(name="device_discovery", description="Find and compare devices",
                    input_schema=Inputs, edges=[("START", FunctionNode(name="search", func=search))])
    skill = Skill(frontmatter=Frontmatter(name="policy-help", description="Explain shopping policies"),
                  instructions="PRIVATE_SKILL_BODY: explain returns accurately.")
    specialist_model = ChoiceModel(model="synthetic", choice="specialist")
    specialist = LlmAgent(name="specialist", description="Diagnose service problems",
                          instruction="PRIVATE_SPECIALIST_BODY", model=specialist_model)
    binding = CapabilityBindings(skills=[skill], workflows=[flow], sub_agents=[specialist])
    def guard(tool, args, tool_context):
        if deny and tool.name == "device_discovery":
            return {"error": "denied"}
    model = ChoiceModel(model="synthetic", choice=choice)
    agent = LlmAgent(name="prospect", model=model, instruction="Help the user.",
                    **binding.agent_kwargs(callbacks={"before_tool_callback": guard}))
    return binding, agent, model, specialist_model, calls


def workflow(operation):
    return Workflow(name="device_discovery", description="Find devices",
                    input_schema=Inputs,
                    edges=[("START", FunctionNode(name="lookup", func=operation))])
