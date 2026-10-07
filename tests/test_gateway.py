#!/usr/bin/env python3
"""Tests for the gateway integration and `adkl embed`.

No network. A fake litellm module stands in, so the wire format -- which is where
the production wrinkles live -- is asserted directly.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adkl import embed as embed_mod  # noqa: E402
from adkl.gateway import (  # noqa: E402
    PROVIDERS,
    GatewayEmbedder,
    GatewayError,
    GatewaySettings,
    _thinking_args,
    check_vectors,
    get_provider,
    read_env,
)
from adkl.retrieval import VectorFile  # noqa: E402


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


ENV = {
    "LITELLM_GATEWAY_API_BASE": "https://gw.example/v1",
    "LITELLM_GATEWAY_API_KEY": "k",
    "LITELLM_GATEWAY_MODEL3P5": "gemini-3.5-flash",
}


class FakeLiteLlm:
    """Records the call and returns unit vectors, out of order on purpose."""

    def __init__(self, dim: int = 768, shuffle: bool = False):
        self.dim = dim
        self.shuffle = shuffle
        self.calls: list[dict] = []

    async def aembedding(self, **kwargs):
        self.calls.append(kwargs)
        texts = kwargs["input"]
        items = [{"index": i, "embedding": [float(i)] + [0.0] * (self.dim - 1)}
                 for i in range(len(texts))]
        if self.shuffle:
            items = list(reversed(items))
        return {"data": items}


class TestSettings(unittest.TestCase):
    def test_reads_the_production_variable_names(self):
        s = GatewaySettings.from_env(ENV)
        self.assertEqual(s.api_base, "https://gw.example/v1")
        self.assertEqual(s.model, "gemini-3.5-flash")
        self.assertTrue(s.configured)

    def test_embedding_defaults_match_production(self):
        s = GatewaySettings.from_env({})
        # Region-pinned id, because production measured a tighter tail than the
        # bare id's multi-region routing.
        self.assertEqual(s.embedding_model,
                         "text-embedding-005_standard-us-east4")
        # 0 means "omit the parameter and take the model's native width".
        self.assertEqual(s.embedding_dimensions, 0)
        # Skill embedding through the gateway is the prod default, and is a
        # SEPARATE flag from completions so the two roll back independently.
        self.assertTrue(s.embedding_enabled)

    def test_missing_credentials_are_named(self):
        s = GatewaySettings.from_env({})
        self.assertFalse(s.configured)
        self.assertEqual(s.missing(),
                         ["LITELLM_GATEWAY_API_BASE", "LITELLM_GATEWAY_API_KEY"])

    def test_skill_embedding_gateway_can_be_turned_off(self):
        s = GatewaySettings.from_env({**ENV, "SKILL_EMBEDDING_GATEWAY": "false"})
        self.assertFalse(s.embedding_enabled)

    def test_project_env_reader_does_not_inherit_process_credentials(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with patch.dict(os.environ, {"GOOGLE_API_KEY": "ambient-secret"}):
                self.assertNotIn("GOOGLE_API_KEY", read_env(root))
                (root / ".env").write_text("GOOGLE_API_KEY=project-secret\n")
                self.assertEqual(read_env(root)["GOOGLE_API_KEY"], "project-secret")


    def test_requires_credentials(self):
        with self.assertRaises(GatewayError) as cm:
            GatewayEmbedder(GatewaySettings.from_env({}))
        self.assertIn("LITELLM_GATEWAY_API_BASE", str(cm.exception))

    def test_dimensions_on_a_non_matryoshka_model_is_rejected(self):
        # Production raises at construction for text-embedding-00x rather than
        # letting the call fail later.
        s = GatewaySettings.from_env(
            {**ENV, "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "768"})
        with self.assertRaises(GatewayError) as cm:
            GatewayEmbedder(s)
        self.assertIn("Matryoshka", str(cm.exception))

    def test_matryoshka_model_accepts_a_supported_width(self):
        s = GatewaySettings.from_env({
            **ENV,
            "SKILL_EMBEDDING_GATEWAY_MODEL": "gemini-embedding-001",
            "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "1536",
        })
        GatewayEmbedder(s)   # must not raise

    def test_matryoshka_model_rejects_an_unsupported_width(self):
        s = GatewaySettings.from_env({
            **ENV,
            "SKILL_EMBEDDING_GATEWAY_MODEL": "gemini-embedding-001",
            "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "1000",
        })
        with self.assertRaises(GatewayError):
            GatewayEmbedder(s)

    def test_region_suffix_does_not_hide_the_base_model(self):
        # text-embedding-005_standard-us-east4 must still be recognized as
        # text-embedding-005 for the Matryoshka check.
        s = GatewaySettings.from_env(
            {**ENV, "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "768"})
        with self.assertRaises(GatewayError) as cm:
            GatewayEmbedder(s)
        self.assertIn("text-embedding-005_standard-us-east4", str(cm.exception))


class TestWireFormat(unittest.TestCase):
    def test_task_type_is_forwarded(self):
        # Retrieval embeddings are asymmetric. Dropping task_type degrades
        # ranking with no error, so it must be on the request.
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV), litellm_module=fake)
        run(e.embed("hello"))
        self.assertEqual(fake.calls[0]["task_type"], "RETRIEVAL_QUERY")

    def test_document_task_type_when_asked(self):
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV),
                            task_type="RETRIEVAL_DOCUMENT", litellm_module=fake)
        run(e.embed("hello"))
        self.assertEqual(fake.calls[0]["task_type"], "RETRIEVAL_DOCUMENT")

    def test_model_carries_a_provider_prefix(self):
        # litellm refuses the call outright without one -- "LLM Provider NOT
        # provided" -- and all 28 tests here passed while this was broken,
        # because none asserted the model string. Only the live call caught it.
        # Production does the same (utils/gateway_embedder.py:110).
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV), litellm_module=fake)
        run(e.embed("x"))
        self.assertEqual(fake.calls[0]["model"],
                         "openai/text-embedding-005_standard-us-east4")

    def test_an_existing_prefix_is_left_alone(self):
        s = GatewaySettings.from_env(
            {**ENV,
             "SKILL_EMBEDDING_GATEWAY_MODEL": "vertex_ai/text-embedding-005"})
        fake = FakeLiteLlm()
        run(GatewayEmbedder(s, litellm_module=fake).embed("x"))
        self.assertEqual(fake.calls[0]["model"], "vertex_ai/text-embedding-005")

    def test_credentials_are_on_the_call(self):
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV), litellm_module=fake)
        run(e.embed("x"))
        self.assertEqual(fake.calls[0]["api_base"], "https://gw.example/v1")
        self.assertEqual(fake.calls[0]["api_key"], "k")

    def test_dimensions_travel_in_extra_body_not_as_a_kwarg(self):
        # litellm REJECTS a top-level `dimensions` client-side for several
        # models, so production sends it via extra_body.
        s = GatewaySettings.from_env({
            **ENV,
            "SKILL_EMBEDDING_GATEWAY_MODEL": "gemini-embedding-001",
            "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "768",
        })
        fake = FakeLiteLlm()
        run(GatewayEmbedder(s, litellm_module=fake).embed("x"))
        call = fake.calls[0]
        self.assertNotIn("dimensions", call)
        self.assertEqual(call["extra_body"], {"dimensions": 768})

    def test_no_extra_body_when_dimensions_is_zero(self):
        fake = FakeLiteLlm()
        run(GatewayEmbedder(GatewaySettings.from_env(ENV),
                            litellm_module=fake).embed("x"))
        self.assertNotIn("extra_body", fake.calls[0])

    def test_batch_results_are_reordered_by_index(self):
        # The API returns an index per item and does not guarantee input order.
        # Zipping blindly would silently attach the wrong vector to each skill.
        fake = FakeLiteLlm(shuffle=True)
        e = GatewayEmbedder(GatewaySettings.from_env(ENV), litellm_module=fake)
        out = run(e.embed_batch(["a", "b", "c"]))
        self.assertEqual([v[0] for v in out], [0.0, 1.0, 2.0])

    def test_a_short_response_is_an_error_not_a_silent_gap(self):
        class Short(FakeLiteLlm):
            async def aembedding(self, **kwargs):
                return {"data": [{"index": 0, "embedding": [1.0]}]}

        e = GatewayEmbedder(GatewaySettings.from_env(ENV),
                            litellm_module=Short())
        with self.assertRaises(GatewayError):
            run(e.embed_batch(["a", "b"]))


class TestVectorFileChecks(unittest.TestCase):
    def test_local_file_against_gateway_is_flagged(self):
        # The real trap: prod_skill_embeddings_local.json is a 384-dim ONNX
        # space, unrelated to the gateway's 768. Cosine across them returns
        # plausible numbers rather than failing.
        vf = VectorFile(vectors={"a": [0.0] * 384}, descriptions={},
                        backend="local")
        problems = check_vectors(vf, GatewaySettings.from_env(ENV))
        self.assertTrue(any("384" in p for p in problems))

    def test_matching_gateway_file_passes(self):
        vf = VectorFile(vectors={"a": [0.0] * 768}, descriptions={},
                        backend="gateway", task_type="RETRIEVAL_DOCUMENT")
        self.assertEqual(check_vectors(vf, GatewaySettings.from_env(ENV)), [])

    def test_query_task_type_in_a_document_file_is_flagged(self):
        vf = VectorFile(vectors={"a": [0.0] * 768}, descriptions={},
                        backend="gateway", task_type="RETRIEVAL_QUERY")
        problems = check_vectors(vf, GatewaySettings.from_env(ENV))
        self.assertTrue(any("RETRIEVAL_DOCUMENT" in p for p in problems))

    def test_dimension_mismatch_is_flagged(self):
        s = GatewaySettings.from_env({
            **ENV,
            "SKILL_EMBEDDING_GATEWAY_MODEL": "gemini-embedding-001",
            "SKILL_EMBEDDING_GATEWAY_DIMENSIONS": "1536",
        })
        vf = VectorFile(vectors={"a": [0.0] * 768}, descriptions={},
                        backend="gateway")
        self.assertTrue(any("1536" in p for p in check_vectors(vf, s)))


class TestEmbedCommand(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._d.name)
        for name, desc in [("alpha", "browse phones and tablets"),
                           ("beta", "answer factual questions")]:
            d = self.root / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: >\n  {desc}\n"
                f"metadata:\n  adk_additional_tools: []\n---\n\n## Workflow\n")

    def tearDown(self):
        self._d.cleanup()

    def test_reads_descriptions_in_include_order(self):
        d = embed_mod.descriptions(self.root / "skills", ["beta", "alpha"])
        self.assertEqual(list(d), ["beta", "alpha"])

    def test_a_skill_without_a_description_is_an_error(self):
        d = self.root / "skills" / "empty"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: empty\n---\n")
        with self.assertRaises(ValueError) as cm:
            embed_mod.descriptions(self.root / "skills", ["empty"])
        self.assertIn("unreachable", str(cm.exception))

    def test_build_produces_the_production_file_shape(self):
        descs = embed_mod.descriptions(self.root / "skills")
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV),
                            task_type="RETRIEVAL_DOCUMENT", litellm_module=fake)
        data = run(embed_mod.build(descs, e, "text-embedding-005", 0))
        self.assertEqual(set(data), {"embedding_metadata", "skills"})
        meta = data["embedding_metadata"]
        self.assertEqual(meta["backend"], "gateway")
        self.assertEqual(meta["task_type"], "RETRIEVAL_DOCUMENT")
        self.assertEqual(meta["dimensions"], 768)
        self.assertEqual(set(data["skills"]), {"alpha", "beta"})

    def test_check_detects_an_edited_description(self):
        # The whole point: an edited description means retrieval is matching the
        # OLD text, silently.
        descs = embed_mod.descriptions(self.root / "skills")
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV),
                            task_type="RETRIEVAL_DOCUMENT", litellm_module=fake)
        data = run(embed_mod.build(descs, e, "text-embedding-005", 0))
        out = self.root / "vectors.json"
        out.write_text(json.dumps(data))

        self.assertEqual(embed_mod.check(out, descs), [])

        descs["alpha"] = "browse phones, tablets AND WATCHES"
        problems = embed_mod.check(out, descs)
        self.assertTrue(any("OLD text" in p for p in problems))
        self.assertTrue(any("alpha" in p for p in problems))

    def test_check_detects_a_new_skill(self):
        descs = embed_mod.descriptions(self.root / "skills")
        fake = FakeLiteLlm()
        e = GatewayEmbedder(GatewaySettings.from_env(ENV),
                            task_type="RETRIEVAL_DOCUMENT", litellm_module=fake)
        out = self.root / "v.json"
        out.write_text(json.dumps(
            run(embed_mod.build(descs, e, "text-embedding-005", 0))))
        descs["gamma"] = "something new"
        problems = embed_mod.check(out, descs)
        self.assertTrue(any("cannot be retrieved at all" in p for p in problems))

    def test_check_reports_a_missing_file(self):
        problems = embed_mod.check(self.root / "nope.json", {"a": "b"})
        self.assertTrue(any("adkl embed" in p for p in problems))

    def test_fingerprint_changes_with_the_description(self):
        a = embed_mod.fingerprint("m", 0, "RETRIEVAL_DOCUMENT", {"s": "one"})
        b = embed_mod.fingerprint("m", 0, "RETRIEVAL_DOCUMENT", {"s": "two"})
        self.assertNotEqual(a, b)

    def test_fingerprint_changes_with_the_model(self):
        a = embed_mod.fingerprint("m1", 0, "RETRIEVAL_DOCUMENT", {"s": "one"})
        b = embed_mod.fingerprint("m2", 0, "RETRIEVAL_DOCUMENT", {"s": "one"})
        self.assertNotEqual(a, b)


# --------------------------------------------------------------------- gemini
#
# The clawtutor agent runs on Google's Gemini API rather than the ExampleCo
# gateway, and still through LiteLLM. These assert the parts that differ: the
# route prefix, the absent api_base, the key names, and the vector space.

GEMINI_ENV = {"GOOGLE_API_KEY": "gk"}


def _thinking_request(level=None, include_thoughts=None):
    """Minimal stand-in for an LlmRequest carrying a ThinkingConfig."""
    from types import SimpleNamespace
    tc = SimpleNamespace(
        thinking_level=SimpleNamespace(value=level) if level else None,
        include_thoughts=include_thoughts,
    )
    return SimpleNamespace(config=SimpleNamespace(thinking_config=tc))


class TestProviderRegistry(unittest.TestCase):
    def test_unknown_provider_names_the_alternatives(self):
        with self.assertRaises(GatewayError) as cm:
            get_provider("vertex")
        self.assertIn("gateway", str(cm.exception))
        self.assertIn("gemini", str(cm.exception))

    def test_default_is_the_gateway(self):
        # Every agent that predates `agent.provider` is a gateway agent, so the
        # default has to stay put.
        self.assertEqual(get_provider(None).name, "gateway")
        self.assertEqual(GatewaySettings.from_env({}).provider, "gateway")

    def test_the_schema_validates_against_this_same_registry(self):
        # A doc with an unknown provider is rejected, and the two lists cannot
        # drift because the validator reads PROVIDERS rather than a literal.
        from adkl import agent_schema

        def provider_errors(value):
            doc = {"version": 1, "agent": {"name": "a", "model": "m",
                                           "provider": value}}
            errors, _ = agent_schema.validate(doc, pathlib.Path("."))
            return [e for e in errors if e.startswith("agent.provider")]

        self.assertTrue(provider_errors("vertex"))
        for known in PROVIDERS:
            self.assertEqual(provider_errors(known), [])
        # Absent is fine: every agent predating the field is a gateway agent.
        errors, _ = agent_schema.validate(
            {"version": 1, "agent": {"name": "a", "model": "m"}},
            pathlib.Path("."))
        self.assertEqual([e for e in errors if e.startswith("agent.provider")],
                         [])

    def test_env_can_select_a_provider_but_an_argument_wins(self):
        s = GatewaySettings.from_env({**GEMINI_ENV, "ADKL_PROVIDER": "gemini"})
        self.assertEqual(s.provider, "gemini")
        # A project's own declaration is not something an ambient env var
        # should be able to flip.
        s = GatewaySettings.from_env({**ENV, "ADKL_PROVIDER": "gemini"},
                                     provider="gateway")
        self.assertEqual(s.provider, "gateway")


class TestGeminiSettings(unittest.TestCase):
    def test_accepts_clawtutors_existing_key_name(self):
        s = GatewaySettings.from_env(GEMINI_ENV, provider="gemini")
        self.assertEqual(s.api_key, "gk")
        self.assertTrue(s.configured)

    def test_key_names_are_tried_in_order(self):
        s = GatewaySettings.from_env(
            {"GOOGLE_AI_API_KEY": "c", "GOOGLE_API_KEY": "b",
             "GEMINI_API_KEY": "a"}, provider="gemini")
        self.assertEqual(s.api_key, "a")
        s = GatewaySettings.from_env({"GOOGLE_AI_API_KEY": "c"},
                                     provider="gemini")
        self.assertEqual(s.api_key, "c")

    def test_no_api_base_is_required(self):
        # The gateway needs a base URL; Google's endpoint is the route default,
        # so demanding one would only be a way to get it wrong.
        s = GatewaySettings.from_env(GEMINI_ENV, provider="gemini")
        self.assertEqual(s.api_base, "")
        self.assertTrue(s.configured)

    def test_missing_key_names_the_alternates(self):
        s = GatewaySettings.from_env({}, provider="gemini")
        self.assertFalse(s.configured)
        (msg,) = s.missing()
        self.assertIn("GEMINI_API_KEY", msg)
        self.assertIn("GOOGLE_API_KEY", msg)
        self.assertNotIn("LITELLM", msg)

    def test_defaults_match_what_clawtutor_uses(self):
        s = GatewaySettings.from_env(GEMINI_ENV, provider="gemini")
        self.assertEqual(s.model, "gemini-3.6-flash")
        self.assertEqual(s.embedding_model, "gemini-embedding-001")
        # 0 means "omit the parameter" -> the model's native 3072, which is the
        # width of clawtutor's committed index.
        self.assertEqual(s.embedding_dimensions, 0)


class TestGeminiWireFormat(unittest.TestCase):
    def _call(self, env=GEMINI_ENV, dim=3072):
        fake = FakeLiteLlm(dim=dim)
        e = GatewayEmbedder(GatewaySettings.from_env(env, provider="gemini"),
                            litellm_module=fake)
        run(e.embed("x"))
        return fake.calls[0]

    def test_model_carries_the_gemini_prefix(self):
        self.assertEqual(self._call()["model"], "gemini/gemini-embedding-001")

    def test_no_api_base_on_the_call(self):
        # Passing an empty api_base is not the same as passing none: litellm
        # would route to "" rather than to Google.
        self.assertNotIn("api_base", self._call())

    def test_key_and_task_type_are_forwarded(self):
        call = self._call()
        self.assertEqual(call["api_key"], "gk")
        self.assertEqual(call["task_type"], "RETRIEVAL_QUERY")

    def test_dimensions_are_a_top_level_kwarg_here(self):
        # The gateway needs extra_body because litellm rejects `dimensions`
        # client-side for text-embedding-00x. gemini-embedding-001 IS
        # Matryoshka, so the top-level param maps to output_dimensionality;
        # extra_body would bypass that mapping.
        call = self._call({**GEMINI_ENV, "SKILL_EMBEDDING_DIMENSIONS": "768"},
                          dim=768)
        self.assertEqual(call["dimensions"], 768)
        self.assertNotIn("extra_body", call)

    def test_no_dimensions_when_unset(self):
        call = self._call()
        self.assertNotIn("dimensions", call)
        self.assertNotIn("extra_body", call)

    def test_unsupported_width_is_rejected_at_construction(self):
        s = GatewaySettings.from_env(
            {**GEMINI_ENV, "SKILL_EMBEDDING_DIMENSIONS": "512"},
            provider="gemini")
        with self.assertRaises(GatewayError):
            GatewayEmbedder(s)

    def test_gateway_variable_names_do_not_leak_across(self):
        # SKILL_EMBEDDING_GATEWAY_MODEL belongs to the gateway. Honouring it on
        # the gemini route would silently embed into the wrong space.
        s = GatewaySettings.from_env(
            {**GEMINI_ENV,
             "SKILL_EMBEDDING_GATEWAY_MODEL": "text-embedding-005"},
            provider="gemini")
        self.assertEqual(s.embedding_model, "gemini-embedding-001")


class TestGeminiVectorSpace(unittest.TestCase):
    def test_a_gateway_file_against_gemini_is_flagged(self):
        # The whole point: same width, unrelated space, plausible-looking
        # cosine numbers. This must be an error, not a shrug.
        vf = VectorFile(vectors={"a": [0.0] * 768}, descriptions={},
                        backend="gateway", task_type="RETRIEVAL_DOCUMENT")
        s = GatewaySettings.from_env(GEMINI_ENV, provider="gemini")
        problems = check_vectors(vf, s)
        self.assertTrue(any("gateway" in p and "gemini" in p
                            for p in problems), problems)

    def test_a_gemini_file_passes(self):
        vf = VectorFile(vectors={"a": [0.0] * 3072}, descriptions={},
                        backend="gemini", task_type="RETRIEVAL_DOCUMENT")
        s = GatewaySettings.from_env(GEMINI_ENV, provider="gemini")
        self.assertEqual(check_vectors(vf, s), [])

    def test_embed_stamps_the_configured_backend(self):
        data = run(embed_mod.build({"a": "d"}, FakeLiteLlmEmbedder(3072),
                                   "gemini-embedding-001", 0,
                                   backend="gemini"))
        self.assertEqual(data["embedding_metadata"]["backend"], "gemini")
        self.assertEqual(data["embedding_metadata"]["dimensions"], 3072)


class FakeLiteLlmEmbedder:
    """Satisfies the embedder interface `embed_mod.build` calls."""

    def __init__(self, dim):
        self.dim = dim

    async def embed_batch(self, texts):
        return [[0.0] * self.dim for _ in texts]


class TestThinkingForwarding(unittest.TestCase):
    """ADK drops thinking_config on the LiteLLM path; both routes need it back,
    and they express it differently."""

    def test_gateway_uses_extra_body(self):
        args = _thinking_args(_thinking_request("LOW", include_thoughts=False),
                              PROVIDERS["gateway"])
        self.assertEqual(
            args, {"extra_body": {"thinking_config": {
                "include_thoughts": False, "thinking_level": "LOW"}}})

    def test_gemini_uses_a_camel_case_thinking_config(self):
        # litellm's Gemini transform merges this straight into
        # generationConfig; the gateway's snake_case extra_body would be
        # filtered out.
        args = _thinking_args(_thinking_request("LOW", include_thoughts=False),
                              PROVIDERS["gemini"], "gemini-3.6-flash")
        self.assertEqual(args, {"thinkingConfig": {
            "thinkingLevel": "low", "includeThoughts": False}})

    def test_include_thoughts_false_is_not_flipped_to_true(self):
        # The regression that made reasoning_effort unusable: every tier except
        # "disable" maps to includeThoughts=True, so an agent that asked NOT to
        # echo its reasoning would start streaming it to callers.
        args = _thinking_args(_thinking_request("HIGH", include_thoughts=False),
                              PROVIDERS["gemini"], "gemini-3.6-flash")
        self.assertIs(args["thinkingConfig"]["includeThoughts"], False)

    def test_none_becomes_the_lowest_level_the_model_has(self):
        # Gemini 3 cannot fully disable thinking. "minimal" exists on the flash
        # line only, which is litellm's own rule.
        flash = _thinking_args(_thinking_request("NONE"), PROVIDERS["gemini"],
                               "gemini-3.6-flash")
        self.assertEqual(flash["thinkingConfig"]["thinkingLevel"], "minimal")
        pro = _thinking_args(_thinking_request("NONE"), PROVIDERS["gemini"],
                             "gemini-3-pro")
        self.assertEqual(pro["thinkingConfig"]["thinkingLevel"], "low")

    def test_include_thoughts_alone_still_travels_on_gemini(self):
        args = _thinking_args(_thinking_request(None, include_thoughts=True),
                              PROVIDERS["gemini"], "gemini-3.6-flash")
        self.assertEqual(args, {"thinkingConfig": {"includeThoughts": True}})

    def test_no_thinking_config_forwards_nothing(self):
        from types import SimpleNamespace
        req = SimpleNamespace(config=SimpleNamespace(thinking_config=None))
        for name in PROVIDERS:
            self.assertEqual(_thinking_args(req, PROVIDERS[name], "m"), {})

    def test_a_broken_request_never_breaks_a_turn(self):
        self.assertEqual(_thinking_args(object(), PROVIDERS["gemini"], "m"), {})


class TestAgainstRealLiteLLM(unittest.TestCase):
    """The only assertions here that could catch a wrong kwarg NAME.

    Everything above uses a fake litellm, which will happily accept a kwarg the
    real library ignores -- and an ignored thinking kwarg is exactly the silent
    failure this module exists to prevent. These run litellm's own parameter
    mapping, offline and without credentials, and assert the wire shape.

    Skipped when litellm is absent so the suite still runs on a stdlib+PyYAML
    interpreter.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from litellm.utils import get_optional_params
        except ImportError:
            raise unittest.SkipTest("litellm not installed")
        cls.get_optional_params = staticmethod(get_optional_params)

    def _mapped(self, model="gemini-3.6-flash", **kw):
        return self.get_optional_params(
            model=model, custom_llm_provider="gemini", drop_params=True, **kw)

    def test_our_thinking_kwarg_reaches_generation_config(self):
        args = _thinking_args(_thinking_request("LOW", include_thoughts=False),
                              PROVIDERS["gemini"], "gemini-3.6-flash")
        mapped = self._mapped(**args)
        self.assertEqual(mapped["thinkingConfig"],
                         {"thinkingLevel": "low", "includeThoughts": False})

    def test_reasoning_effort_would_have_flipped_include_thoughts(self):
        # Records why the obvious approach was rejected. If a future litellm
        # stops forcing this, the mechanism can be revisited -- and this test
        # is what will say so.
        # Use a model in LiteLLM's public registry for the historical behavior.
        # The host's internal 3.6 alias may be unknown to that registry; recent
        # LiteLLM then drops reasoning_effort entirely under drop_params=True.
        mapped = self._mapped(model="gemini-3-flash-preview", reasoning_effort="low")
        self.assertIs(mapped["thinkingConfig"]["includeThoughts"], True)

    def test_explicit_thinking_config_handles_unregistered_host_alias(self):
        args = _thinking_args(_thinking_request("LOW", include_thoughts=False),
                              PROVIDERS["gemini"], "gemini-3.6-flash")
        mapped = self._mapped(model="gemini-3.6-flash", **args)
        self.assertEqual(mapped["thinkingConfig"],
                         {"thinkingLevel": "low", "includeThoughts": False})

    def test_the_gateway_shape_would_not_survive_this_route(self):
        # snake_case in extra_body is right for the gateway and inert here: it
        # is passed through untouched and never becomes a thinkingConfig.
        gateway_args = _thinking_args(_thinking_request("LOW"),
                                      PROVIDERS["gateway"])
        mapped = self._mapped(**gateway_args)
        self.assertNotIn("thinkingConfig", mapped)

    def test_embedding_params_are_the_names_gemini_actually_reads(self):
        from litellm.llms.vertex_ai.gemini_embeddings.batch_embed_content_transformation import (  # noqa: E501
            _filter_embed_params,
        )
        s = GatewaySettings.from_env(
            {**GEMINI_ENV, "SKILL_EMBEDDING_DIMENSIONS": "768"},
            provider="gemini")
        kw = GatewayEmbedder(s, task_type="RETRIEVAL_DOCUMENT")._kwargs()
        # Only the transport keys are ours to strip; the rest is what litellm
        # gets handed.
        params = {k: v for k, v in kw.items()
                  if k not in ("model", "api_key", "api_base", "input")}
        self.assertEqual(
            _filter_embed_params(params),
            {"taskType": "RETRIEVAL_DOCUMENT", "outputDimensionality": 768})

    def test_extra_body_would_be_dropped_on_the_embedding_route(self):
        # Why the gateway's extra_body trick is NOT reused here: litellm filters
        # embedding params to a fixed set, so it would vanish with no error.
        from litellm.llms.vertex_ai.gemini_embeddings.batch_embed_content_transformation import (  # noqa: E501
            _filter_embed_params,
        )
        self.assertEqual(
            _filter_embed_params({"extra_body": {"dimensions": 768}}), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
