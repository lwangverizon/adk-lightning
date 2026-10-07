"""Ownership and placement seams for native skill activation.

Three `skill_retrieval` options decide who owns state the ADK runtime also
writes, and each arrived without a direct test of `SelectedSkillToolset.prepare`
-- the method that implements all of them. This file pins their observable
behaviour against a real Runner rather than deriving it from the branch.

Every value here was read off a live run before it was written down. Two of them
contradict the obvious reading of the source, which is most of why this file
exists:

* `native_activation: own` does NOT leave the shared ADK key untouched. `prepare`
  never writes it, but the `load_skill` call it makes for an auto-activation
  does, through ADK's own native path. The option says who owns the write in
  `prepare`; it does not promise the key stays still.
* `skill_hook_position: last` is not last in the finished callback list -- the
  SDK appends its own `observe_model` after it. Position is only meaningful
  relative to the HOST's callbacks.

This module needs ADK, so it belongs in tests/runtime/: the `components` CI lane
runs `uv sync --locked` with no `--extra runtime`, where an ADK import is a
collection error rather than a skip. See tests/test_import_layering.py.
"""
import unittest
from unittest import mock
import warnings

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.skill_toolset import ListSkillsTool
from google.genai import types

from adkl.bindings import RuntimeBindings
from adkl.retrieval import ACTIVATED_KEY
from adkl.skill_runtime import OWN_ACTIVATED_KEY
from test_bindings import Selector, ScriptModel, plain_skill, run

SHARED = ACTIVATED_KEY.format(agent="host")
OWN = OWN_ACTIVATED_KEY.format(agent="host")


def _binding(auto=True, **options):
    """A one-skill binding with `skill_retrieval` active and nothing else on."""
    config = {"auto_activate": auto, "min_skills_for_retrieval": 0}
    config.update(options)
    return RuntimeBindings(
        skills=[plain_skill("support")],
        feature_config={"pipeline": ["skill_retrieval"], "skill_retrieval": config},
        selector=Selector(auto=auto))


def _agent(binding, model=None):
    return LlmAgent(name="host", model=model or ScriptModel(), instruction="BASE",
                    **binding.agent_kwargs())


class CaptureModel(BaseLlm):
    """Records the system instruction and the tool names offered on each turn.

    `ScriptModel` keeps only the instruction, and the catalogue question needs
    both halves: whether the skills XML reached the prompt AND whether
    `list_skills` reached the model. Asserting either alone cannot tell a
    working mode apart from the failure where NEITHER route is open.
    """
    model: str = "capture"
    instructions: list = []
    tool_names: list = []

    async def generate_content_async(self, request, stream=False):
        self.instructions.append(str(request.config.system_instruction))
        for tool in (request.config.tools or []):
            for declaration in (getattr(tool, "function_declarations", None) or []):
                self.tool_names.append(declaration.name)
        yield LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text="Finished")]))


