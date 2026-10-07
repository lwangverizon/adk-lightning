"""Lightweight synthetic projects and worker source; no ADK imports."""
import sys

import yaml


def make_project(root, name="local", target="local"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "execution.yaml").write_text(yaml.safe_dump({
        "version": 1, "default": name, "profiles": {name: {
            "target": target, "environment": "test", "secrets": ["GOOGLE_API_KEY"],
        }}}))
    (root / "agent.yaml").write_text("agent: {name: test, model: gemini-test}\n")
    (root / "uv.lock").write_text("synthetic lock\n")
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    return root


# Exercises real process/pipe/environment boundaries without ADK.
WORKER_CODE = '''import json, os, sys, pathlib, time
sys.path.insert(0, str(pathlib.Path.cwd()))
import collision
for line in sys.stdin:
    request = json.loads(line)
    if request['operation'] == 'hang':
        time.sleep(30)
    print(json.dumps({'value': collision.VALUE, 'cwd': os.getcwd(),
                      'secret': os.getenv('GOOGLE_API_KEY'),
                      'ambient': os.getenv('DATABASE_URL')}), flush=True)
'''
