"""Answer Explorer contracts: grounding, guideline rules, A2UI shape and taps.

No model, state, frontend or delivery claims: these are structural guarantees.
"""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from adkl import answer_explorer as ae
from adkl.a2ui_protocol import BASIC_COMPONENTS, reduce_operations
from adkl.answer_explorer import (
    GUIDELINES, MAX_TOPICS, PROPOSAL_GUIDANCE, VERSION, ExploreContent, ExploreTopic,
    ExplorerArtifact, ExplorerDelivery, OfferedTopics, card_from_payload, lint_payload,
    match_tap, select_explorer, to_a2ui,
)
from adkl.answer_presentation import EvidenceSource, EvidenceText
from adkl.presentation_contracts import Role

EVIDENCE = (
    "Number Lock prevents anyone from porting your number to another carrier. "
    "You can turn Number Lock on or off in the ExampleCo app under Account security. "
    "SIM Protection blocks SIM card changes and eSIM transfers on your line. "
    "Account PIN changes require the account owner to verify their identity. "
    "Protection features are included at no extra cost, $0/mo, on postpaid lines."
)
SOURCE = EvidenceSource("0", EVIDENCE, "public_help", "turn1")
REPLY = (
    "Number Lock prevents anyone from porting your number to another carrier. "
    "It stays on until you turn it off."
)
REQUEST = "What is Number Lock?"


def topic(label, prompt, preview, source="0"):
    return ExploreTopic(label, prompt, EvidenceText(source, preview))


TOGGLE = topic("Turn it on or off", "How do I turn Number Lock on or off?",
               "You can turn Number Lock on or off in the ExampleCo app under "
               "Account security.")
SIM = topic("SIM Protection", "What does ExampleCo SIM Protection block?",
            "SIM Protection blocks SIM card changes and eSIM transfers on your line.")
PIN = topic("PIN changes", "Who can change the account PIN on an ExampleCo account?",
            "Account PIN changes require the account owner to verify their identity.")
COST = topic("Cost", "Do ExampleCo line protection features cost extra?",
             "Protection features are included at no extra cost, $0/mo, on postpaid lines.")
CONTENT = ExploreContent((TOGGLE, SIM), title="More on Number Lock")
ARTIFACT = ExplorerArtifact("public_help", "turn1", CONTENT)
DELIVERY = ExplorerDelivery(card=True, taps=True)


def choose(artifact=ARTIFACT, sources=(SOURCE,), **kwargs):
    options = dict(owner="public_help", turn_id="turn1", reply=REPLY, request=REQUEST,
                   delivery=DELIVERY)
    return select_explorer(artifact, sources, **(options | kwargs))


def with_topics(*topics, title="More on Number Lock"):
    return replace(ARTIFACT, content=ExploreContent(tuple(topics), title))


def test_selected_card_supports_a_complete_reply_without_evidence_identity():
    result = choose()
    assert result.valid and result.reason == "selected" and result.version == VERSION
    assert result.role is Role.SUPPORTING and result.dropped == ()
    payload = result.card.payload("Answer_Explorer")
    assert payload == {
        "type": "Answer_Explorer", "pattern": VERSION, "title": "More on Number Lock",
        "topics": [
            {"label": t.label, "prompt": t.prompt, "preview": t.preview.text}
            for t in (TOGGLE, SIM)
        ],
    }
    assert "turn1" not in json.dumps(payload) and "public_help" not in json.dumps(payload)
    assert lint_payload(payload) == ()
    # A host transport version stamped into the payload never collides.
    assert lint_payload({**payload, "version": "0.9"}) == ()
    assert lint_payload({**payload, "pattern": "answer-explorer-v0"}) == ("pattern",)
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"


@pytest.mark.parametrize("kwargs,reason", [
    ({"owner": "another"}, "artifact_identity"),
    ({"turn_id": "turn2"}, "artifact_identity"),
    ({"owner": ""}, "missing_identity"),
    ({"reply": " "}, "missing_reply"),
    ({"sources": ()}, "invalid_sources"),
    ({"sources": (SOURCE, SOURCE)}, "invalid_sources"),
    ({"sources": (replace(SOURCE, owner="another"),)}, "source_identity"),
    ({"sources": (replace(SOURCE, turn_id="turn0"),)}, "source_identity"),
])
def test_identity_and_evidence_are_bound_by_the_host(kwargs, reason):
    result = choose(**kwargs)
    assert not result.valid and result.reason == reason and result.card is None


def test_unknown_version_and_content_fail_closed():
    assert choose(replace(ARTIFACT, version="latest")).reason == "invalid_artifact"
    assert choose(replace(ARTIFACT, content="cards")).reason == "invalid_content"
    assert choose(with_topics()).reason == "invalid_content"


