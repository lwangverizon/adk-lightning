"""Deterministic policy/compatibility tests, not model-quality evaluation."""
from dataclasses import FrozenInstanceError
from itertools import combinations, permutations

import pytest

from adkl import widgets
from adkl.presentation_contracts import Contract, Role, VISUAL_V1, VISUAL_V2, contract_for_roles, plan_for_roles


PRECEDENCE = [
    (Role.DATA_WITH_WRITTEN_TABLE, Contract.SYNTHESIS_WITH_TABLE),
    (Role.DATA_PRIMARY, Contract.SYNTHESIS),
    (Role.ACTION_WITH_STEPS, Contract.STEP_HANDOFF),
    (Role.INPUT_REQUEST, Contract.ELICITATION),
    (Role.NAVIGATION, Contract.NAVIGATION_HANDOFF),
    (Role.ACTION_PRIMARY, Contract.GUIDANCE),
    (Role.SUPPORTING, Contract.ANSWER),
]


@pytest.mark.parametrize('version', [VISUAL_V1, VISUAL_V2])
def test_every_role_combination_is_order_independent_and_never_loses_required_table(version):
    for size in range(1, len(PRECEDENCE) + 1):
        for combination in combinations(PRECEDENCE, size):
            expected = combination[0][1]
            for roles in permutations([item[0] for item in combination]):
                assert contract_for_roles(iter(roles)) == expected
            plan = plan_for_roles([item[0] for item in combination], version=version)
            assert plan.contract == expected
            assert len(plan.roles) == size
            if Role.INPUT_REQUEST in plan.roles and expected != Contract.ELICITATION:
                assert 'separate input request' in plan.text
            if Role.ACTION_PRIMARY in plan.roles and expected != Contract.GUIDANCE:
                assert 'accompanying' in plan.text


@pytest.mark.parametrize('version', [VISUAL_V1, VISUAL_V2])
def test_empty_unknown_and_explicit_version_boundaries(version):
    plan = plan_for_roles([], version=version)
    assert plan.contract is None
    assert plan.render() == plan.render(channel='tool_text') == ''
    for roles in [['dat_primary'], ['data_primary', 'unknown']]:
        with pytest.raises(ValueError):
            plan_for_roles(roles, version=version)
    with pytest.raises(ValueError):
        plan_for_roles([], version='latest')
    with pytest.raises(TypeError):
        plan_for_roles([])
    with pytest.raises(TypeError):
        plan_for_roles([], version=version, same_message='false')
    with pytest.raises(ValueError):
        plan.render(channel='html')


@pytest.mark.parametrize('role', list(Role))
def test_channels_share_exact_policy_and_only_explicit_coemission(role):
    plan = plan_for_roles([role.value], version=VISUAL_V1)
    block = plan.render()
    assert block.split('\n', 1)[1].rsplit('\n', 1)[0] == plan.render(channel='tool_text')
    assert block.count('<presentation_contract ') == 1
    assert 'version="visual-v1"' in block
    assert '<' not in plan.text
    assert 'same message' not in plan.text
    assert 'required disclosures' in plan.text
    assert 'staging is not evidence' in plan.text
    assert 'will render below' not in plan.text
    assert 'same message' in plan_for_roles([role], version=VISUAL_V1, same_message=True).text
    with pytest.raises(FrozenInstanceError):
        plan.text = 'changed'


def test_existing_widget_names_and_default_prompt_are_preserved():
    assert widgets.Role is Role
    assert widgets.Contract is Contract
    assert widgets.contract_for_roles(['unknown']) is None
    assert widgets.instruction_for(None) == ''
    assert widgets.instruction_for(Contract.SYNTHESIS) == (
        '\n\n<presentation_contract id="synthesis">\n'
        'A data widget will render below your reply. Synthesize over it -- lead '
        'with the comparison or recommendation the rows support. Do not list the '
        'rows back; the customer can already see them.\n</presentation_contract>')
    # The explicit new variant is supported on the ordinary widget path too.
    assert widgets.contract_for_roles([Role.DATA_PRIMARY, Role.DATA_WITH_WRITTEN_TABLE]) == Contract.SYNTHESIS_WITH_TABLE
    assert 'written comparison' in widgets.instruction_for(Contract.SYNTHESIS_WITH_TABLE)


