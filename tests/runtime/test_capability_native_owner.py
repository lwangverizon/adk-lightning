"""Opt-in native skill ownership does not install a second loader or policy."""
import asyncio

import pytest
from google.adk.agents import LlmAgent
from google.adk.skills import Frontmatter, Skill
from google.adk.tools.skill_toolset import SkillToolset

from adkl.capability_bindings import CapabilityBindings
from tests.support.native import run
from tests.support.capabilities import ChoiceModel, workflow


def test_native_skill_owner_and_guarded_workflow_coexist():
    from adkl.capability_policy import CapabilityDecision

    class Policy:
        def expose(self, context, catalog):
            return []

        def authorize(self, context, request):
            return CapabilityDecision(False)

    skill = Skill(frontmatter=Frontmatter(name="records", description="Records"),
                  instructions="Read records")
    native = SkillToolset(skills=[skill])
    calls = []
    binding = CapabilityBindings(skills=[skill], native_toolset=native,
                                 workflows=[workflow(lambda ctx, node_input: calls.append(True))],
                                 policy=Policy())
    kwargs = binding.agent_kwargs()
    assert kwargs["tools"][0] is native
    assert binding.inspect()["skillPolicyOwnership"] == "host"
    assert binding.inspect()["autoActivation"] == "host_owned"
    model = ChoiceModel(model="fixture", choice="workflow")
    try:
        asyncio.run(run(LlmAgent(name="host", model=model, **kwargs)))
    except ValueError as exc:
        assert "not found" in str(exc)
    assert not calls
    assert "device_discovery" not in model.requests[0].tools_dict


def test_native_skill_owner_declarations_must_match():
    skill = Skill(frontmatter=Frontmatter(name="records", description="Records"),
                  instructions="Read records")
    native = SkillToolset(skills=[skill])
    with pytest.raises(ValueError, match="must match"):
        CapabilityBindings(native_toolset=native)
    with pytest.raises(ValueError, match="Configure max_activated_skills"):
        CapabilityBindings(skills=[skill], native_toolset=native, max_activated_skills=3)
    with pytest.raises(TypeError, match="native SkillToolset"):
        CapabilityBindings(native_toolset=object())
