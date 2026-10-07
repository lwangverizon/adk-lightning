"""Closed disclosure and exact ownership checks, including literal tool schemas."""
import copy
import json
import secrets

import pytest
import yaml

from adkl.connected import Target, Unavailable, validate
from tests.test_connected import declaration


def rich():
    data = declaration()
    node = data['agents'][0]
    group = {'id': 'set/0', 'owner': node['id'], 'type': 'SkillToolset',
             'availability': 'dynamic; not evaluated', 'skills': [], 'tools': []}
    group['skills'] = [{'id': 'skill/0', 'owner': group['id'], 'name': 'test',
                        'description': 'Approved', 'instructions': {'kind': 'static', 'text': 'Test'},
                        'requestedTools': ['lookup'], 'resources': 'omitted',
                        'source': 'loaded', 'editable': False}]
    group['tools'] = [{'id': 'tool/0', 'owner': group['id'], 'name': 'lookup',
                       'description': 'Approved', 'type': 'FunctionTool',
                       'schemaStatus': 'native_function_declaration',
                       'schema': {'name': 'lookup', 'parameters_json_schema': {
                           'type': 'object', 'properties': {'query': {'type': 'string'}},
                           'required': ['query'], 'additionalProperties': False}}}]
    node['toolsets'] = [group]
    return data


def check(data):
    target = Target('prospect', 'Prospect', 'uat',
                    'https://host/internal/adkl/v1/agents/prospect/declarations', secrets.token_hex(32))
    return validate(json.dumps(data).encode(), target)


def test_rich_closed_schema_accepts_native_shape():
    data = rich()
    assert check(data) == data


@pytest.mark.parametrize('location', ['root', 'node', 'group', 'skill', 'tool', 'instruction', 'schema'])
def test_undeclared_fields_never_forwarded(location):
    data = rich()
    node = data['agents'][0]
    group = node['toolsets'][0]
    targets = {'root': data, 'node': node, 'group': group,
               'skill': group['skills'][0], 'tool': group['tools'][0],
               'instruction': node['instruction'],
               'schema': group['tools'][0]['schema']['parameters_json_schema']}
    targets[location]['debugContext'] = {'sessionState': 'must not escape'}
    with pytest.raises(Unavailable):
        check(data)


@pytest.mark.parametrize('change', ['duplicate_tool', 'duplicate_skill', 'duplicate_group',
                                   'cross_kind', 'wrong_owner', 'wrong_group_owner',
                                   'child_ref', 'tool_agent_ref'])
def test_reference_integrity(change):
    data = rich()
    node = data['agents'][0]
    group = node['toolsets'][0]
    if change == 'duplicate_tool':
        group['tools'].append(copy.deepcopy(group['tools'][0]))
    elif change == 'duplicate_skill':
        group['skills'].append(copy.deepcopy(group['skills'][0]))
    elif change == 'duplicate_group':
        node['toolsets'].append(copy.deepcopy(group))
    elif change == 'cross_kind':
        group['skills'][0]['id'] = group['tools'][0]['id']
    elif change == 'wrong_owner':
        group['tools'][0]['owner'] = node['id']
    elif change == 'wrong_group_owner':
        group['owner'] = 'missing'
    elif change == 'child_ref':
        node['children'] = ['tool/0']
    else:
        group['tools'][0]['agent'] = 'missing'
    with pytest.raises(Unavailable):
        check(data)


def test_graph_cycles_are_valid_references_not_recursion():
    data = rich()
    data['agents'][0]['children'] = ['agents/0']
    assert check(data)['root'] == 'agents/0'


@pytest.mark.parametrize('body', [b'{"schemaVersion":1,"schemaVersion":1}',
                                 b'{"x":NaN}', b'{"x":1e999}'])
def test_duplicate_keys_and_nonfinite_numbers_refused(body):
    target = Target('prospect', 'Prospect', 'uat',
                    'https://host/internal/adkl/v1/agents/prospect/declarations', secrets.token_hex(32))
    with pytest.raises(Unavailable):
        validate(body, target)


def test_schema_external_ref_refused_but_declared_literal_property_allowed():
    data = rich()
    schema = data['agents'][0]['toolsets'][0]['tools'][0]['schema']['parameters_json_schema']
    schema['properties']['debugContext'] = {'type': 'string', 'default': 'Approved literal'}
    check(data)  # A declared property name is not a new host envelope field.
    schema['properties']['debugContext'] = {'$ref': 'https://external/schema'}
    with pytest.raises(Unavailable):
        check(data)


def test_connected_ci_build_keeps_required_stage_gates():
    from pathlib import Path
    config = yaml.safe_load((Path(__file__).parents[1] / '.gitlab-ci.yml').read_text())
    job = config['connected-portal-review-build']
    assert job['stage'] == 'build'
    assert job['extends'] == '.flutter'
    assert 'needs' not in job
    assert 'lib/connected_main.dart' in '\n'.join(job['script'])
    assert 'artifact_manifest.py verify' in '\n'.join(job['script'])