@pytest.mark.parametrize("bad,reason", [
    (topic("Paraphrase", "How do I turn Number Lock off?", "Use the app to switch it."),
     "unsupported_preview"),
    (topic("Other source", "How do I turn Number Lock off?", TOGGLE.preview.text, "9"),
     "unsupported_preview"),
    (topic("Restated", "Does Number Lock block porting my number?",
           "Number Lock prevents anyone from porting your number to another carrier."),
     "redundant_with_reply"),
    (topic("Number Lock", "What's Number Lock?", PIN.preview.text), "repeats_request"),
    (topic("Visit site", "Where is example.com/number-lock explained?", PIN.preview.text),
     "prompt_unsafe_copy"),
    (topic("<b>PIN</b>", "Who can change the account PIN?", PIN.preview.text),
     "label_unsafe_copy"),
    (topic("Price", "Is Number Lock still $5/mo for me?", PIN.preview.text),
     "prompt_unsafe_copy"),
    (topic("**PIN**", "Who can change the account PIN?", PIN.preview.text),
     "label_unsafe_copy"),
    (topic("See below", "Who can change the account PIN?", PIN.preview.text),
     "label_unsafe_copy"),
    (topic("Linked", "Where do I read more about PINs?",
           "Account PIN changes require https://example.test owner checks."),
     "unsupported_preview"),
    (topic("x" * 19, "Who can change the account PIN?", PIN.preview.text),
     "label_copy_bounds"),
    (topic("PIN", "Why?", PIN.preview.text), "prompt_copy_bounds"),
    (topic("Duplicate", TOGGLE.prompt, PIN.preview.text), "duplicate_topic"),
])
def test_unusable_topics_are_dropped_with_bounded_codes(bad, reason):
    result = choose(with_topics(TOGGLE, bad, SIM))
    assert result.reason == "selected" and result.dropped == (reason,)
    assert [t.label for t in result.card.topics] == [TOGGLE.label, SIM.label]
    alone = choose(with_topics(TOGGLE, bad))
    assert not alone.valid and alone.reason == reason and alone.card is None


def test_near_verbatim_restatement_counts_as_redundant():
    near = topic("Porting", "Can someone port my ExampleCo number away?",
                 "Number Lock prevents anyone from porting your number to another carrier.")
    reply = "In short, Number Lock prevents anyone porting your number to another carrier."
    assert choose(with_topics(TOGGLE, near, SIM), reply=reply).dropped == (
        "redundant_with_reply",)
    paraphrase = "It stops number transfers that you did not approve."
    assert choose(with_topics(TOGGLE, near, SIM), reply=paraphrase).dropped == ()


def test_grounded_figures_survive_in_verbatim_previews_only():
    result = choose(with_topics(TOGGLE, COST))
    assert result.reason == "selected"
    assert "$0/mo" in result.card.topics[1].preview


def test_topic_count_is_bounded_in_model_order():
    duplicate = topic("Eligibility", "Which ExampleCo lines can use SIM Protection?",
                      COST.preview.text)
    result = choose(with_topics(TOGGLE, COST, duplicate, SIM))
    assert len(result.card.topics) == MAX_TOPICS == 3
    assert [t.label for t in result.card.topics] == [TOGGLE.label, COST.label, SIM.label]
    assert result.dropped == ("duplicate_topic",)
    assert choose(with_topics(TOGGLE, SIM, PIN, COST)).dropped == ("topic_bounds",)
    assert choose(with_topics(*(TOGGLE,) * 7)).reason == "invalid_content"


@pytest.mark.parametrize("kwargs,reason", [
    ({"occupied": True}, "primary_occupied"),
    ({"delivery": ExplorerDelivery()}, "unsupported_surface"),
    ({"delivery": ExplorerDelivery(card=True)}, "taps_unverified"),
    ({"delivery": ExplorerDelivery(card=True, taps="yes")}, "taps_unverified"),
])
def test_delivery_is_never_inferred(kwargs, reason):
    result = choose(**kwargs)
    assert result.valid and result.reason == reason
    assert result.card is None and result.role is None


def test_title_falls_back_to_the_host_default_and_never_to_unsafe_copy():
    assert choose(with_topics(TOGGLE, SIM, title="")).card.title == "Explore further"
    unsafe = with_topics(TOGGLE, SIM, title="<i>Locks</i>")
    assert choose(unsafe, default_title="More to explore").card.title == "More to explore"
    assert choose(unsafe, default_title="").reason == "invalid_title"