class TestNativeActivationOwnership(unittest.IsolatedAsyncioTestCase):
    """Who may write `_adk_activated_skill_<agent>`, and what survives.

    The key has more than one writer, so "activated" is a negotiated value
    rather than an owned one. Each mode picks a different settlement.
    """

    async def _state(self, mode, seed, auto=True):
        binding = _binding(auto=auto, native_activation=mode)
        _events, session = await run(_agent(binding), state=dict(seed))
        return session.state.get(SHARED), session.state.get(OWN)

    async def test_replace_discards_a_foreign_entry(self):
        # The divergence the other two modes exist to avoid: replace derives the
        # shared key from THIS turn's ranking, so whatever another writer left
        # there is gone. auto_activate is off, so nothing is added back and the
        # loss is the only thing on screen.
        shared, own = await self._state("replace", {SHARED: ["other"]}, auto=False)
        self.assertEqual(shared, [])
        self.assertIsNone(own, "replace owns the shared key, so it keeps no private copy")

    async def test_merge_keeps_a_foreign_entry_and_orders_its_own_first(self):
        shared, own = await self._state("merge", {SHARED: ["other"]})
        self.assertEqual(shared, ["support", "other"])
        self.assertEqual(own, ["support"])

    async def test_own_leaves_the_shared_key_exactly_as_it_found_it(self):
        # auto_activate off means no load_skill call, so `prepare` is the only
        # candidate writer. This is the assertion that actually pins "own never
        # writes the shared key" -- the auto-activated case below cannot.
        shared, own = await self._state("own", {SHARED: ["other"]}, auto=False)
        self.assertEqual(shared, ["other"])
        self.assertEqual(own, [])

    async def test_under_own_an_auto_activation_still_moves_the_shared_key(self):
        # And that is not a bug in `own`. `prepare` does not write the key; the
        # load_skill tool it invokes does, through ADK's native activation. A
        # test asserting the key stays ["other"] here would be asserting that
        # auto-activation does not work. The tell is ORDER: native appends, so
        # the foreign entry stays first, where merge puts its own first.
        shared, own = await self._state("own", {SHARED: ["other"]})
        self.assertEqual(shared, ["other", "support"])
        self.assertEqual(own, ["support"])
        merged, _own = await self._state("merge", {SHARED: ["other"]})
        self.assertEqual(merged, ["support", "other"])
        self.assertNotEqual(shared, merged,
                            "order is the only thing separating the two modes here")

    async def test_only_replace_keeps_no_private_copy(self):
        # The private key is what lets merge and own tell "we activated this"
        # from "someone else did". replace does not need it because it claims
        # the shared key outright.
        for mode, private in (("replace", False), ("merge", True), ("own", True)):
            with self.subTest(native_activation=mode):
                _shared, own = await self._state(mode, {})
                self.assertEqual(own is not None, private)

    async def test_the_first_turn_falls_back_to_the_shared_key(self):
        # No private copy exists yet, so `prepare` reads the shared key to learn
        # what is already active. Without the fallback, every first turn would
        # look like a cold start and re-activate a skill the host had running.
        shared, own = await self._state("merge", {SHARED: ["support"]}, auto=False)
        self.assertEqual(own, ["support"], "the shared key seeded the private copy")
        self.assertEqual(shared, ["support"])

    async def test_an_empty_private_copy_is_not_a_missing_one(self):
        # `own = None if mode == "replace" else ctx.state.get(own_key)` keys off
        # ABSENCE, and [] is not None -- so a host with genuinely nothing active
        # does not silently inherit the shared key's contents as its own.
        #
        # The shared key is ["support"] either way, so it cannot distinguish
        # these two cases; the private copy is the only observable. That is why
        # the previous test does not already cover this one.
        shared, own = await self._state("merge", {OWN: [], SHARED: ["support"]}, auto=False)
        self.assertEqual(own, [])
        self.assertEqual(shared, ["support"])


class TestSkillHookPosition(unittest.TestCase):
    """Where the selection's before_model hook sits among the host's own."""

    @staticmethod
    def _labels(binding, hooks):
        """Each callback as a name, marking the ones bound to the selection.

        Bound methods are not identity-stable: `binding._selection.before_model`
        mints a new object on every attribute access, so `assertIs` against it
        is always False and an identity assertion would fail on correct code.
        Comparing `__self__` to the toolset is the honest equivalent.
        """
        labels = []
        for fn in hooks:
            owned = getattr(fn, "__self__", None) is binding._selection
            labels.append(f"selection.{fn.__name__}" if owned else fn.__name__)
        return labels

    def _hooks(self, position):
        def host_before_model(callback_context, llm_request):
            return None
        binding = _binding(skill_hook_position=position)
        merged = binding.agent_kwargs(
            callbacks={"before_model_callback": [host_before_model]})
        return self._labels(binding, merged["before_model_callback"])

    def test_first_puts_the_selection_hook_ahead_of_the_host(self):
        self.assertEqual(self._hooks("first")[:2],
                         ["selection.before_model", "host_before_model"])

    def test_last_puts_the_selection_hook_after_the_host(self):
        labels = self._hooks("last")
        self.assertLess(labels.index("host_before_model"),
                        labels.index("selection.before_model"))

    def test_both_positions_place_it_relative_to_the_host_not_the_list_end(self):
        # The SDK appends its own observer after both, so the final entry is
        # observe_model in EITHER position. Written the obvious way --
        # `assertEqual(labels[-1], "selection.before_model")` for "last" -- this
        # fails against correct code, so the real invariant is pinned instead.
        for position in ("first", "last"):
            with self.subTest(skill_hook_position=position):
                self.assertEqual(self._hooks(position)[-1], "observe_model")


