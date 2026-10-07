"""Compatibility aliases must not change retrieval or pretend to enforce policy."""
import pytest

from adkl.features import FeatureError, resolve
from adkl.retrieval import config_from_dict


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("option", ["lexical", "bm25"])
def test_lexical_option_and_legacy_alias_reach_runtime(option, value):
    result = resolve({"pipeline": ["skill_retrieval"], "skill_retrieval": {option: value}})
    assert config_from_dict(result.config["skill_retrieval"]).lexical is value
    assert any("bm25 is deprecated" in warning for warning in result.warnings) == (option == "bm25")


def test_conflicting_aliases_fail():
    with pytest.raises(FeatureError, match="disagree"):
        resolve({"skill_retrieval": {"bm25": False, "lexical": True}})


def test_inert_pair_option_warns_only_when_written():
    assert not any("preserve_tool_pairs" in w for w in resolve({}).warnings)
    result = resolve({"history_slicing": {"preserve_tool_pairs": False}})
    assert any("deprecated and inert" in w for w in result.warnings)
