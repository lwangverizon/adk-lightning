"""Extractive projection contracts; no model-quality or delivery claims."""
from dataclasses import FrozenInstanceError, replace

import pytest

from adkl.answer_presentation import (
    VERSION, AnswerArtifact, AnswerContent, AnswerDelivery, AnswerShape,
    EvidenceSource, EvidenceText, select_answer,
)
from adkl.presentation_contracts import Role

TEXT = "Prepare the fixture. Open Settings. Select Network. Keep the device connected."
SOURCE = EvidenceSource("s1", TEXT, "public_help", "turn1")
CONTENT = AnswerContent(
    AnswerShape.PROCEDURE,
    EvidenceText("s1", "Prepare the fixture."),
    (EvidenceText("s1", "Open Settings."), EvidenceText("s1", "Select Network.")),
    (EvidenceText("s1", "Keep the device connected."),),
)
ARTIFACT = AnswerArtifact("public_help", "turn1", CONTENT)
DELIVERY = AnswerDelivery(frozenset({AnswerShape.PROCEDURE}), True)


def choose(artifact=ARTIFACT, sources=(SOURCE,), **kwargs):
    options = dict(owner="public_help", turn_id="turn1", delivery=DELIVERY,
                   allowed_shapes=frozenset({AnswerShape.PROCEDURE}))
    return select_answer(artifact, sources, **(options | kwargs))


def test_projection_keeps_caveats_in_both_views_without_transcribing_steps():
    result = choose()
    assert result.valid and result.shape is AnswerShape.PROCEDURE
    assert result.role is Role.ACTION_WITH_STEPS
    assert result.version == VERSION
    assert "1. Open Settings.\n2. Select Network." in result.fallback_text
    assert "Open Settings" not in result.companion_text
    assert "Keep the device connected." in result.companion_text
    assert "Keep the device connected." in result.fallback_text
    with pytest.raises(FrozenInstanceError):
        result.reason = "changed"


@pytest.mark.parametrize("kwargs,reason", [
    ({"owner": "another"}, "artifact_identity"),
    ({"turn_id": "turn2"}, "artifact_identity"),
    ({"owner": ""}, "missing_identity"),
])
def test_identity_is_bound_by_host(kwargs, reason):
    result = choose(**kwargs)
    assert not result.valid and result.reason == reason
    assert not result.fallback_text


@pytest.mark.parametrize("source", [
    replace(SOURCE, owner="another"), replace(SOURCE, turn_id="turn0"),
    replace(SOURCE, text=""), replace(SOURCE, source_id=""),
])
def test_stale_failed_or_unidentified_sources_cannot_support_output(source):
    assert not choose(sources=(source,)).valid


@pytest.mark.parametrize("sources", [(), (SOURCE, SOURCE)])
def test_missing_and_ambiguous_sources_are_rejected(sources):
    assert not choose(sources=sources).valid


@pytest.mark.parametrize("fragment", [
    EvidenceText("s2", "Open Settings."), EvidenceText("s1", "Invented policy"),
    EvidenceText("s1", ""), EvidenceText("s1", " " * 8),
])
def test_every_fragment_requires_an_exact_source_span(fragment):
    for field in ("answer", "details", "disclosures", "uncertainties"):
        value = fragment if field == "answer" else (fragment, fragment)
        result = choose(replace(ARTIFACT, content=replace(CONTENT, **{field: value})))
        assert not result.valid and not result.fallback_text


@pytest.mark.parametrize("kwargs,reason", [
    ({"allowed_shapes": frozenset()}, "owner_shape_disabled"),
    ({"occupied": True}, "primary_occupied"),
    ({"delivery": AnswerDelivery()}, "unsupported_surface"),
    ({"delivery": AnswerDelivery(DELIVERY.shapes)}, "recovery_unverified"),
    ({"delivery": AnswerDelivery(DELIVERY.shapes, "true")}, "recovery_unverified"),
])
def test_delivery_is_not_inferred_and_text_fallback_is_complete(kwargs, reason):
    result = choose(**kwargs)
    assert result.valid and result.shape is AnswerShape.TEXT
    assert result.reason == reason and result.role is None
    assert result.companion_text == result.fallback_text == choose().fallback_text


def test_text_answers_never_get_forced_into_widgets():
    result = choose(replace(ARTIFACT, content=replace(CONTENT, shape=AnswerShape.TEXT)))
    assert result.shape is AnswerShape.TEXT and result.reason == "text_requested"


@pytest.mark.parametrize("details", [(), CONTENT.details[:1], CONTENT.details * 5,
                                         (CONTENT.details[0], CONTENT.details[0])])
def test_procedure_rows_are_bounded_and_never_truncated(details):
    assert not choose(replace(ARTIFACT, content=replace(CONTENT, details=details))).valid


def test_unknown_version_and_shape_fail_closed():
    assert not choose(replace(ARTIFACT, version="latest")).valid
    assert not choose(replace(ARTIFACT, content=replace(CONTENT, shape="chart"))).valid


def test_base_package_schema_can_be_used_by_optional_runtime_adapter():
    TypeAdapter = pytest.importorskip("pydantic").TypeAdapter

    adapter = TypeAdapter(AnswerContent)
    parsed = adapter.validate_json(adapter.dump_json(CONTENT))
    assert parsed == CONTENT
    assert "EvidenceText" in adapter.json_schema()["$defs"]
