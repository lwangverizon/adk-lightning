"""Answer Explorer: a complete written answer plus one card of grounded drill-downs.

Stdlib only. No model calls, state, callbacks, emitters, transport or tools. Every
producer follows the same five steps:

1. The model proposes ``ExploreContent`` in the generation that already writes the
   reply, using ``PROPOSAL_GUIDANCE``. The pattern adds no model call.
2. The host stamps ``ExplorerArtifact(owner, turn_id, content)`` and supplies its
   own admitted evidence, the complete reply, the user's request and delivery
   facts. Model output never chooses its evidence authority or capability.
3. ``select_explorer`` checks identity, exact preview spans, copy safety, bounds
   and redundancy, and returns an immutable ``ExplorerSelection`` with a bounded
   reason code. A selected card is ``Role.SUPPORTING``: the reply still carries
   the answer, so a card that fails to render costs no information.
4. The host stages ``card.payload(widget_type)`` through its existing emitter and
   converts it with ``to_a2ui``. A tap sends the topic's standalone prompt as the
   user's next message through the host's existing send-message action.
5. On the next turn ``match_tap`` recognizes an offered drill-down, for telemetry
   or planning context. It is recognition, not authorization.

``GUIDELINES`` maps each card rule to the published inline-card guidance it
follows and to the code that enforces it. Exact span membership is not
entailment, relevance, completeness or current policy verification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from adkl.answer_presentation import EvidenceSource, EvidenceText
from adkl.presentation_contracts import Role

VERSION = "answer-explorer-v1"

# Bounds keep the card inline and scannable. MAX_LABEL fits one line of a
# full-width themed button on a 320 px viewport (clients truncate button labels
# rather than wrap them); MAX_PREVIEW keeps each row to a short teaser.
MIN_TOPICS, MAX_TOPICS = 2, 3
MAX_TITLE = 48
MAX_LABEL = 18
MIN_PROMPT, MAX_PROMPT = 12, 120
MIN_PREVIEW, MAX_PREVIEW = 20, 140
#: Share of a preview's words found in one reply sentence at which the preview
#: restates the reply rather than adding to it.
REDUNDANT_OVERLAP = 0.8
#: Word-set similarity at which a prompt merely repeats the user's request.
REPEATED_REQUEST = 0.8


@dataclass(frozen=True)
class Guideline:
    """One card rule: what it requires, its published basis and its enforcement."""

    id: str
    rule: str
    basis: str
    enforced_by: str


GUIDELINES: tuple[Guideline, ...] = (
    Guideline(
        "complementary",
        "The reply carries the complete answer; the card adds drill-downs the reply "
        "does not already state and never repeats the user's request.",
        "OpenAI Apps SDK UI guidelines: widget content complements, not duplicates, "
        "the model's response.",
        "select_explorer: redundant_with_reply, repeats_request, missing_reply",
    ),
    Guideline(
        "grounded",
        "Each topic is anchored by an exact span of this turn's admitted evidence.",
        "Host evidence ownership (adkl.answer_presentation).",
        "select_explorer: unsupported_preview, source_identity",
    ),
    Guideline(
        "single_purpose",
        "One explorer per answer, only when no other primary presentation owns it.",
        "OpenAI Apps SDK inline cards: lightweight and single-purpose.",
        "select_explorer: primary_occupied",
    ),
    Guideline(
        "conversational_drilldown",
        "A tap asks a standalone follow-up in the conversation; the card has no "
        "tabs, dialogs or nested navigation.",
        "OpenAI Apps SDK inline cards: no deep navigation; MCP Apps ui/message.",
        "to_a2ui emits send-message buttons only",
    ),
    Guideline(
        "action_limit",
        "Drill-down taps are equal-weight follow-ups, not calls to action; the card "
        "emits no primary button.",
        "OpenAI Apps SDK inline cards: at most two actions, one primary.",
        "to_a2ui: default (non-primary) buttons",
    ),
    Guideline(
        "fits_without_scrolling",
        f"{MIN_TOPICS}-{MAX_TOPICS} topics with bounded copy so the card fits inline.",
        "OpenAI Apps SDK inline cards: no nested scrolling.",
        "select_explorer and lint_payload: topic_bounds, <field>_copy_bounds",
    ),
    Guideline(
        "plain_copy",
        "Labels and prompts are plain text without links, markup, positional "
        "wording or model-authored prices.",
        "OpenAI Apps SDK UI guidelines: clear, accessible, system-styled content.",
        "select_explorer and lint_payload: <field>_unsafe_copy",
    ),
    Guideline(
        "accessible",
        "Every tap target exposes its label and the question it sends.",
        "OpenAI Apps SDK UI guidelines: accessibility; A2UI accessibility attributes.",
        "to_a2ui: accessibility label and description",
    ),
    Guideline(
        "verified_delivery",
        "Render only where the host verified both the card and tap delivery.",
        "Host delivery ownership; a staged widget is not a rendered one.",
        "select_explorer: unsupported_surface, taps_unverified",
    ),
)

# Positive, default-on phrasing. In live host evaluation a model skipped the field
# when it was introduced as "optional" beside an instruction to write a complete
# reply; an explicit example label also cut over-length labels.
PROPOSAL_GUIDANCE = (
    "Explore offers the user's likely next questions as tappable topics. Your reply "
    "answers the question asked; when admitted evidence also covers related details "
    "your reply does not state, return explore with three topics when the evidence "
    "supports them, otherwise two, most useful first. Each topic has a label of two "
    "or three short words naming the subtopic, at most 18 characters (for example "
    '"Emergency address"); a prompt, the complete standalone question the user '
    "would ask next, naming the product or subject; and a preview, one short "
    "sentence of at most 140 characters copied exactly from an evidence text your "
    "reply does not state, with source_id set to that evidence's identifier. Title, "
    "optional, is up to five plain words naming the subject. Write labels, prompts "
    "and title as plain text without prices, links or markup. Set explore to null "
    "only when your reply already states all of the relevant evidence."
)


@dataclass(frozen=True)
class ExploreTopic:
    """One model-proposed drill-down; constructing it validates nothing."""

    label: str
    prompt: str
    preview: EvidenceText


@dataclass(frozen=True)
class ExploreContent:
    """Model-proposed card content. Tuples let runtime adapters derive a schema."""

    topics: tuple[ExploreTopic, ...]
    title: str = ""


@dataclass(frozen=True)
class ExplorerArtifact:
    """Host-stamped identity; model content cannot choose its authority."""

    owner: str
    turn_id: str
    content: ExploreContent
    version: str = VERSION


@dataclass(frozen=True)
class ExplorerDelivery:
    """Host guarantees, never inferred from a widget name or a staged marker.

    ``card`` asserts the surface renders the converted card. ``taps`` asserts the
    surface delivers a tap as the user's next message. A card whose taps cannot be
    delivered is decorative and is declined.
    """

    card: bool = False
    taps: bool = False


@dataclass(frozen=True)
class ExplorerTopicView:
    """A validated row: display copy and the exact message a tap sends."""

    label: str
    prompt: str
    preview: str
    source_id: str = ""


@dataclass(frozen=True)
class OfferedTopics:
    """What one turn offered, kept by the host to recognize the next turn's tap."""

    owner: str
    turn_id: str
    labels: tuple[str, ...]
    prompts: tuple[str, ...]
    version: str = VERSION

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for host session state; contains no evidence text."""
        return {"version": self.version, "owner": self.owner, "turn_id": self.turn_id,
                "labels": list(self.labels), "prompts": list(self.prompts)}

    @classmethod
    def from_dict(cls, value: Any) -> OfferedTopics | None:
        """Rebuild from host state; anything malformed or foreign is ``None``."""
        if not isinstance(value, dict) or value.get("version") != VERSION:
            return None
        labels, prompts = value.get("labels"), value.get("prompts")
        owner, turn_id = value.get("owner"), value.get("turn_id")
        if (not isinstance(labels, list) or not isinstance(prompts, list)
                or len(labels) != len(prompts)
                or not MIN_TOPICS <= len(prompts) <= MAX_TOPICS
                or not all(isinstance(item, str) and item for item in labels + prompts)
                or not _valid_text(owner, 200) or not _valid_text(turn_id, 200)):
            return None
        return cls(owner, turn_id, tuple(labels), tuple(prompts))


@dataclass(frozen=True)
class ExplorerCard:
    """A validated, host-renderable card. Contains no identity or evidence index."""

    title: str
    topics: tuple[ExplorerTopicView, ...]
    version: str = VERSION

    def payload(self, widget_type: str) -> dict[str, Any]:
        """The semantic widget payload a host stages; never transport frames.

        The schema rides under ``pattern``, not ``version``: hosts commonly stamp
        a transport version into widget payloads before conversion.
        """
        if not _valid_text(widget_type, 80):
            raise ValueError("widget_type must be nonempty text")
        return {
            "type": widget_type,
            "pattern": self.version,
            "title": self.title,
            "topics": [{"label": t.label, "prompt": t.prompt, "preview": t.preview}
                       for t in self.topics],
        }

    def offered(self, *, owner: str, turn_id: str) -> OfferedTopics:
        return OfferedTopics(owner, turn_id, tuple(t.label for t in self.topics),
                             tuple(t.prompt for t in self.topics))


@dataclass(frozen=True)
class ExplorerSelection:
    """Immutable result. ``valid`` means the content passed validation; only
    ``reason == "selected"`` carries a card. ``dropped`` lists per-topic codes."""

    version: str
    reason: str
    valid: bool
    card: ExplorerCard | None = None
    role: Role | None = None
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExplorerTap:
    """A recognized drill-down tap. Recognition only: it grants no authority."""

    index: int
    label: str
    owner: str
    turn_id: str
    version: str = VERSION


# --------------------------------------------------------------------- copy rules

_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MARKUP = re.compile(r"[<>]|&(?:lt|gt|#\d+);", re.IGNORECASE)
_LINK = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|gov|io|co|us)\b", re.IGNORECASE)
_MARKDOWN = re.compile(r"[*_`\[\]|#\\]")
_PRICE = re.compile(r"[$€£]\s?\d|\b\d+(?:\.\d+)?\s?(?:dollars?|usd)\b", re.IGNORECASE)
# Positional wording breaks when a client reflows the card; checked on model copy.
_POSITIONAL = re.compile(
    r"\b(?:above|below|click here|tap here|this card|the card)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)


def _valid_text(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _collapse(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _norm(text: str) -> str:
    """Case-, spacing- and punctuation-insensitive form; never display copy."""
    return " ".join(_TOKEN.findall(text.casefold())) if isinstance(text, str) else ""


def _words(text: str) -> frozenset[str]:
    """Content words (three or more characters) for overlap measures."""
    return frozenset(_WORD.findall(text.casefold()))


def _copy_problem(value: object, *, low: int, high: int, verbatim: bool = False
                  ) -> str | None:
    """Return a bounded code for unusable display copy, or ``None``.

    Model-authored copy (labels, prompts, titles) must be plain: no markup,
    links, Markdown controls, prices or positional wording. ``verbatim`` evidence
    keeps grounded figures and punctuation, which ``to_a2ui`` escapes, but still
    cannot carry markup or links into a card.
    """
    if not isinstance(value, str):
        return "invalid_content"
    text = _collapse(value)
    if not low <= len(text) <= high:
        return "copy_bounds"
    if _CONTROL.search(value.replace("\n", " ")) or _MARKUP.search(text) or _LINK.search(text):
        return "unsafe_copy"
    if not verbatim and (_MARKDOWN.search(text) or _PRICE.search(text)
                         or _POSITIONAL.search(text)):
        return "unsafe_copy"
    return None


def _restates(preview: str, reply: str) -> bool:
    """True when the reply already states the preview, verbatim or nearly so."""
    if f" {_norm(preview)} " in f" {_norm(reply)} ":
        return True
    words = _words(preview)
    if len(words) < 4:
        return False
    return any(len(words & _words(sentence)) / len(words) >= REDUNDANT_OVERLAP
               for sentence in _SENTENCE.split(reply) if sentence.strip())


def _repeats(prompt: str, request: str) -> bool:
    a, b = _words(prompt), _words(request)
    if not a or not b:
        return _norm(prompt) == _norm(request)
    return _norm(prompt) == _norm(request) or len(a & b) / len(a | b) >= REPEATED_REQUEST


def _title(value: object, default: str) -> str | None:
    """Model title when usable, else the host default; ``None`` if neither is."""
    if isinstance(value, str) and value.strip():
        if _copy_problem(value, low=1, high=MAX_TITLE) is None:
            return _collapse(value)
    if _copy_problem(default, low=1, high=MAX_TITLE) is None:
        return _collapse(default)
    return None


# --------------------------------------------------------------------- selection

def select_explorer(
    artifact: ExplorerArtifact,
    sources: tuple[EvidenceSource, ...],
    *,
    owner: str,
    turn_id: str,
    reply: str,
    request: str,
    delivery: ExplorerDelivery,
    occupied: bool = False,
    default_title: str = "Explore further",
) -> ExplorerSelection:
    """Validate a proposal against host evidence and select a card when deliverable.

    Pure: no mutation, side effects or generated policy. Topics are checked in the
    model's order; an unusable topic is dropped with a bounded code (copy codes
    name their field, e.g. ``label_copy_bounds``), extra valid
    topics beyond ``MAX_TOPICS`` are dropped as ``topic_bounds``, and fewer than
    ``MIN_TOPICS`` survivors reject the whole proposal. A rejected proposal never
    changes the host's reply, which must already be complete.
    """
    def reject(reason: str, dropped: tuple[str, ...] = ()) -> ExplorerSelection:
        return ExplorerSelection(VERSION, reason, False, dropped=dropped)

    if not isinstance(artifact, ExplorerArtifact) or artifact.version != VERSION:
        return reject("invalid_artifact")
    if not _valid_text(owner, 200) or not _valid_text(turn_id, 200):
        return reject("missing_identity")
    if artifact.owner != owner or artifact.turn_id != turn_id:
        return reject("artifact_identity")
    content = artifact.content
    if (not isinstance(content, ExploreContent) or not isinstance(content.topics, tuple)
            or not content.topics or len(content.topics) > 2 * MAX_TOPICS):
        return reject("invalid_content")
    if not _valid_text(reply, 32000):
        return reject("missing_reply")
    if not isinstance(request, str):
        return reject("invalid_content")
    if not isinstance(sources, tuple) or not 1 <= len(sources) <= 64:
        return reject("invalid_sources")
    indexed: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, EvidenceSource):
            return reject("invalid_sources")
        if source.owner != owner or source.turn_id != turn_id:
            return reject("source_identity")
        if (not _valid_text(source.source_id, 100) or not _valid_text(source.text, 32000)
                or source.source_id in indexed):
            return reject("invalid_sources")
        indexed[source.source_id] = _collapse(source.text)
    title = _title(content.title, default_title)
    if title is None:
        return reject("invalid_title")

    kept: list[ExplorerTopicView] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for topic in content.topics:
        reason = _topic_problem(topic, indexed, reply, request)
        if reason is None:
            keys = {f"l:{_norm(topic.label)}", f"p:{_norm(topic.prompt)}",
                    f"v:{_norm(topic.preview.text)}"}
            if keys & seen:
                reason = "duplicate_topic"
            elif len(kept) == MAX_TOPICS:
                reason = "topic_bounds"
            else:
                seen |= keys
                kept.append(ExplorerTopicView(
                    _collapse(topic.label), _collapse(topic.prompt),
                    _collapse(topic.preview.text), topic.preview.source_id))
        if reason is not None:
            dropped.append(reason)
    if len(kept) < MIN_TOPICS:
        return reject(dropped[0] if dropped else "topic_bounds", tuple(dropped))
    card = ExplorerCard(title, tuple(kept))
    reason = "selected"
    if occupied:
        reason = "primary_occupied"
    elif delivery is None or delivery.card is not True:
        reason = "unsupported_surface"
    elif delivery.taps is not True:
        reason = "taps_unverified"
    if reason != "selected":
        return ExplorerSelection(VERSION, reason, True, dropped=tuple(dropped))
    return ExplorerSelection(VERSION, reason, True, card, Role.SUPPORTING, tuple(dropped))


def _topic_problem(topic: object, indexed: dict[str, str], reply: str,
                   request: str) -> str | None:
    if not isinstance(topic, ExploreTopic) or not isinstance(topic.preview, EvidenceText):
        return "invalid_content"
    for field, value, low, high in (("label", topic.label, 1, MAX_LABEL),
                                    ("prompt", topic.prompt, MIN_PROMPT, MAX_PROMPT)):
        problem = _copy_problem(value, low=low, high=high)
        if problem:
            return f"{field}_{problem}"
    preview = topic.preview
    if (not _valid_text(preview.source_id, 100) or not isinstance(preview.text, str)
            or preview.source_id not in indexed
            or not _collapse(preview.text)
            or _collapse(preview.text) not in indexed[preview.source_id]):
        return "unsupported_preview"
    problem = _copy_problem(preview.text, low=MIN_PREVIEW, high=MAX_PREVIEW,
                            verbatim=True)
    if problem:
        return f"preview_{problem}"
    if _restates(preview.text, reply):
        return "redundant_with_reply"
    if _repeats(topic.prompt, request):
        return "repeats_request"
    return None


# --------------------------------------------------------------------- payloads

def lint_payload(payload: Any) -> tuple[str, ...]:
    """Structural guideline violations of a staged payload, with no evidence access.

    Hosts call this at emit time: register state is plain session data, so a card
    is re-checked before it reaches the wire. Empty means conformant.
    """
    if not isinstance(payload, dict):
        return ("invalid_payload",)
    problems: list[str] = []
    if payload.get("pattern") != VERSION:
        problems.append("pattern")
    if _copy_problem(payload.get("title"), low=1, high=MAX_TITLE):
        problems.append("title")
    topics = payload.get("topics")
    if not isinstance(topics, list) or not MIN_TOPICS <= len(topics) <= MAX_TOPICS:
        return (*problems, "topic_bounds")
    seen: set[str] = set()
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict):
            problems.append(f"topic{index}:invalid")
            continue
        for field, low, high, verbatim in (
            ("label", 1, MAX_LABEL, False),
            ("prompt", MIN_PROMPT, MAX_PROMPT, False),
            ("preview", MIN_PREVIEW, MAX_PREVIEW, True),
        ):
            problem = _copy_problem(topic.get(field), low=low, high=high,
                                    verbatim=verbatim)
            if problem:
                problems.append(f"topic{index}:{field}:{problem}")
        for field in ("label", "prompt"):
            key = f"{field}:{_norm(str(topic.get(field) or ''))}"
            if key in seen:
                problems.append(f"topic{index}:{field}:duplicate")
            seen.add(key)
    return tuple(problems)


def card_from_payload(payload: Any) -> ExplorerCard | None:
    """Rebuild a conformant card from a staged payload, or ``None``."""
    if lint_payload(payload):
        return None
    return ExplorerCard(
        _collapse(payload["title"]),
        tuple(ExplorerTopicView(_collapse(t["label"]), _collapse(t["prompt"]),
                                _collapse(t["preview"])) for t in payload["topics"]))


# --------------------------------------------------------------------- A2UI

_LEADING_MARKER = re.compile(r"^(\s*)(\d{1,9})([.)])(\s)")
_MARKDOWN_CHAR = re.compile(r"([\\`*_\[\]#|])")


def _literal(text: str) -> str:
    """Escape Markdown so verbatim body copy renders literally on Markdown clients.

    Only for body/caption copy. Headings and button labels are drawn as plain
    strings by A2UI clients, so escaping them would show stray backslashes.
    """
    return _LEADING_MARKER.sub(r"\1\2\\\3\4", _MARKDOWN_CHAR.sub(r"\\\1", text))


def to_a2ui(
    card: ExplorerCard | dict[str, Any],
    *,
    surface_id: str,
    catalog_id: str,
    action_name: str = "send_message",
    message_keys: tuple[str, ...] = ("text",),
    accessibility: bool = True,
) -> list[dict[str, Any]]:
    """A2UI v0.9 operations built from basic-catalog primitives only.

    ``Card > Column[Text h4 title, Column[topic...]]`` where each topic row is a
    ``Column[Button(default, Text label), Text caption preview]``. ``default`` is
    the equal-weight, non-primary variant: it keeps a visible tap affordance on
    unthemed clients, where ``borderless`` draws as plain text. Buttons are
    direct ``Column`` children (clients mis-measure buttons inside a ``Row``). No
    ``List`` (template semantics differ across clients), ``Tabs``, ``Modal``,
    images or primary buttons. Title and labels are plain strings; previews are
    Markdown-escaped. Hosts pass their catalog, action name and the context
    key(s) their action handler reads as the outgoing message. A payload that no
    longer satisfies ``lint_payload`` yields ``[]`` so the host keeps the text.
    """
    if isinstance(card, dict):
        card = card_from_payload(card)
    if not isinstance(card, ExplorerCard) or lint_payload(card.payload("check")):
        return []
    if not (_valid_text(surface_id, 200) and _valid_text(catalog_id, 500)
            and _valid_text(action_name, 100) and message_keys
            and all(_valid_text(key, 40) for key in message_keys)):
        raise ValueError("surface_id, catalog_id, action_name and message_keys are required")
    components: list[dict[str, Any]] = [
        {"id": "ae-title", "component": "Text", "text": card.title, "variant": "h4"}]
    rows: list[str] = []
    for index, topic in enumerate(card.topics):
        prefix = f"ae-topic{index}"
        button: dict[str, Any] = {
            "id": f"{prefix}-button", "component": "Button",
            "child": f"{prefix}-label", "variant": "default",
            "action": {"event": {"name": action_name,
                                 "context": {key: topic.prompt for key in message_keys}}},
        }
        if accessibility:
            button["accessibility"] = {"label": topic.label, "description": topic.prompt}
        components.extend([
            {"id": f"{prefix}-label", "component": "Text",
             "text": topic.label, "variant": "body"},
            button,
            {"id": f"{prefix}-preview", "component": "Text",
             "text": _literal(topic.preview), "variant": "caption"},
            {"id": prefix, "component": "Column",
             "children": [f"{prefix}-button", f"{prefix}-preview"]},
        ])
        rows.append(prefix)
    components.extend([
        {"id": "ae-topics", "component": "Column", "children": rows},
        {"id": "ae-body", "component": "Column", "children": ["ae-title", "ae-topics"]},
        {"id": "root", "component": "Card", "child": "ae-body"},
    ])
    return [
        {"version": "v0.9", "createSurface": {"surfaceId": surface_id,
                                              "catalogId": catalog_id}},
        {"version": "v0.9", "updateComponents": {"surfaceId": surface_id,
                                                 "components": components}},
    ]


# --------------------------------------------------------------------- taps

def match_tap(offered: OfferedTopics | None, text: Any) -> ExplorerTap | None:
    """Recognize the user's message as one offered drill-down, by its exact prompt.

    Matching is case-, spacing- and punctuation-insensitive. A typed message that
    happens to equal an offered prompt is the same question and matches too.
    """
    if not isinstance(offered, OfferedTopics) or not isinstance(text, str):
        return None
    key = _norm(text)
    if not key:
        return None
    for index, prompt in enumerate(offered.prompts):
        if _norm(prompt) == key:
            return ExplorerTap(index, offered.labels[index], offered.owner,
                               offered.turn_id)
    return None
