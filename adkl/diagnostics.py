"""Shared diagnostic presentation and subject contract; no host imports."""

GUIDANCE = {
    "INSTRUCTION_PROCEDURE_WRONG_LAYER": ("Content", "A domain procedure is attributed to a global or interface layer.",
        "Keep universal policy in the system kernel, intrinsic contracts in tool descriptions, and task decisions in the active skill/workflow. Verify the served owner before removing text."),
    "INSTRUCTION_OWNER_INACTIVE": ("Content", "Conditional guidance is present without its declared owner active.",
        "Inspect the final request after callbacks and retrieval. Shortlisting is not activation. Remove stale ambient procedures without dropping required safety policy."),
    "INSTRUCTION_TOOL_OWNER_BYPASS": ("Tools", "A tool bypasses its declared instruction owner.",
        "Review standalone, cold-start and retained-tool exposure. Load/prepare the appropriate owner before exposing the tool; do not copy domain policy into the global prompt. Authorization remains a code boundary."),
    "INSTRUCTION_PROVENANCE_INCOMPLETE": ("Content", "Some final instruction text has no declared source/layer.",
        "Attribute all final system text and outgoing tool descriptions after every transform. Investigate late callback injections; do not label unknown text universal merely to pass."),
    "INSTRUCTION_STATIC_AFTER_DYNAMIC": ("Cost", "Static instructions follow dynamic context.",
        "Review final assembly order while preserving presentation and trust boundaries. Measure provider cache behavior; byte ordering alone does not establish savings."),
    "INSTRUCTION_TEXT_DUPLICATED": ("Content", "Long instruction sentences repeat in the assembled capture.",
        "Choose one prose owner across kernel, tool interface and active procedure. Review safety exceptions and every serving variant before deduplicating."),
    "INSTRUCTION_STATE_POLICY": ("Content", "State is attributed as instruction policy.",
        "Keep session/tool evidence as data; declare and validate trusted application controls separately. A field name or model-visible tag grants no authority."),
    "INSTRUCTION_TOOL_LAYER_MISMATCH": ("Tools", "Tool description carries guidance owned by another layer.",
        "Keep identity, inputs, outputs, side effects and intrinsic preconditions in the tool interface. Move routing and cross-tool sequences to the activated owner."),
    "INSTRUCTION_SYSTEM_BUDGET": ("Cost", "Final system text exceeds the host's byte budget.",
        "Review all assembled layers and variants. Preserve prerequisites and recovery; evaluate quality, calls and real provider usage before claiming optimization."),
    "INSTRUCTION_TOOL_BUDGET": ("Cost", "An outgoing tool description exceeds the host's byte budget.",
        "Keep the tool's real contract and remove unrelated procedure detail. Bytes are not tokens; measure schema overhead and host outcomes separately."),
    "TOOL_OUTPUT_VERSION_MISMATCH": ("Tools", "A declared consumer targets a different tool output version.",
        "Review the tool's listed consumers together. Update the consumer implementation and explicit field contract, then run host fixture and behavior tests; do not merely bump a version to hide incompatibility."),
    "TOOL_OUTPUT_CONSUMER_MISMATCH": ("Tools", "A consumer requires a field or type absent from the declared output.",
        "Reconcile the consumer and output contract in doctor-tools.json. Preserve per-item semantics and test the real formatter/prompt assembly; static declarations do not inspect prose or execute consumers."),
    "TOOL_OUTPUT_FIXTURE_MISMATCH": ("Tools", "A synthetic fixture does not establish the declared field path and type.",
        "Inspect the fixture locally and capture representative synthetic tool output. Include a nonempty item for wildcard paths. Correct the producer or contract rather than weakening checks to silence drift."),
    "VERIFICATION_FAILED": ("Verification", "The retained current host attempt failed or regressed.",
        "Inspect the retained verification run locally. Fix the failing behavior and run python -m adkl.verification candidate in the trusted host environment; Doctor never executes it."),
    "VERIFICATION_NOT_CURRENT": ("Verification", "No current passing host candidate is established.",
        "Configure verification.yaml and record a baseline, then run the candidate explicitly. Match the Doctor profile; do not treat a baseline, stale report or missing run as a pass."),
    "CONTRACT_PROBE_FAILED": ("Verification", "A lifecycle contract has failing host observations.",
        "Reproduce with scripted models and fake backends at the actual execution boundary. Preserve consent, callback ownership and state scope; rerun the host candidate and Doctor."),
    "CONTRACT_EVIDENCE_REQUIRED": ("Verification", "An explicitly required contract lacks current passing host evidence.",
        "Run the host's synthetic regression evaluator with the matching profile and complete cases, then rerun Doctor with the same --require-contract options. Do not remove requirements or relabel stale evidence to obtain a pass."),
    "CONTRACT_EVIDENCE_MISSING": ("Verification", "A declared lifecycle contract lacks current passing candidate evidence.",
        "Tag relevant suite cases with adkl_contracts and execute the configured host evaluator explicitly. Include failure, cancellation and recovery cases; a tag alone is not execution proof."),
    "PROMPT_POLICY_REPEATED": ("Content", "Long prompt sentences are repeated.",
        "Give each instruction one owner. Keep safety and authorization independent of skill selection; measure assembled request costs."),
    "PROMPT_SKILL_POLICY_DUPLICATED": ("Content", "Prompt and skill repeat a policy sentence.",
        "Keep universal policy in the kernel and workflow detail in the skill. Review every consumer before moving instructions."),
    "PROMPT_SURFACE_CONFLICT": ("Presentation", "Explicit formatting directives disagree.",
        "Compose channel-specific presentation policy instead of appending an opposing directive. Verify each consumer's behavior."),
    "PROMPT_SKILL_LOADER_NOT_IN_INVENTORY": ("Tools", "Prompt delegates policy to a skill without a recorded loader.",
        "Check actual consumer capabilities. Share required policy directly with direct-tool consumers rather than relying on an unavailable skill."),
    "PROMPT_TOOL_NOT_IN_INVENTORY": ("Tools", "A prompt directive names a tool absent from the supplied inventory.",
        "Inspect implicit and dynamic bindings before concluding the tool is unavailable. Adapt the prompt to the consumer; do not widen tool scope or authorization to silence this check."),
    "TOOL_NOT_IN_ANY_SKILL": ("Tools", "No skill references this catalog tool.",
        "Check host registration and direct callers before removing anything. No skill link does not mean unused or unreachable."),
    "TOOL_DECLARATION_LARGE": ("Cost", "Estimated tool schema context is large.",
        "Review optional context, then measure actual token use and task success. This is an estimate, not a measured bill."),
    "TOOL_DOCSTRING_HEAVY": ("Cost", "Estimated tool description context is large.",
        "Keep parameter meaning and required constraints; move optional implementation notes into code comments. Verify behavior."),
    "TOOL_PARAMS_UNDOCUMENTED": ("Tools", "Tool parameters lack descriptions.",
        "Describe the reported parameters in the tool source, including their meaning and constraints."),
    "AUTHORING_SOURCE_UNMAPPED": ("Authoring", "Inspection works but no editable skill source is mapped.",
        "Map the SKILL.md actually loaded by the factory, then inspect again."),
    "SKILL_BODY_LARGE": ("Cost", "Long instructions increase context use; the estimate is advisory, not a measured bill.",
        "Separate optional explanation from stateful workflow complexity. Keep routing, consent, required steps and recovery together; split a skill only with explicit state ownership. Measure assembled requests and test continuation before accepting compression."),
    "TURN_COST_HIGH": ("Cost", "Estimated instruction and tool context is large.",
        "Review instruction length and scoped tool exposure without broadening permissions. Measure assembled requests, cached/provider usage and task success; reference extraction can add calls. Do not infer savings from source length alone."),
    "SKILL_VARIANT_TOOLS_DIFFER": ("Tools", "Same-named skill variants declare different tools; a fix may reach only some served copies.",
        "Compare the listed variants. Keep intentional environment or channel differences, and apply behavior fixes, tool removals and tool additions to every served copy. Regenerate retrieval artifacts and run each surface's tests."),
    "FACTORY_CONSTRUCTION_ERROR": ("Configuration", "The factory logged an error while it was built; a caught failure can leave a fallback configuration.",
        "Reproduce construction locally and fix the logged failure, or confirm the fallback is intended. Assert the expected runtime lane in host startup tests; do not silence the log."),
    "FACTORY_CONSTRUCTION_WARNING": ("Configuration", "The factory logged a warning while it was built.",
        "Review whether the warning changes the constructed agent, tools or features. Fixture-only infrastructure warnings may be expected in offline inspection."),
    "CHECK_FAILED": ("Diagnostics", "The doctor could not establish evidence for this check.",
        "Inspect the project's configuration and locked dependencies locally, then rerun this check. Do not treat missing evidence as a pass."),
    "TOOL_UNRESOLVED": ("Tools", "A declared tool is absent from the checked inventory.",
        "Check the tool name and constructor registration, or remove an obsolete declaration. Preserve authorization and skill scope."),
    "TOOL_USED_NOT_DECLARED": ("Tools", "Instructions may request an unavailable tool.",
        "Reconcile the instruction with the intended skill tool bindings; do not promote tools globally to silence this finding."),
    "WIDGET_OUTPUT_MISSING": ("Presentation", "No widget-emitting tool is declared; runtime output is not verified.",
        "Bind a display tool the skill's steps actually call, or record an intentional text-only skill with "
        "`metadata.adkl_presentation: text_only`. A bound tool is a declaration, not evidence a widget reached the customer."),
    "WIDGET_OUTPUT_ANNOTATION_STALE": ("Presentation", "A text-only annotation contradicts the skill's own tool bindings.",
        "Remove the annotation, or remove the display tool it claims the skill does not use. Do not rely on it to suppress findings."),
    "PRESENTATION_METADATA_INVALID": ("Presentation", "A presentation annotation is not a value the checks recognize.",
        "Use `metadata.adkl_presentation: text_only`, or delete the key. An unrecognized value records no intent."),
    "AUTHORING_SOURCE_DRIFT": ("Authoring", "The editor mapping does not match the inspected factory.",
        "Map the actual SKILL.md loaded by the factory, then inspect again. Preserve the factory as the runtime authority."),
    "ENVIRONMENT": ("Environment", "Local inspection prerequisites are incomplete.",
        "Correct the execution profile or prepare the host's locked environment. Keep credentials out of source."),
    "CONFIG": ("Configuration", "The project descriptor is invalid or inconsistent.",
        "Correct the reported configuration field using the SDK version installed by this host."),
    "SDK_UNSUPPORTED": ("Configuration", "The requested behavior is not executable by this SDK.",
        "Use a supported configuration or retain the host implementation; do not bypass capability validation."),
}


