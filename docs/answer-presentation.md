# Evidence-backed answer presentation (v1)

`adkl.answer_presentation` is an opt-in Python-only, stdlib mechanism. It does
not install callbacks, emit widgets, invoke models or change existing
`visual-v1`/`visual-v2` presentation contracts. No YAML/Console/export switch is
introduced. Explanatory answers with tap-to-ask drill-downs use the companion
[Answer Explorer](answer-explorer.md) pattern on the same evidence model. V1 supports **extractive text and ordered procedures**, not arbitrary
UI generation, charts, eligibility, actions or semantic entailment checking.

## Ownership

- The host supplies successful, authorized, current-turn `EvidenceSource`s,
  including owner and turn identity. Do not use titles, URLs or tool policy as
  factual content. The host determines relevance, completeness and applicability.
- The model may propose `AnswerContent` in an existing structured completion.
  It selects `EvidenceText(source_id, text)` fragments. No extra planner call is
  required by this API. An optional malformed proposal should not destroy the
  host's otherwise valid answer.
- The host stamps `AnswerArtifact(owner, turn_id, content)`. Model output does
  not get to choose its evidence authority or delivery capability.
- `select_answer` checks identity, unique source IDs, exact fragment membership,
  bounded rows and supported shape. It returns immutable `AnswerSelection` with
  a full text fallback and a complementary-text projection.
- The host supplies `AnswerDelivery` and owner `allowed_shapes`. A supported
  widget name is insufficient: `recovery_ready=True` asserts the host has tested
  conversion failure, feature-flag refusal and history replay recovery. It is
  not an SDK delivery guarantee. An occupied primary answer declines a visual.
- The host compiles selected content into its semantic payload and uses its
  existing staged emitter, converter, authorization and action handling.

A rejected proposal returns no candidate text: preserve the original grounded
answer. A valid but unsupported visual returns the complete candidate text, with
no visual role. A selected procedure returns `Role.ACTION_WITH_STEPS`, compatible
with [shared presentation contracts](presentation-contracts.md). Disclosures and
uncertainties remain in both projections; detailed steps stay in the full view.
No domain fact is interpolated into SDK instructions.

## Minimal integration sketch

```python
from adkl.answer_presentation import (
    AnswerArtifact, AnswerContent, AnswerDelivery, AnswerShape,
    EvidenceSource, EvidenceText, select_answer,
)

source = EvidenceSource(
    "s1", "Prepare the fixture. Open Settings. Select Network.", "help", "turn1"
)
content = AnswerContent(
    AnswerShape.PROCEDURE,
    EvidenceText("s1", "Prepare the fixture."),
    (EvidenceText("s1", "Open Settings."), EvidenceText("s1", "Select Network.")),
)
selection = select_answer(
    AnswerArtifact("help", "turn1", content), (source,),
    owner="help", turn_id="turn1",
    delivery=AnswerDelivery(),  # no proven recovery -> complete text
    allowed_shapes=frozenset({AnswerShape.PROCEDURE}),
)
assert selection.valid and selection.shape is AnswerShape.TEXT
```

Optional runtime adapters can derive structured output schemas from the standard
library dataclasses (for example with Pydantic). ADKL does not require Pydantic
for this module. Host adapters retain schema parsing/error recovery and model
boundary attribution. Treat every proposed string as untrusted data.

## Host integration guidance

These are integration considerations, not published host-evaluation results.

- **Carry the extract in the generation that already writes the answer.** Avoid
  a separate presentation-staging model round trip when the host supports it.
- **Give the model one procedure, not two.** Remove competing staging
  instructions rather than appending a contradictory override.
- **Use the host's own evidence admission.** Build `EvidenceSource`s from the
  admitted evidence the composer sees; test the actual result-trimming path.
- **Keep rollout controls explicit.** If using an experiment, verify that the
  disabled path changes no schema, instruction, payload or limit.
- **Recover the answer, not the proposal.** Preserve the host's valid answer
  when an optional extract is malformed; retry policy remains host-owned.
- **Decide text completeness explicitly.** A complete reply survives card
  failure and history replay but can duplicate the card. Complementary text
  reduces duplication but makes the card load-bearing and requires tested recovery.

## Limits and validation

Exact quote membership is **not** entailment, completeness, relevance, factual
accuracy, safe markup or current policy verification. A malicious or irrelevant
source can contain exact matching text. Required disclosures omitted by the
producer cannot be detected without a host-owned requirement set. Hosts must
preserve their grounding/safety rules and evaluate omission and prompt-injection
cases. V1 fails unsupported shapes explicitly rather than overloading booleans
for unknown eligibility or accepting model-generated prices.

Run `uv run --no-sync python -m pytest tests/test_answer_presentation.py
 tests/test_presentation_contracts.py -q` (join onto one shell line).
Tests cover immutable projections, identity isolation, exact spans, duplicate
IDs/details, row bounds, capability/recovery refusal and optional runtime schema
adaptation. These are not live-model or frontend acceptance results.
