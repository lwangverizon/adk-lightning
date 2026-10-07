"""Host injects shared policy once in a real native Runner; no model-quality claim."""
import asyncio

import pytest

from google.adk.agents import LlmAgent
from google.adk.tools.skill_toolset import SkillToolset

from adkl.bindings import RuntimeBindings
from adkl.presentation_contracts import VISUAL_V1, VISUAL_V2, plan_for_roles
from adkl.widgets import WidgetSpec
from tests.support.native import ScriptModel, run


@pytest.mark.parametrize('version', [VISUAL_V1, VISUAL_V2])
@pytest.mark.parametrize('roles', [
    ['data_primary', 'input_request'], ['navigation'],
    ['data_primary', 'navigation', 'input_request'],
])
def test_native_host_injects_explicit_visual_policy_without_sdk_duplicate(version, roles):
    native = SkillToolset(skills=[])
    plan = plan_for_roles(roles, version=version)
    def inject(callback_context, llm_request):
        # Host already evaluated eligibility. No SDK state resolver or mutation.
        llm_request.append_instructions([plan.render()])
    spec = WidgetSpec('fixture', 'fixture', 'card', lambda *args: [])
    binding = RuntimeBindings(skills=[], native_toolset=native, widget_specs=[spec],
        feature_config={'pipeline': ['staged_widgets'], 'staged_widgets': {'inject_contract': False}})
    model = ScriptModel()
    kwargs = binding.merge_into({'name': 'host', 'model': model, 'tools': [native],
        'instruction': 'Host consent and disclosure policy', 'before_model_callback': [inject]})
    agent = LlmAgent(**kwargs)
    asyncio.run(run(agent, {}))
    assert model.requests
    for request in model.requests:
        assert request.count('<presentation_contract ') == 1
        assert plan.render() in request
        assert 'Host consent and disclosure policy' in request
        if 'input_request' in roles:
            assert 'separate input request' in request
        if roles == ['navigation']:
            assert plan.contract.value == 'navigation_handoff'
            assert "button replace navigation directions" in request
        elif 'navigation' in roles:
            assert 'navigation buttons replace directions' in request
        if version == VISUAL_V2:
            assert 'blank lines' in request
            assert 'each bullet or numbered step on its own line' in request
    assert kwargs['tools'] == [native]