TITLES = {
    "SKILL_BODY_LARGE": "Long instructions",
    "TURN_COST_HIGH": "Large context estimate",
    "WIDGET_OUTPUT_MISSING": "No widget declared",
    "WIDGET_OUTPUT_ANNOTATION_STALE": "Text-only annotation conflicts",
    "TOOL_UNRESOLVED": "Tool not found",
    "TOOL_USED_NOT_DECLARED": "Tool not declared",
    "TOOL_NOT_IN_ANY_SKILL": "Not linked to a skill",
    "TOOL_DECLARATION_LARGE": "Large tool schema",
    "TOOL_DOCSTRING_HEAVY": "Long tool description",
    "TOOL_PARAMS_UNDOCUMENTED": "Parameters need descriptions",
    "AUTHORING_SOURCE_UNMAPPED": "Source not mapped",
    "AUTHORING_SOURCE_DRIFT": "Source mapping differs",
    "SKILL_VARIANT_TOOLS_DIFFER": "Variants declare different tools",
    "FACTORY_CONSTRUCTION_ERROR": "Error logged during construction",
    "FACTORY_CONSTRUCTION_WARNING": "Warning logged during construction",
}
TOOL_RULES = frozenset({"TOOL_NOT_IN_ANY_SKILL", "TOOL_DECLARATION_LARGE",
                        "TOOL_DOCSTRING_HEAVY", "TOOL_PARAMS_UNDOCUMENTED"})


def describe(raw):
    """Add metadata without changing severity, evidence, or legacy subject field."""
    rule = raw.get("rule", "CHECK_FAILED")
    name = raw.get("contract") or raw.get("skill") or None
    category, impact, fix = GUIDANCE.get(rule, (
        "Content", "Review the reported evidence; this is not runtime verification.",
        "Review the reported rule in context. Preserve domain policy and behavior; rerun the doctor and relevant host tests."))
    return {**raw, "title": TITLES.get(rule, rule.replace("_", " ").lower()),
            "subject": {"kind": "contract" if raw.get("contract") else "tool" if rule in TOOL_RULES else "skill" if name else "project", "name": name},
            "category": category, "impact": impact, "remediation": fix}


def enrich_payload(payload):
    """Enrich findings in editor responses, including nested lint/save results."""
    if not isinstance(payload, dict):
        return payload
    return {key: [describe(f) if isinstance(f, dict) and "rule" in f else f for f in value]
            if key in {"findings", "allFindings"}
            and isinstance(value, list) else enrich_payload(value)
            for key, value in payload.items()}
