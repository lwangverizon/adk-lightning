# Shared presentation contracts

`adkl.presentation_contracts` supplies a pure, versioned reply-shaping policy for
hosts that want to keep native widget eligibility and instruction injection.
It imports only stdlib and never inspects state, calls tools/models, installs
callbacks, changes requests or asserts a widget was delivered.

The recommended `visual-v2` policy is **opt-in**: compact reply
shapes with explicit line-break guidance. The initial, longer `visual-v1` remains
frozen for reproducible comparisons, not as the recommended integration target.
Neither version is claimed to be live-model graded. Existing
`adkl.widgets.instruction_for()` wording and automatic injection remain unchanged
for existing roles. There is no new selectable runtime feature or implicit
SDK-upgrade switch to either policy.

## Role contracts

These policies shape replies; deterministic SDK checks do not establish
live-model quality or production outcomes.

| Eligible role | Primary contract | Reply responsibility |
| --- | --- | --- |
| `data_with_written_table` | `synthesis_with_table` | Host explicitly requires a written comparison in addition to the visual; preserve units, periods, unknowns and qualifications |
| `data_primary` | `synthesis` | Lead with the supported takeaway and decision-relevant drivers; visual holds detailed rows |
| `action_with_steps` | `step_handoff` | Card carries the answer (steps, figures or prose); concise but useful textual takeaway |
| `input_request` | `elicitation` | What is needed, why, and how to respond; not a fictitious destination |
| `navigation` | `navigation_handoff` | Button replaces directions to the destination; brief purpose, with prerequisites, essential after-click guidance, disclosures and separate answers preserved |
| `action_primary` | `guidance` | Explain destination/purpose, prerequisites and verified procedure |
| `supporting` | `answer` | Text carries the actual answer; visual is supplementary |

Table order is primary precedence. Empty roles produce no contract and no text.
The existing telemetry strings `action_with_steps` and `step_handoff` are retained
rather than renamed as part of semantic generalization. All known role values
are accepted as strings or enums; unknown values fail explicitly.

Unlike a winner-only prompt, a data/card turn co-staged with an input request or
an action retains a brief instruction covering that secondary responsibility.
Hosts must pass **all eligible roles** to preserve these mixed-turn obligations,
not only the already-collapsed winning role. Presence alone is not eligibility.

`navigation` is an explicit additional role; existing role sets keep their policy
text. The built-in commerce navigation presentation selects it; adopted hosts
select it on their eligible navigation specs. `navigation_handoff_guidance()` and
`navigation_secondary_guidance()` expose the canonical fragments for hosts that
retain their own co-emission/marker format. Mixed data/navigation turns retain
the main answer contract and a navigation-specific secondary obligation. A
button is not evidence of completed work. If a card is unavailable, the host must
provide a grounded text fallback; a legacy card unable to display answer detail
must not shorten that answer. Discovery-channel guidance must be conditional on
actually selecting/staging a usable destination.


## Compact policy and readable output

`visual-v2` uses compact reply shapes rather than a general response standard.
Its shorter prose is not evidence of improved model outcomes.

The shared layout rule is explicit about the **generated answer**, not just
formatting the prompt itself:

> Separate distinct points with blank lines; put each bullet or numbered step
> on its own line.

The contract still bounds the answer: a takeaway or input request stays one or
two sentences; procedures use up to five ordered steps; synthesis/supporting
facts use up to three points. No mandatory headings, section count, bullets for
every reply or extra closing boilerplate are introduced. Tables remain tables.
Required disclosures and essential steps take precedence over brevity.

Whitespace word counts (not tokenizer measurements):

| Role | `visual-v2` | v2 with co-emission | v1 with co-emission |
| --- | ---: | ---: | ---: |
| Supporting | 57 | 74 | 152 |
| Data | 52 | 69 | 148 |
| Written table | 59 | 76 | 148 |
| Destination | 60 | 77 | 146 |
| Answer-bearing card | 57 | 74 | 151 |
| Input request | 54 | 71 | 145 |

The v2 bounds are 60 words for a single role, 80 with co-emission and 100 for any
mixed-role combination without navigation. New mixed navigation combinations
have a 135-word / 1,100-character ceiling to preserve the additional obligation.
Short secondary clauses preserve input/action obligations.
V1 remains bounded separately at 240 words / 1,800 characters.

Illustrative layout target, **not a captured model response**:

```text
The standard fee is $40.

- Applies once per activation.
- A waiver requires the stated qualifying offer.
```

In this illustrative fixture the amount and conditions are supplied facts, not
an authoritative fee policy. A one-sentence fee answer needs no list. A verified
multi-step procedure instead uses `1.`, `2.`, etc., each on its own line. Adding
line breaks must not add facts, omit qualifiers or turn a short answer into a
longer one. The host still owns domain grounding, authorization, transport and
complete accessible/replay fallbacks. Neither SDK policy rewrites streamed text
or guarantees that a model/renderer preserves whitespace.

## Host-owned injection

```python
from adkl.presentation_contracts import VISUAL_V2, plan_for_roles

# roles comes from the host's native gates: dirty/revived, not already emitted,
# supported channel, not suppressed, nonempty and not expected-empty.
plan = plan_for_roles(
    ["data_primary", "input_request"],
    version=VISUAL_V2,
)
if plan.contract is not None:
    llm_request.append_instructions([plan.render()])
    # Record plan.version, plan.contract.value, plan.roles and host source IDs
    # in the host's existing invocation-scoped telemetry, not this library.
```