def test_adopt_host_written_table_role_without_loss(monkeypatch):
    from types import SimpleNamespace
    source = SimpleNamespace(key='comparison', presentation_role='data_with_written_table',
                             build=lambda *args: [])
    monkeypatch.setattr(widgets, '_resolve_attr', lambda *args, **kwargs: [source])
    spec = widgets.adopt('fixture:specs')[0]
    assert spec.role is Role.DATA_WITH_WRITTEN_TABLE
    assert not spec.adoption_warnings
    from adkl.widget_lint import _practice
    assert any(f.level == 'WARN' and 'data_with_written_table' in f.message
               for f in _practice(spec))


@pytest.mark.parametrize('role', list(Role))
@pytest.mark.parametrize('same_message', [False, True])
def test_compact_policy_layout_and_channel_identity(role, same_message):
    plan = plan_for_roles([role], version=VISUAL_V2, same_message=same_message)
    assert plan.version == VISUAL_V2
    assert plan.contract == contract_for_roles([role])
    assert plan.text.startswith('Write the visible reply now.')
    # Output layout must be explicit, not merely newlines inside the prompt.
    assert 'blank lines' in plan.text
    assert 'each bullet or numbered step on its own line' in plan.text
    assert 'required disclosures and essential steps over brevity' in plan.text
    assert ('same message' in plan.text) is same_message
    if same_message:
        assert 'presentation-only' in plan.text
        assert 'wait for needed business results' in plan.text
    assert plan.render(channel='tool_text') == plan.text
    assert plan.render() == (
        f'<presentation_contract id="{plan.contract.value}" version="visual-v2">\n'
        f'{plan.text}\n</presentation_contract>')
    assert len(plan.text.split()) <= (80 if same_message else 60)
    assert 'will render' not in plan.text
    assert 'below' not in plan.text


def test_compact_policy_mixed_roles_stays_small_and_keeps_responsibilities():
    for size in range(1, len(Role) + 1):
        for roles in combinations(list(Role), size):
            for coemit in [False, True]:
                old = plan_for_roles(roles, version=VISUAL_V1, same_message=coemit)
                new = plan_for_roles(roles, version=VISUAL_V2, same_message=coemit)
                assert new.roles == old.roles
                assert new.contract == old.contract
                assert len(new.text.split()) < len(old.text.split())
                # New mixed navigation obligation is additive; old role sets
                # retain their original budget and exact policy text.
                assert len(new.text.split()) <= (135 if Role.NAVIGATION in roles else 100)
                assert len(new.text) <= (1100 if Role.NAVIGATION in roles else 800)


def test_compact_policy_keeps_short_answer_and_table_shapes():
    for role in [Role.INPUT_REQUEST, Role.ACTION_WITH_STEPS, Role.SUPPORTING]:
        text = plan_for_roles([role], version=VISUAL_V2).text
        assert 'two' in text
        assert 'heading' not in text.lower()
        assert 'five bullet' not in text.lower()
    text = plan_for_roles([Role.DATA_WITH_WRITTEN_TABLE], version=VISUAL_V2).text
    for requirement in ['written markdown table', 'units and periods', 'unknowns']:
        assert requirement in text


def test_navigation_is_explicit_and_preserved_by_adoption(monkeypatch):
    from types import SimpleNamespace
    from adkl import presentation
    from adkl.presentation_contracts import navigation_handoff_guidance

    source = SimpleNamespace(key='nav', presentation_role='navigation',
                             build=lambda *args: [])
    monkeypatch.setattr(widgets, '_resolve_attr', lambda *args, **kwargs: [source])
    spec = widgets.adopt('fixture:specs')[0]
    assert spec.role is Role.NAVIGATION
    assert not spec.adoption_warnings
    assert presentation.ROLES['navigation'] is Role.NAVIGATION
    assert widgets.contract_for_roles([spec.role]) is Contract.NAVIGATION_HANDOFF
    assert navigation_handoff_guidance() in widgets.instruction_for(Contract.NAVIGATION_HANDOFF)
    assert widgets.contract_for_roles([Role.ACTION_PRIMARY]) is Contract.GUIDANCE


def test_instruction_size_is_bounded_not_a_quality_score():
    for size in range(1, len(Role) + 1):
        for roles in combinations(list(Role), size):
            plan = plan_for_roles(roles, version=VISUAL_V1, same_message=True)
            assert len(plan.text.split()) <= 240
            assert len(plan.text) <= 1800
