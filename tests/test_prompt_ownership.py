"""Prompt checks reach Doctor/native audits without claiming runtime behavior."""
import json
from pathlib import Path

import pytest
import yaml

from adkl import doctor
from adkl.inspection import audit_snapshot
from adkl.lint_prompt import check_prompt

POLICY = ('Require verified authorization before disclosing private customer data '
          'or performing any account specific actions.')


def rules(prompt, skills=(), catalog=None, core=()):
    return {rule for _, rule, _, _ in check_prompt(prompt, skills, catalog, core)}


def test_exact_duplication_and_cross_layer_owner():
    assert 'PROMPT_POLICY_REPEATED' in rules(POLICY + '\n\n' + POLICY)
    assert 'PROMPT_SKILL_POLICY_DUPLICATED' in rules(
        POLICY, [('orders', POLICY, [])])
    assert not rules('Answer directly. Answer directly.')
    assert not rules(POLICY + '\n\n```\n' + POLICY + '\n```')


@pytest.mark.parametrize('text', [
    'Never call `load_skill`.',
    'If skills are available, call `load_skill`.',
    'The `load_skill` tool is not available.',
    '"Call `load_skill`." is a bad example.',
    '```\nCall `load_skill`.\n```',
])
def test_mentions_and_negative_or_conditional_directives_do_not_flag(text):
    assert not rules(text, catalog={'tools': []})


def test_inventory_unknown_implicit_and_declared_are_distinct():
    prompt = 'Call `load_skill` before answering.'
    assert not rules(prompt)  # No inventory: not evidence of absence.
    assert 'PROMPT_TOOL_NOT_IN_INVENTORY' in rules(prompt, catalog={'tools': []})
    assert not rules(prompt, [('orders', POLICY, [])], {'tools': []})
    assert not rules(prompt, catalog={'tools': [{'name': 'load_skill'}]})
    assert not rules(prompt, catalog={'tools': []}, core=['load_skill'])


@pytest.mark.parametrize('feature', ['Markdown', 'tables', 'bullet points'])
def test_explicit_surface_conflict(feature):
    assert 'PROMPT_SURFACE_CONFLICT' in rules(
        f'Use {feature}. Never use {feature}.')
    assert not rules(f'For web, use {feature}. For voice, never use {feature}.')
    assert not rules(f'Never use {feature}. Never use {feature}.')


def test_real_surface_override_and_skill_delegation_shapes():
    prompt = ('Use Markdown only — no HTML, JSON, or embedded markup.\n'
              '- Do NOT use markdown formatting (bold, italics, headers).')
    assert 'PROMPT_SURFACE_CONFLICT' in rules(prompt)
    delegation = 'The safety-and-refusal skill carries the exact refusal wording.'
    assert 'PROMPT_SKILL_LOADER_NOT_IN_INVENTORY' in rules(
        delegation, catalog={'tools': []})
    assert not rules(delegation)
    assert not rules(delegation, [('safety-and-refusal', 'Refuse.', [])], {'tools': []})
    assert not rules(delegation, catalog={'tools': [{'name': 'load_skill'}]})
    for mention in ('If available, ' + delegation, '```\n' + delegation + '\n```',
                    'Do not assume ' + delegation, '"' + delegation + '"'):
        assert not rules(mention, catalog={'tools': []})


def test_native_audit_uses_same_rules_without_evaluating_callable():
    snapshot = {'skills': [], 'tools': []}
    result = audit_snapshot(snapshot, 'Call `load_skill`.')
    assert 'PROMPT_TOOL_NOT_IN_INVENTORY' in {f['rule'] for f in result['findings']}

    def dynamic(_context):
        raise AssertionError('must not execute dynamic instruction')

    result = audit_snapshot(snapshot, dynamic)
    assert not result['findings']
    assert result['limitations'] == ['Dynamic instruction was not evaluated']


def test_doctor_reports_and_resolves_prompt_findings(tmp_path: Path):
    directory = tmp_path / 'skills' / 'answer'
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(
        '---\nname: answer\ndescription: Explain public facts.\n---\n' + POLICY,
        encoding='utf-8')
    (tmp_path / 'catalog.json').write_text(json.dumps({'tools': []}), encoding='utf-8')
    prompt = tmp_path / 'instruction.md'
    prompt.write_text(POLICY + '\n\n' + POLICY + '\nUse Markdown. Never use Markdown.',
                      encoding='utf-8')
    (tmp_path / 'agent.yaml').write_text(yaml.safe_dump({
        'version': 1,
        'agent': {'name': 'example', 'model': 'fixture', 'provider': 'gemini',
                  'instruction': {'template': 'instruction.md'}},
        'skills': {'include': ['answer']}, 'tools': {'catalog': 'catalog.json'},
        'eject': {'target': 'generated.py'},
    }), encoding='utf-8')
    before = doctor.diagnose(tmp_path)
    findings = [f for f in before['findings'] if f['rule'].startswith('PROMPT_')]
    assert {f['rule'] for f in findings} == {
        'PROMPT_POLICY_REPEATED', 'PROMPT_SKILL_POLICY_DUPLICATED',
        'PROMPT_SURFACE_CONFLICT'}
    for finding in findings:
        if not finding['skill']:
            assert not finding['location'] or 'skills/SKILL.md' not in finding['location']['path']
    brief = doctor.render(before)
    assert 'privacy' in brief
    assert POLICY not in brief  # No complete prompt copied into handoff.
    prompt.write_text('Answer directly.', encoding='utf-8')
    after = doctor.diagnose(tmp_path, previous=before)
    assert {f['id'] for f in findings} <= set(after['comparison']['resolved'])
    assert after['behavioralEvaluation'] == 'not_run'
