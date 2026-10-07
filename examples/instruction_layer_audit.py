"""Credential-free final instruction audit: one broken and one corrected request."""
from adkl.instruction_audit import InstructionSpan, audit_request


def main():
    kernel = "Answer from verified evidence. Authorization is enforced by trusted code."
    procedure = "Clarify purchase versus billed charge before investigating."
    state = "Current page: cart."
    description = "Read public support evidence. Returns current policy; no account mutation."
    system = "\n".join([kernel, procedure, state])
    spans = [
        InstructionSpan("system", 0, len(kernel), "system", "universal", "kernel"),
        InstructionSpan("system", len(kernel) + 1, len(kernel) + 1 + len(procedure),
                        "skill", "procedure", "fee-help"),
        InstructionSpan("system", len(system) - len(state), len(system),
                        "state", "data", "session", True),
        InstructionSpan("tool:search_supports", 0, len(description),
                        "tool", "interface", "search_supports"),
    ]
    arguments = dict(system=system, tools={"search_supports": description}, spans=spans,
                     required_owners={"search_supports": ["fee-help"]}, phase="cold")
    broken = audit_request(**arguments, active_owners=[])
    assert {f["rule"] for f in broken["findings"]} == {
        "INSTRUCTION_OWNER_INACTIVE", "INSTRUCTION_TOOL_OWNER_BYPASS"}
    corrected = audit_request(**arguments, active_owners=["fee-help"])
    assert corrected["passed"] and not corrected["findings"]
    print("Flagged inactive procedure and owner bypass; corrected fixture passes.")
    print("Structural synthetic evidence only; no model or business tool executed.")


if __name__ == "__main__":
    main()
