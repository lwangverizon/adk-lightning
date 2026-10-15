# Answer Explorer (v1)

`adkl.answer_explorer` is the shared pattern for **text + widget** answers whose
user may want to go deeper: the reply carries the complete answer and one inline
card offers two or three grounded drill-down topics. Tapping a topic asks its
standalone follow-up question in the conversation. The module is stdlib-only and
opt-in; it installs no callbacks, emits nothing and calls no model.

It builds on [answer presentation](answer-presentation.md) (same evidence and
identity model) and resolves to the `supporting` role of
[presentation contracts](presentation-contracts.md). Procedures keep using
answer presentation; Answer Explorer is for explanatory answers.

## One pattern for every producer

1. **Propose in the existing generation.** Append `PROPOSAL_GUIDANCE` to the
   structured-output protocol that already writes the reply and add an optional
   `ExploreContent` field. No extra model call or staging tool.
2. **Stamp and supply host facts.** `ExplorerArtifact(owner, turn_id, content)`
   plus the host's admitted `EvidenceSource`s, the complete reply, the user's
   request and `ExplorerDelivery(card=..., taps=...)`.
3. **Select.** `select_explorer` returns an immutable `ExplorerSelection` with a
   bounded `reason`. Unusable topics are dropped with per-topic codes in
   `dropped`; fewer than two survivors reject the proposal. Only
   `reason == "selected"` carries a `card` and `Role.SUPPORTING`.
4. **Stage and convert.** Stage `card.payload(widget_type)` through the host's
   existing staged emitter. Its schema id rides under `pattern`, so a host that
   stamps a transport `version` into payloads cannot collide with it. Re-check with `lint_payload` / `card_from_payload` at
   emit time and convert with `to_a2ui(..., catalog_id, action_name,
   message_keys)` using the host's catalog and send-message action contract.
5. **Recognize taps.** Store `card.offered(owner=..., turn_id=...).to_dict()` in
   host state; on the next turn `match_tap(OfferedTopics.from_dict(...), text)`
   returns an `ExplorerTap` for telemetry or planning context. Recognition grants
   no authority.

The reply must remain complete. A card that cannot render, is declined or is
lost in history replay therefore removes only optional exploration.

## Card rules

`GUIDELINES` records each rule, its published basis and its enforcement point.

| Rule | Enforcement |
| --- | --- |
| Complementary: previews must not restate the reply; prompts must not repeat the request | `redundant_with_reply`, `repeats_request`, `missing_reply` |
| Grounded: every topic has an exact preview span of this turn's admitted evidence | `unsupported_preview`, `source_identity` |
| Single purpose: one explorer, only when no other primary presentation owns the answer | `primary_occupied` |
| Conversational drill-down: taps send a follow-up; no tabs, dialogs or nested navigation | `to_a2ui` |
| Action limit: topics are equal-weight (`default`) follow-ups, never primary calls to action | `to_a2ui` |
| Fits inline: 2–3 topics; labels ≤18 characters (one line of a full-width button at 320 px), previews ≤140, title ≤48 | `topic_bounds`, `<field>_copy_bounds` |
| Plain copy: no links, markup, Markdown controls, positional wording or model-authored prices in model copy | `<field>_unsafe_copy` |
| Accessible: each tap target exposes its label and the question it sends | `to_a2ui` |
| Verified delivery: both card rendering and tap delivery are host-verified | `unsupported_surface`, `taps_unverified` |

The published bases are the OpenAI Apps SDK UI guidelines for inline cards
(complement the model's response; single purpose; at most two actions; no deep
navigation or nested scrolling; accessible, system-styled content) and the
OpenAI MCP Apps `ui/message` extension, where a widget sends a user message into
the conversation. ADKL encodes them as checks; it does not claim certification
by any vendor or that a model follows the guidance.

Previews are verbatim evidence and may carry grounded figures; `to_a2ui`
escapes their Markdown controls. Model-authored labels, prompts and titles may not
carry prices. An unusable model title falls back to the host `default_title`.

