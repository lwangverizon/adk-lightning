"""Opt-in pipeline boundaries, without changing required jobs or publishing."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_only_explicit_main_web_runs_and_protected_release_pushes_are_admitted():
    config = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
    rules = config["workflow"]["rules"]
    # This intentionally locks the small admission policy, not a partial clone
    # of GitLab's expression evaluator. Project CI Lint validates its syntax.
    assert rules == [
        {"if": '$CI_PIPELINE_SOURCE == "web" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'},
        {"if": '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG =~ /^v[0-9]+\\.[0-9]+\\.[0-9]+(-rc\\.[0-9]+)?$/ && $CI_COMMIT_REF_PROTECTED == "true"'},
        {"when": "never"},
    ]


def test_opt_in_does_not_turn_required_jobs_into_manual_or_allowed_failures():
    config = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
    jobs = {name: job for name, job in config.items()
            if isinstance(job, dict) and "stage" in job}
    assert {"python-lint", "secret-scan", "native-adk-contract", "sdk-publish"} <= jobs.keys()
    for name, job in jobs.items():
        assert not job.get("allow_failure"), name
        assert job.get("when") != "manual", name
        assert all(rule.get("when") != "manual" and not rule.get("allow_failure")
                   for rule in job.get("rules", [])), name
    assert config["sdk-publish"]["rules"] == [
        {**config["workflow"]["rules"][1], "when": "on_success"},
        {"when": "never"},
    ]
