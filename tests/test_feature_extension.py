"""Feature additions must retain validation, isolation and lightweight imports."""
import copy
import pathlib
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import capabilities, features  # noqa: E402


class TestFeatureExtension(unittest.TestCase):
    def test_new_declaration_validates_merged_options_without_enabling_runtime(self):
        calls = []

        def validate(config):
            calls.append(copy.deepcopy(config))
            if not config["labels"]:
                raise features.FeatureError("labels must not be empty")

        spec = features.Spec(
            "future", features.Kind.BEFORE_MODEL, "Synthetic extension",
            options={"labels": ("list", ["default"], "Example labels")},
            validate=validate,
        )
        with patch.dict(features.REGISTRY, {spec.name: spec}):
            resolved = features.resolve({"future": {}, "pipeline": []})
            self.assertEqual(calls, [{"labels": ["default"]}])
            self.assertNotIn("future", resolved.active)
            with self.assertRaisesRegex(features.FeatureError, "labels must not be empty"):
                features.resolve({"future": {"labels": []}, "pipeline": []})
            active = features.resolve({"pipeline": ["future"]})
            for target in ("preview", "sdk", "export"):
                with self.subTest(target=target), self.assertRaisesRegex(features.FeatureError, "future"):
                    capabilities.require_supported(active, target)

    def test_invalid_support_metadata_fails_when_declared(self):
        for options in ({"support_status": "ready"}, {"support_detail": " "},
                        {"callback_adapter": True}, {"validate": "function_name"}):
            with self.subTest(options=options), self.assertRaises(features.FeatureError):
                features.Spec("future", features.Kind.TOOLSET, "Example", **options)

    def test_resolved_defaults_are_owned_by_each_agent(self):
        before = copy.deepcopy(features.resolve({}).config)
        first, second = features.resolve({}), features.resolve({})
        first.config["skill_retrieval"]["excluded_skills"].append("one-agent-only")
        first.config["tool_result_projection"]["tools"]["lookup"] = {"fields": ["id"]}
        self.assertEqual(second.config, before)
        self.assertEqual(features.resolve({}).config, before)

    def test_nested_declared_options_do_not_alias_input_or_other_agents(self):
        declaration = {"skill_state_injection": {"skills": {
            "checkout": {"cart": {"from_state": "cart", "required": True}}
        }}}
        before = copy.deepcopy(declaration)
        first, second = features.resolve(declaration), features.resolve(declaration)
        first.config["skill_state_injection"]["skills"]["checkout"]["cart"]["required"] = False
        self.assertEqual(declaration, before)
        self.assertTrue(second.config["skill_state_injection"]["skills"]["checkout"]["cart"]["required"])

    def test_console_default_values_do_not_modify_future_agent_defaults(self):
        before = features.resolve({}).config
        card = next(item for item in features.describe() if item["name"] == "skill_retrieval")
        option = next(item for item in card["options"] if item["key"] == "excluded_skills")
        option["default"].append("console-only")
        self.assertEqual(features.resolve({}).config, before)

    def test_invalid_skill_containers_raise_configuration_errors(self):
        for name, key in (("skill_retrieval", "state_pins"),
                          ("skill_state_injection", "skills")):
            for value in (None, 42, "bad", {} if key == "state_pins" else []):
                with self.subTest(name=name, value=value), self.assertRaises(features.FeatureError):
                    features.resolve({name: {key: value}})

    def test_feature_authoring_and_callback_construction_need_no_runtime_packages(self):
        root = str(pathlib.Path(__file__).resolve().parent.parent)
        script = f"""
import sys
sys.path.insert(0, {root!r})
from adkl import capabilities, features
from adkl.adapters import build_callback_adapter
resolved = features.resolve({{"pipeline": ["history_slicing"]}})
assert capabilities.require_supported(resolved, "sdk")["canRun"]
assert features.describe()
assert build_callback_adapter({{"pipeline": ["history_slicing"]}})["before_model_callback"]
assert not any(name.startswith(("google", "litellm", "yaml")) for name in sys.modules)
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
