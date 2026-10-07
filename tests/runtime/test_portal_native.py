"""Offline export from a real isolated native host worker; no live model call."""
from pathlib import Path
import shutil
import sys

from adkl import portal


def test_native_portal_export_contains_real_declarations_and_doctor_coverage(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'projects/native-agent'
    root = tmp_path / 'host'
    shutil.copytree(source, root, ignore=shutil.ignore_patterns('.venv', '.adkl', '__pycache__', 'vendor', 'uv.lock'))
    (root / '.venv').symlink_to(sys.prefix, target_is_directory=True)
    (root / 'uv.lock').write_text('fixture lock')
    result = portal.export(root, agent_id='native', name='Native', artifact='fixture-artifact',
                           sdk_revision='fixture-sdk', inspect=True)
    assert result['inventory']['scope'] == 'native_declarations'
    assert result['inventory']['skills']
    assert result['inventory']['tools']
    assert all(s['instructions'] is None for s in result['inventory']['skills'])
    check = next(c for c in result['report']['checks'] if c['id'] == 'native')
    assert check['status'] in {'passed', 'findings'}, result['report']
    assert result['report']['freshness'] == 'current'
    assert result['identity']['deployment'] == 'not_verified'