class TestSkillCatalogPlacement(unittest.IsolatedAsyncioTestCase):
    """skill_catalog decides whether the model READS the catalogue or asks for it."""

    def _core_binding(self, catalog):
        def lookup(query: str):
            """A host core tool, present only to select the presentation branch."""
            return {"status": "ok"}
        return RuntimeBindings(
            skills=[plain_skill("support")],
            feature_config={"pipeline": ["skill_retrieval"],
                            "skill_retrieval": {"auto_activate": True, "min_skills_for_retrieval": 0,
                                                "skill_catalog": catalog}},
            selector=Selector(), tools=[lookup], core_tools=["lookup"])

    def _both_branches(self, catalog):
        """A core tool in doc.tools is what selects the presentation branch."""
        return (("SkillToolset", _binding(skill_catalog=catalog)),
                ("PresentationSkillToolset", self._core_binding(catalog)))

    def test_each_branch_builds_the_toolset_type_it_claims(self):
        # Guards the fixture, not the SDK: if both rows built the same class the
        # coverage claim below would be false while every assertion still passed.
        for expected, binding in self._both_branches("tool"):
            with self.subTest(toolset=expected):
                built = binding._make_skill_toolset(["support"])
                self.assertEqual(type(built).__name__, expected)

    def test_prompt_mode_removes_the_tool_from_both_branches(self):
        # `_tools`, not `tool_filter`. Filtering is applied in get_tools, which
        # leaves the tool in _tools -- ADK then keeps the catalogue OUT of the
        # prompt because the tool is present, AND hides the tool from the model,
        # so neither route survives. PresentationSkillToolset overrides only
        # get_tools, which is why reaching into _tools covers both branches.
        for catalog, present in (("prompt", False), ("tool", True)):
            for name, binding in self._both_branches(catalog):
                with self.subTest(toolset=name, skill_catalog=catalog):
                    built = binding._make_skill_toolset(["support"])
                    self.assertEqual(
                        any(isinstance(t, ListSkillsTool) for t in built._tools), present)

    async def _routes(self, catalog):
        model = CaptureModel()
        await run(_agent(_binding(skill_catalog=catalog), model=model))
        return ("<available_skills>" in " ".join(model.instructions),
                "list_skills" in model.tool_names)

    async def test_the_two_modes_are_mutually_exclusive_routes(self):
        # The end-to-end half, and the one that would catch the tool_filter
        # mistake: exactly one route is open per mode. `tool` costs a round trip
        # (call list_skills, then choose), `prompt` costs instruction tokens.
        # Both open is a waste; both shut is a model that can find no skill at
        # all, which is what tool_filter would have produced.
        for catalog, expected in (("prompt", (True, False)), ("tool", (False, True))):
            with self.subTest(skill_catalog=catalog):
                self.assertEqual(await self._routes(catalog), expected)

    def test_native_eager_disclosure_is_used_without_the_deprecated_route(self):
        # ADK 2.11 deprecated removing list_skills to imply eager disclosure and
        # warns that a future release will stop injecting the catalogue for it.
        # Where the native mode exists the binding must ask for it directly.
        from google.adk.tools import skill_toolset
        if getattr(skill_toolset, "SkillDiscoveryMode", None) is None:
            self.skipTest("Installed ADK predates native discovery_mode")
        for name, binding in self._both_branches("prompt"):
            with self.subTest(toolset=name), warnings.catch_warnings():
                warnings.simplefilter("error", FutureWarning)
                built = binding._make_skill_toolset(["support"])
                self.assertIs(built._discovery_mode, skill_toolset.SkillDiscoveryMode.EAGER)
                self.assertTrue(built._inject_catalog(set()))

    def test_an_adk_without_the_private_tool_list_fails_loudly(self):
        # Legacy route only: an ADK with native discovery_mode never reaches it.
        from google.adk.tools import skill_toolset
        binding = _binding(skill_catalog="prompt")
        with mock.patch.object(skill_toolset, "SkillDiscoveryMode", None, create=True), \
                self.assertRaises(ValueError) as caught:
            binding._apply_skill_catalog(object())
        message = str(caught.exception)
        self.assertIn("_tools", message)
        self.assertIn("skill_catalog: tool", message)   # names the way out

    def test_tool_mode_leaves_a_missing_tool_list_alone(self):
        # The guard is scoped to prompt mode. Raising here too would break every
        # host on an ADK that stops exposing _tools, for a mode that never
        # needed it.
        binding = _binding(skill_catalog="tool")
        sentinel = object()
        self.assertIs(binding._apply_skill_catalog(sentinel), sentinel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