## A2UI output

`to_a2ui` emits only v0.9 basic-catalog primitives:
`Card > Column[Text h4 title, Column[topic rows]]`, each row a
`Column[Button(default, Text label), Text caption preview]`. Buttons stay
direct `Column` children because clients mis-measure buttons inside a `Row`.
No `List` (clients differ on array-children templates), `Tabs`, `Modal`,
images or primary buttons. The title and labels are plain strings (clients draw
headings and button labels without Markdown); previews are Markdown-escaped. Each `Button` carries
`{"event": {"name": action_name, "context": {key: prompt for key in message_keys}}}`
and `accessibility.label/description`. Hosts must verify that their action
handler sends that context key as the user's next message and that their
catalog accepts `accessibility`; pass `accessibility=False` otherwise.

## Minimal integration sketch

```python
from adkl.answer_explorer import (
    ExplorerArtifact, ExplorerDelivery, ExploreContent, ExploreTopic,
    select_explorer, to_a2ui,
)
from adkl.answer_presentation import EvidenceSource, EvidenceText

source = EvidenceSource(
    "0", "Turn the lock on in Settings. Owners can change the PIN.", "help", "t1"
)
content = ExploreContent((
    ExploreTopic("Turn it on", "How do I turn the lock on?",
                 EvidenceText("0", "Turn the lock on in Settings.")),
    ExploreTopic("PIN changes", "Who can change the account PIN?",
                 EvidenceText("0", "Owners can change the PIN.")),
))
selection = select_explorer(
    ExplorerArtifact("help", "t1", content), (source,), owner="help", turn_id="t1",
    reply="The lock blocks unauthorized transfers.", request="What is the lock?",
    delivery=ExplorerDelivery(card=True, taps=True),
)
assert selection.reason == "selected"
operations = to_a2ui(selection.card, surface_id="answer-explorer",
                     catalog_id="https://example.test/catalog.json")
```

## Host integration guidance

Verify these considerations in the host's actual renderer. They are not public
host-evaluation results.

- **Ride the existing generation; request only where it can ship.** Add the
  optional field to the structured call that already writes the reply, and only
  when the host's delivery predicate says the card can render. Elsewhere the
  request stays byte-identical, and a rollback flag restores it.
- **Re-check at emit time and convert through the host registry.** Validate the
  staged payload with `lint_payload` in the host's register validator and call
  `to_a2ui` from the host's converter, so its transport, surface IDs and
  fallback stay in one place.
- **Hosts stamp a transport `version` into payloads.** The schema id rides
  under `pattern` for that reason.
- **Check label truncation.** Measure inner-element overflow on narrow viewports,
  not only the outer button box; client typography affects available space.
- **Button labels and headings are plain strings.** Markdown escaping belongs
  only in body/caption copy; verify this against the actual client.
- **Verify the action contract and accessible name on the real renderer.** Check
  that `context.text` becomes the intended next user message and that the client
  honors `accessibility.label`; SDK attributes alone do not establish accessibility.
- **Use the proposal guidance and test it.** `PROPOSAL_GUIDANCE` asks for three
  topics when supported so one dropped topic can still leave a valid card.
  Measure actual generation and rendering behavior in the host.
- **One follow-up surface per answer.** Avoid duplicate follow-up chips when
  an explorer is emitted.

## Limits and validation

Exact span membership and word-overlap redundancy checks are structural: they do
not establish entailment, relevance, completeness, translation quality or that a
follow-up is answerable. Paraphrased redundancy can pass. Hosts own evidence
admission, language, authorization, delivery verification, emitter and
history behavior, and must evaluate render rate, tap-through and answer quality
with real models and their actual frontend.

Run `uv run --no-sync python -m pytest tests/test_answer_explorer.py
tests/test_answer_presentation.py -q` (join onto one shell line). These are
structural tests, not live-model, browser or accessibility acceptance results.
