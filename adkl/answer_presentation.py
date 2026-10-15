"""Opt-in, extractive answer presentation without model, state or renderer access.

Hosts own evidence authority, completeness, authorization and delivery. Exact span
checks establish membership, not relevance, entailment or current business policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adkl.presentation_contracts import Role

VERSION = "answer-presentation-v1"


class AnswerShape(str, Enum):
    TEXT = "text"
    PROCEDURE = "procedure"


@dataclass(frozen=True)
class EvidenceText:
    """An extract selected from a host-supplied source, never an arbitrary URL."""

    source_id: str
    text: str


@dataclass(frozen=True)
class AnswerContent:
    """Model-proposed content; constructing this object does not validate evidence.

    V1 deliberately supports extractive text and procedures only. Conditions,
    charts, eligibility and actions need their own typed domain evidence first.
    Tuple fields also let native structured-output adapters derive a schema.
    """

    shape: AnswerShape
    answer: EvidenceText
    details: tuple[EvidenceText, ...] = ()
    disclosures: tuple[EvidenceText, ...] = ()
    uncertainties: tuple[EvidenceText, ...] = ()


@dataclass(frozen=True)
class EvidenceSource:
    """Trusted host projection of an authorized successful observation."""

    source_id: str
    text: str
    owner: str
    turn_id: str


@dataclass(frozen=True)
class AnswerArtifact:
    """Host-stamped task identity; model content cannot choose its authority."""

    owner: str
    turn_id: str
    content: AnswerContent
    version: str = VERSION


@dataclass(frozen=True)
class AnswerDelivery:
    """Host guarantees, not facts inferred from a widget name or staged marker.

    Recovery covers conversion failure, disabled UI and retained-history replay.
    Supported shapes alone never grant permission to shorten the written answer.
    """

    shapes: frozenset[AnswerShape] = frozenset()
    recovery_ready: bool = False


@dataclass(frozen=True)
class AnswerSelection:
    version: str
    shape: AnswerShape
    reason: str
    valid: bool
    fallback_text: str = ""
    companion_text: str = ""
    role: Role | None = None


def _valid_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _fragments(content: AnswerContent) -> tuple[EvidenceText, ...]:
    return (content.answer, *content.details, *content.disclosures, *content.uncertainties)


def select_answer(
    artifact: AnswerArtifact,
    sources: tuple[EvidenceSource, ...],
    *,
    owner: str,
    turn_id: str,
    delivery: AnswerDelivery,
    allowed_shapes: frozenset[AnswerShape],
    occupied: bool = False,
) -> AnswerSelection:
    """Validate an extractive proposal and select text or a proven visual shape.

    No mutation, side effects, callback installation or generated policy. Reject
    duplicate source IDs, cross-owner/turn evidence and ungrounded fragments. A
    rejected proposal contains no text: the host must preserve its original answer.
    Host-required disclosures must be supplied/checked by the host; this function
    cannot detect a qualification the producer never included.
    """
    def reject(reason: str) -> AnswerSelection:
        return AnswerSelection(VERSION, AnswerShape.TEXT, reason, False)

    if not isinstance(artifact, AnswerArtifact) or artifact.version != VERSION:
        return reject("invalid_artifact")
    if not _valid_text(owner, 200) or not _valid_text(turn_id, 200):
        return reject("missing_identity")
    if artifact.owner != owner or artifact.turn_id != turn_id:
        return reject("artifact_identity")
    content = artifact.content
    if not isinstance(content, AnswerContent) or not isinstance(content.shape, AnswerShape):
        return reject("invalid_content")
    for rows in (content.details, content.disclosures, content.uncertainties):
        if not isinstance(rows, tuple) or len(rows) > 8:
            return reject("invalid_rows")
    if content.shape is AnswerShape.PROCEDURE and not 2 <= len(content.details) <= 8:
        return reject("procedure_bounds")
    if not isinstance(sources, tuple) or not 1 <= len(sources) <= 64:
        return reject("invalid_sources")
    indexed: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, EvidenceSource):
            return reject("invalid_sources")
        if source.owner != owner or source.turn_id != turn_id:
            return reject("source_identity")
        if (
            not _valid_text(source.source_id, 100)
            or not _valid_text(source.text, 32000)
            or source.source_id in indexed
        ):
            return reject("invalid_sources")
        indexed[source.source_id] = source.text
    fragments = _fragments(content)
    for fragment in fragments:
        if (
            not isinstance(fragment, EvidenceText)
            or not _valid_text(fragment.source_id, 100)
            or not _valid_text(fragment.text, 4000)
            or fragment.source_id not in indexed
            or fragment.text not in indexed[fragment.source_id]
        ):
            return reject("unsupported_fragment")
    if len({row.text for row in content.details}) != len(content.details):
        return reject("duplicate_details")

    detail = "\n".join(
        f"{index}. {row.text}" if content.shape is AnswerShape.PROCEDURE else row.text
        for index, row in enumerate(content.details, 1)
    )
    caveats = tuple(row.text for row in (*content.disclosures, *content.uncertainties))
    fallback = "\n\n".join(part for part in (content.answer.text, detail, *caveats) if part)
    companion = "\n\n".join((content.answer.text, *caveats))
    reason = "selected"
    if content.shape is AnswerShape.TEXT:
        reason = "text_requested"
    elif content.shape not in allowed_shapes:
        reason = "owner_shape_disabled"
    elif occupied:
        reason = "primary_occupied"
    elif content.shape not in delivery.shapes:
        reason = "unsupported_surface"
    elif delivery.recovery_ready is not True:
        reason = "recovery_unverified"
    if reason != "selected":
        return AnswerSelection(VERSION, AnswerShape.TEXT, reason, True, fallback, fallback)
    return AnswerSelection(
        VERSION, content.shape, reason, True, fallback, companion, Role.ACTION_WITH_STEPS
    )