def test_a2ui_uses_basic_primitives_and_send_message_taps_only():
    payload = choose().card.payload("Answer_Explorer")
    ops = to_a2ui(payload, surface_id="answer-explorer", catalog_id="catalog",
                  action_name="example:send", message_keys=("text", "userMessage"))
    surface = reduce_operations(ops)[0]
    assert surface["root"] == "root" and surface["warnings"] == []
    components = {c["id"]: c for c in surface["components"]}
    kinds = {c["component"] for c in components.values()}
    assert kinds <= BASIC_COMPONENTS and not kinds & {"List", "Tabs", "Modal", "Image"}
    assert components["root"]["component"] == "Card"
    assert components["ae-body"]["children"] == ["ae-title", "ae-topics"]
    assert components["ae-topics"]["children"] == ["ae-topic0", "ae-topic1"]
    for row in components["ae-topics"]["children"]:
        button, preview = (components[c] for c in components[row]["children"])
        assert button["component"] == "Button" and preview["variant"] == "caption"
    buttons = [c for c in components.values() if c["component"] == "Button"]
    assert [b["action"]["event"] for b in buttons] == [
        {"name": "example:send", "context": {"text": t.prompt, "userMessage": t.prompt}}
        for t in (TOGGLE, SIM)
    ]
    assert {b["variant"] for b in buttons} == {"default"}
    assert buttons[0]["accessibility"] == {"label": TOGGLE.label,
                                           "description": TOGGLE.prompt}
    assert components[buttons[0]["child"]]["text"] == TOGGLE.label


def test_a2ui_escapes_verbatim_markdown_and_refuses_nonconformant_payloads():
    card = choose().card
    marked = replace(card, topics=(replace(card.topics[0], preview="1. Open *Settings* now."),
                                   card.topics[1]))
    labelled = replace(marked, topics=(replace(marked.topics[0], label="1. Setup"),
                                       marked.topics[1]))
    components = {c["id"]: c for c in
                  to_a2ui(labelled, surface_id="s", catalog_id="c")[1]
                  ["updateComponents"]["components"]}
    assert components["ae-topic0-preview"]["text"] == "1\\. Open \\*Settings\\* now."
    # Button labels and headings are plain strings on clients; never escaped.
    assert components["ae-topic0-label"]["text"] == "1. Setup"
    payload = card.payload("Answer_Explorer")
    tampered = {**payload, "topics": payload["topics"] * 2}
    assert lint_payload(tampered) == ("topic_bounds",)
    assert to_a2ui(tampered, surface_id="s", catalog_id="c") == []
    linked = json.loads(json.dumps(payload))
    linked["topics"][0]["label"] = "Visit https://a.io"
    assert lint_payload(linked) == ("topic0:label:unsafe_copy",)
    assert card_from_payload(linked) is None
    assert card_from_payload(payload) == replace(card, topics=tuple(
        replace(t, source_id="") for t in card.topics))
    with pytest.raises(ValueError):
        to_a2ui(card, surface_id="", catalog_id="c")


def test_offered_topics_round_trip_and_recognize_only_their_prompts():
    offered = choose().card.offered(owner="public_help", turn_id="turn1")
    restored = OfferedTopics.from_dict(json.loads(json.dumps(offered.to_dict())))
    assert restored == offered
    tap = match_tap(restored, "  how do I turn NUMBER LOCK on or off ")
    assert (tap.index, tap.label, tap.turn_id) == (0, TOGGLE.label, "turn1")
    assert match_tap(restored, "How do I turn Number Lock on?") is None
    assert match_tap(None, TOGGLE.prompt) is None
    for value in ({}, {**offered.to_dict(), "version": "x"},
                  {**offered.to_dict(), "prompts": ["only one"]},
                  {**offered.to_dict(), "owner": ""}):
        assert OfferedTopics.from_dict(value) is None


def test_guidelines_name_their_enforcement_and_guidance_stays_domain_neutral():
    ids = [g.id for g in GUIDELINES]
    assert len(ids) == len(set(ids)) and all(g.rule and g.basis and g.enforced_by
                                             for g in GUIDELINES)
    source = open(ae.__file__, encoding="utf-8").read()
    for code in ("redundant_with_reply", "repeats_request", "unsupported_preview",
                 "primary_occupied", "taps_unverified", "unsafe_copy", "copy_bounds",
                 "topic_bounds"):
        assert any(code in g.enforced_by for g in GUIDELINES)
        assert f'"{code}"' in source
    lowered = PROPOSAL_GUIDANCE.lower()
    assert "exampleco" not in lowered and "customer" not in lowered


def test_base_package_schema_can_be_used_by_optional_runtime_adapter():
    TypeAdapter = pytest.importorskip("pydantic").TypeAdapter

    adapter = TypeAdapter(ExploreContent)
    parsed = adapter.validate_json(adapter.dump_json(CONTENT))
    assert parsed == CONTENT
    assert {"ExploreTopic", "EvidenceText"} <= set(adapter.json_schema()["$defs"])
