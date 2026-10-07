"""Pure, versioned reply-shaping policy for host-selected visual roles.

No state inspection, injection, tools, network calls or delivery assertions.
Hosts supply only eligible roles and retain authorization and business policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Iterable


class Role(str, Enum):
    """What an eligible widget contributes, not its renderer/component type."""

    SUPPORTING = "supporting"
    DATA_PRIMARY = "data_primary"
    DATA_WITH_WRITTEN_TABLE = "data_with_written_table"
    ACTION_PRIMARY = "action_primary"
    NAVIGATION = "navigation"
    # Stable host telemetry spelling; applies to any answer-carrying card.
    ACTION_WITH_STEPS = "action_with_steps"
    INPUT_REQUEST = "input_request"


class Contract(str, Enum):
    SYNTHESIS = "synthesis"
    SYNTHESIS_WITH_TABLE = "synthesis_with_table"
    GUIDANCE = "guidance"
    NAVIGATION_HANDOFF = "navigation_handoff"
    STEP_HANDOFF = "step_handoff"
    ELICITATION = "elicitation"
    ANSWER = "answer"


_PRECEDENCE = (
    (Role.DATA_WITH_WRITTEN_TABLE, Contract.SYNTHESIS_WITH_TABLE),
    (Role.DATA_PRIMARY, Contract.SYNTHESIS),
    (Role.ACTION_WITH_STEPS, Contract.STEP_HANDOFF),
    (Role.INPUT_REQUEST, Contract.ELICITATION),
    (Role.NAVIGATION, Contract.NAVIGATION_HANDOFF),
    (Role.ACTION_PRIMARY, Contract.GUIDANCE),
    (Role.SUPPORTING, Contract.ANSWER),
)


def contract_for_roles(roles: Iterable[Role | str]) -> Contract | None:
    """Collapse known roles deterministically; reject unknown roles, not typos to silence.

    Accept enum values/strings for native hosts; consume a generator only once.
    Eligibility and fail-open handling remain host decisions.
    """
    present = {Role(role) for role in roles}
    return next((contract for role, contract in _PRECEDENCE if role in present), None)


VISUAL_V1 = "visual-v1"
VISUAL_V2 = "visual-v2"

def navigation_handoff_guidance() -> str:
    """Canonical navigation detail policy for host-owned prompt composition.

    Use only for an eligible navigation action, or conditionally before staging.
    This fragment does not assert delivery or replace the host's visible-reply,
    co-emission, eligibility or fallback responsibilities.
    """
    return (
        "Explain the destination's purpose in a sentence or two. Let its button "
        "replace navigation directions and its card carry displayed steps. Preserve "
        "prerequisites, essential after-click guidance, disclosures and separate answers."
    )


def navigation_secondary_guidance() -> str:
    """Navigation obligation retained when another eligible role takes precedence."""
    return (
        "Let navigation buttons replace directions to their destinations; "
        "keep displayed steps in cards and essential after-click guidance in text."
    )


# Immutable instruction sources: positive, task-specific guidance, no brand,
# widget position, mandatory upsell or invented delivery guarantees.
_VISUAL_V1 = {
    Contract.NAVIGATION_HANDOFF: navigation_handoff_guidance(),
    Contract.SYNTHESIS: (
        "Lead with the conclusion supported by the available data. Explain up to three "
        "decision-relevant differences or change drivers, using key figures when useful. "
        "Let the visual carry the detailed rows rather than recreating its list or table. "
        "State material uncertainty or qualifications; offer a next step only when useful."),
    Contract.SYNTHESIS_WITH_TABLE: (
        "Lead with the supported conclusion. Include the host-required comparison as a compact "
        "written table with meaningful headers, units and aligned comparison periods. Use only "
        "available figures; mark missing values as unknown. Explain up to three material change "
        "drivers. This explicitly required table complements the visual; keep other details there."),
    Contract.GUIDANCE: (
        "Explain what the offered destination or action helps the user do and any prerequisite. "
        "When verified instructions are available, give a short ordered procedure, aiming for up "
        "to five steps without dropping essential steps. Otherwise give a short explanation and "
        "the next action, without inventing a procedure."),
    Contract.STEP_HANDOFF: (
        "The selected card is intended to carry the answer, whether steps, figures or an "
        "explanation. Give a useful one- or two-sentence takeaway with the outcome or key fact "
        "and any prerequisite or material limit. Keep detailed repetition in the card rather "
        "than a second walkthrough; make the takeaway meaningful without seeing it."),
    Contract.ELICITATION: (
        "Briefly explain what input is needed and why it matters, then give a clear way to "
        "respond. Aim for one or two sentences; let the input surface show its fields or "
        "choices. Request only information required for this task and preserve required consent "
        "or privacy guidance."),
    Contract.ANSWER: (
        "The reply carries the answer; the visual is supporting. Lead with the direct answer "
        "in one or two sentences, then up to three useful supporting facts when needed. "
        "Prefer grounded specifics to generalities. Let the supporting visual carry its own "
        "content, and suggest a next step only if it advances the user's request."),
}

_COMMON = (
    "Provide a useful visible reply alongside the planned visuals. Ground statements in "
    "available results; staging is not evidence of rendering, user submission or completed action. "
    "Preserve required disclosures, safety instructions, consent and material qualifications "
    "even when they exceed the suggested length. Use short paragraphs or a brief list, "
    "with descriptive action labels rather than positional references."
)
_COEMIT = (
    "If making a presentation-only staging or curation call whose outcome is not needed "
    "to establish the answer, include the visible reply in the same message when the host "
    "supports it. Wait for any still-needed business or verification results before making "
    "claims about them."
)
_MIXED_INPUT = (
    "A separate input request also needs a brief explanation of what is needed and why; "
    "make the required response clear without implying it was already provided."
)
_MIXED_ACTION = (
    "For the accompanying destination or action, give its purpose and any essential "
    "prerequisite not covered by the main answer; an offered control is not a completed action."
)


# Compact successor. Keep v1 frozen:
# an explicit policy version must not silently acquire different prompt text.
_VISUAL_V2 = {
    Contract.NAVIGATION_HANDOFF: navigation_handoff_guidance(),
    Contract.SYNTHESIS: (
        "State the conclusion, explain up to three user-relevant differences or change "
        "drivers, and note any material caveat. Let the visual carry detailed rows."),
    Contract.SYNTHESIS_WITH_TABLE: (
        "State the conclusion, then give the required comparison as a written markdown "
        "table using available figures, units and periods; mark unknowns. Note material "
        "caveats and at most three change drivers."),
    Contract.GUIDANCE: (
        "Name what the destination does for this request. When retrieved results carry "
        "steps, list up to five in order; otherwise use a short paragraph. "
        "State any limit and the next action."),
    Contract.STEP_HANDOFF: (
        "The card carries the answer. Give a meaningful takeaway: the outcome or "
        "headline figure, plus any limit or prerequisite. A sentence or two; "
        "the card holds the detail."),
    Contract.ELICITATION: (
        "Say what input you need and why it changes the answer, plus how to provide "
        "it. Up to two sentences; the widget shows the fields."),
    Contract.ANSWER: (
        "Lead with the direct answer in a sentence or two, then up to three useful "
        "supporting facts. Prefer grounded specifics; let the supporting card carry "
        "its own content."),
}
_V2_OPEN = "Write the visible reply now."
_V2_LAYOUT = (
    "Separate distinct points with blank lines; put each bullet or numbered step "
    "on its own line."
)
_V2_SAFETY = "Preserve required disclosures and essential steps over brevity."
_V2_COEMIT = (
    "If also calling presentation-only staging or curation, reply in that same "
    "message; wait for needed business results."
)
_V2_MIXED_INPUT = "For a separate input request, say what is needed and why."
_V2_MIXED_ACTION = "Give the accompanying action's purpose and prerequisite, without claiming completion."


@dataclass(frozen=True)
class PresentationPlan:
    """Immutable instruction result; not an execution or delivery plan.

    ``roles`` records all eligible roles, even those below primary precedence.
    ``same_message`` records the host's opt-in to supported co-emission guidance.
    """

    version: str
    roles: tuple[Role, ...]
    contract: Contract | None
    same_message: bool
    text: str

    def render(self, *, channel: str = "instruction") -> str:
        """One source of wording for prompt-tail and plain tool-result channels.

        Hosts must not promote arbitrary tool content into trusted instructions.
        This method only formats SDK-owned policy text, never tool payloads.
        """
        if channel not in {"instruction", "tool_text"}:
            raise ValueError("channel must be instruction or tool_text")
        if not self.text:
            return ""
        if channel == "tool_text":
            return self.text
        return (f'<presentation_contract id="{self.contract.value}" version="{self.version}">\n'
                f'{self.text}\n</presentation_contract>')


def plan_for_roles(roles: Iterable[Role | str], *, version: str,
                   same_message: bool = False) -> PresentationPlan:
    """Prepare opt-in visual policy from roles already qualified by the host.

    Required version prevents a future policy from silently replacing an existing
    host prompt. Unknown versions/roles raise ValueError. No injection is installed.
    ``same_message`` requires host support for text + presentation calls and must
    not be used to announce unverified business actions.
    """
    if version not in (VISUAL_V1, VISUAL_V2):
        raise ValueError(f"Unsupported presentation policy version: {version!r}")
    if not isinstance(same_message, bool):
        raise TypeError("same_message must be a boolean")
    present = {Role(role) for role in roles}
    ordered = tuple(role for role, _ in _PRECEDENCE if role in present)
    contract = contract_for_roles(ordered)
    parts = []
    if contract is not None and version == VISUAL_V2:
        parts = [_V2_OPEN]
        if same_message:
            parts.append(_V2_COEMIT)
        parts.extend([_VISUAL_V2[contract], _V2_LAYOUT, _V2_SAFETY])
        if Role.INPUT_REQUEST in present and contract != Contract.ELICITATION:
            parts.append(_V2_MIXED_INPUT)
        if Role.ACTION_PRIMARY in present and contract != Contract.GUIDANCE:
            parts.append(_V2_MIXED_ACTION)
    elif contract is not None:
        parts = [_COMMON, _VISUAL_V1[contract]]
        if Role.INPUT_REQUEST in present and contract != Contract.ELICITATION:
            parts.append(_MIXED_INPUT)
        if Role.ACTION_PRIMARY in present and contract != Contract.GUIDANCE:
            parts.append(_MIXED_ACTION)
        if same_message:
            parts.append(_COEMIT)
    if Role.NAVIGATION in present and contract != Contract.NAVIGATION_HANDOFF:
        parts.append(navigation_secondary_guidance())
    return PresentationPlan(version, ordered, contract, same_message, "\n".join(parts))