`PresentationPlan` is immutable and records `version`, ordered/deduplicated
`roles`, `contract`, `same_message` and plain `text`. `render(channel="instruction")`
wraps that text once with a stable contract/version tag. `render(channel="tool_text")`
returns the **exact same sentences without markup**. Unknown versions/channels
raise `ValueError`; no default `latest` silently changes a deployed prompt.

Tool-text is for a host-controlled discovery channel that must supply guidance
before a subsequent same-pass staging/answer. It is not a reason to promote
untrusted tool payloads into system instructions, and it does not override
instruction priority. Host authorization and channel allowlisting still apply.

`same_message=True` adds conditional text-plus-call guidance only when the host
supports co-emission for presentation-only staging/curation. Default is false.
Do not use it to pre-announce purchases, verification results or other business
operations whose results are still needed. It cannot force a model/provider to
emit text and a function call together and cannot recover text already skipped
by early termination.

## Ownership and migration

ADKL owns vocabulary, precedence and versioned common wording. The host retains:

- native widget eligibility, suppression, revival, rendering/channel checks;
- per-agent feature flags (including supporting/answer rollout), exception policy;
- exactly-once request insertion and invocation diagnostics;
- domain figures, business disclosures, consent, authorization and actions;
- deterministic companion fallback, transport, accessible/history replay.

Keep `staged_widgets.inject_contract=False` when the host injects. Do not install
a second callback or recompute gates in ADKL. Hosts may call `contract_for_roles`
for vocabulary-only adoption independently of versioned wording.

For a host migrating to this policy:

1. Pin the SDK wheel revision/hash; retain the existing host callback and flags.
2. Preserve native gate behavior and expose all qualifying roles from the same
   gate pass; retain source IDs and decline diagnostics. Do not substitute the
   SDK gate resolver: exception/fallback semantics differ between the hosts.
3. Replace local common wording with `visual-v2` behind an explicit rollout switch.
   Do not append the old block as well. Keep required domain constraints in their
   host-owned policy, not arbitrary SDK prompt inputs.
4. Record shared policy version **and** agent rollout version; update prompt
   placement/marker tests and tool-text channel tests. Do not assume old XML
   stripping code accepts the versioned tag.
5. Compare old/new prompts, actual response behavior, tokens, TTFT and end-to-end
   latency in host staging. No production/host repo is changed by this SDK work.

## Output-format review

Reviewed against [W3C WAI writing guidance](https://www.w3.org/WAI/tips/writing/):
short, clear sentences and paragraphs; appropriate list formatting; meaningful
headings for longer content; descriptive link text. The compact policy follows
that guidance with explicit paragraph/list breaks and task-specific depth.
It does not mandate headings on short chat replies or prohibit them when a longer
answer needs structure. Link semantics and accessible renderer markup remain host
responsibilities; prompt formatting alone is not WCAG conformance.

The review retained answer-first wording, ordered verified procedures, a compact
written-table exception, meaningful standalone takeaways and required qualifiers.
It rejected compulsory headings/closings and rewriting streamed text after the
fact: either can inflate replies or damage tables, links and disclosures.
W3C guidance supports these design choices, not a claim that this prompt has
proven superior in live chatbot traffic. Other vendor pages could not be reliably
retrieved during this review and are not cited as verified evidence.

## Evaluation before claiming better visual presentation

Deterministic tests cover every role combination/permutation, unknown inputs,
channel identity, immutability, prompt-size ceilings, adopted table roles and a
real synthetic native Runner with exactly one host injection. They do **not**
score generated answers or verify real widget rendering.

Use the host evaluator with fixed old/new revisions, model/provider versions and
representative rendered payloads. Required cases:

| Case | Human/model-response review criteria |
| --- | --- |
| Crowded answer / procedure | Distinct paragraphs separated by blank lines; each bullet/numbered step on its own line, with facts and qualifiers unchanged |
| Brief answer | One or two sentences stay brief; no obligatory headings or list expansion |
| Comparison chart/data table | Grounded conclusion; material caveat; no full row transcription |
| Required written comparison | Correct figures, units/periods and missing values; required table survives other widgets |
| Answer-bearing card | Useful takeaway; limited duplication; full answer recoverable in replay/fallback |
| Destination-only card | Verified purpose/procedure; no invented steps or completed-action claim |
| Form/upload/choice | Clear reason and response; minimal necessary personal information |
| Data + form / data + action | Both interpretation and required next interaction remain clear |
| Supporting-only / no widget | Answer remains in text; no unsolicited action or reference to nonexistent UI |
| Suppressed, empty, unsupported, failed delivery | Native gates/fallback remain correct; no false delivery assurance |
| Long safety procedure/disclosure | Essential content survives the soft length targets |
| Staging + text / business action pending | Supported co-emission only; no premature outcome claims |

Measure response completeness, grounding, duplication, disclosure retention,
companion absence, rendering/replay accessibility and task success alongside
prompt/response tokens and latency. Use representative host traffic/load fixtures;
no added network/model work does not mean mathematically zero runtime overhead.
