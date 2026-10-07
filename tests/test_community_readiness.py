"""Community entry-point and verification-only pipeline contracts."""
import json
from pathlib import Path
import re

import pytest
import yaml

from adkl import agent_schema
from adkl.gateway import get_provider

ROOT = Path(__file__).resolve().parents[1]


def admitted(rules, env):
    """Evaluate the deliberately small GitLab comparison subset used here.

    GitLab CI Lint remains required for authoritative validation; reject unknown
    expression syntax instead of silently accepting a new untested condition.
    """
    def value(token):
        return env.get(token[1:], "") if token.startswith("$") else json.loads(token)
    def term(expression):
        match = re.fullmatch(r'(\$\w+)(?: (==|!=) (\$\w+|"[^"]*"))?', expression)
        assert match, expression
        left, operator, right = match.groups()
        if operator is None:
            return bool(value(left))
        return (value(left) == value(right)) if operator == "==" else (value(left) != value(right))
    for rule in rules:
        if "if" not in rule or all(term(t) for t in rule["if"].split(" && ")):
            return rule.get("when") != "never"
    return False


@pytest.mark.parametrize("source,branch,open_mrs,source_project,tag,expected", [
    ("push", "feature", "", "1", "", True),
    ("push", "feature", "5", "1", "", False),
    ("push", "main", "", "1", "", True),
    ("merge_request_event", "", "", "1", "", True),
    ("merge_request_event", "", "", "2", "", False),
    ("push", "", "", "1", "v0.1.0", False),
    ("schedule", "main", "", "1", "", True),
    ("schedule", "feature", "", "1", "", False),
    ("web", "feature", "", "1", "", True),
    ("api", "main", "", "1", "", False),
    ("trigger", "main", "", "1", "", False),
])
def test_community_pipeline_admission(source, branch, open_mrs, source_project, tag, expected):
    config = yaml.safe_load((ROOT / ".gitlab/ci/community.yml").read_text())
    env = {"CI_PIPELINE_SOURCE": source, "CI_COMMIT_BRANCH": branch,
           "CI_DEFAULT_BRANCH": "main", "CI_OPEN_MERGE_REQUESTS": open_mrs,
           "CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": source_project,
           "CI_COMMIT_TAG": tag, "CI_COMMIT_REF_PROTECTED": "true"}
    assert admitted(config["workflow"]["rules"], env) is expected
    assert not admitted(config["sdk-publish"]["rules"], env)


def test_community_reuses_required_gates_without_enabling_publication():
    config = yaml.safe_load((ROOT / ".gitlab/ci/community.yml").read_text())
    base = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
    assert config["include"] == [{"local": "/.gitlab-ci.yml"}]
    assert set(config) == {"include", "workflow", "sdk-publish"}
    for name in ("python-lint", "secret-scan", "components", "native-adk-contract",
                 "console", "dependency-audit", "sdk-onboarding"):
        assert name in base
        assert base[name].get("allow_failure", False) is False
    # The shared origin configuration remains opt-in, not implicitly activated.
    assert all("merge_request_event" not in r.get("if", "") for r in base["workflow"]["rules"])


def test_launcher_uses_maintained_example():
    config = json.loads((ROOT / ".claude/launch.json").read_text())
    args = config["configurations"][0]["runtimeArgs"]
    root = ROOT / args[args.index("--root") + 1]
    assert (root / "agent.yaml").is_file()
    assert root.name in json.loads((ROOT / "projects/examples.json").read_text())["projects"]


def test_new_schema_template_explicitly_selects_public_provider():
    doc = yaml.safe_load(agent_schema.TEMPLATE)
    assert get_provider(doc["agent"]["provider"]).name == "gemini"
    # Avoid silently rerouting existing deployments that omit the field.
    assert get_provider(None).name == "gateway"
