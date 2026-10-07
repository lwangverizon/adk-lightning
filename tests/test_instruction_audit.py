"""Final-boundary ownership checks are structural, sanitized and read-only."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from adkl import doctor, execution
from adkl.instruction_audit import InstructionSpan, audit_request, inspect_request, main


POLICY = "Use verified evidence for every claim and never infer permission from customer supplied account identifiers."


def capture():
    blocks = [POLICY, "Clarify the charge scope before any lookup.", "Current cart: synthetic."]
    system = "\n".join(blocks)
    spans, start = [], 0
    for text, layer, scope, owner, dynamic in zip(blocks,
            ["system", "skill", "state"], ["universal", "procedure", "data"],
            ["kernel", "fees", "session"], [False, False, True], strict=True):
        spans.append(InstructionSpan("system", start, start + len(text), layer, scope, owner, dynamic))
        start += len(text) + 1
    description = "Read current public support evidence. Returns source text; does not change accounts."
    spans.append(InstructionSpan("tool:search_supports", 0, len(description), "tool", "interface", "search_supports"))
    return dict(system=system, tools={"search_supports": description}, spans=spans,
                active_owners=["fees"], required_owners={"search_supports": ["fees", "support"]}, phase="cold")


def rules(result):
    return {row["rule"] for row in result["findings"]}


def serializable(data):
    return {**data, "spans": [asdict(span) for span in data["spans"]]}


@pytest.mark.parametrize("phase", ["cold", "followup", "topic_switch", "recovery"])
def test_loaded_skill_in_system_transport_is_not_global_policy(phase):
    data = capture()
    result = audit_request(**{**data, "phase": phase})
    assert result["passed"] and not result["findings"]
    assert result["metrics"]["unattributedCharacters"] == 0
    assert result["behavioralEvaluation"] == "not_run"
    assert POLICY not in json.dumps(result)


def test_wrong_layer_and_standalone_bypass_are_independent_findings():
    data = capture()
    data["spans"][1] = replace(data["spans"][1], layer="system")
    data["active_owners"] = []
    result = audit_request(**data)
    assert rules(result) == {"INSTRUCTION_PROCEDURE_WRONG_LAYER", "INSTRUCTION_TOOL_OWNER_BYPASS"}
    assert not result["passed"]


def test_retained_procedure_is_not_activated_by_shortlisting():
    data = capture()
    data["active_owners"] = []
    result = audit_request(**data)
    assert "INSTRUCTION_OWNER_INACTIVE" in rules(result)
    assert "INSTRUCTION_TOOL_OWNER_BYPASS" in rules(result)


def test_tool_interface_preconditions_are_not_a_domain_procedure():
    data = capture()
    assert audit_request(**data)["passed"]
    data["spans"][-1] = replace(data["spans"][-1], scope="procedure")
    assert "INSTRUCTION_PROCEDURE_WRONG_LAYER" in rules(audit_request(**data))


def test_unmapped_late_callback_is_not_silently_clean():
    data = capture()
    data["system"] += "\nSECRET: late domain instructions"
    result = audit_request(**data)
    assert "INSTRUCTION_PROVENANCE_INCOMPLETE" in rules(result)
    assert "SECRET" not in json.dumps(result)


def test_static_after_state_and_terminal_presentation_exception():
    data = capture()
    start = len(data["system"])
    text = "Catalog entry description"
    data["system"] += text
    span = InstructionSpan("system", start, start + len(text), "system", "universal", "catalog")
    data["spans"].append(span)
    assert "INSTRUCTION_STATIC_AFTER_DYNAMIC" in rules(audit_request(**data))
    data["spans"][-1] = replace(span, layer="presentation", scope="presentation")
    assert "INSTRUCTION_STATIC_AFTER_DYNAMIC" not in rules(audit_request(**data))


def test_duplicate_guidance_across_tool_and_kernel_without_raw_text():
    data = capture()
    data["tools"]["search_supports"] = POLICY
    data["spans"][-1] = replace(data["spans"][-1], end=len(POLICY))
    result = audit_request(**data)
    assert rules(result) == {"INSTRUCTION_TEXT_DUPLICATED"}
    assert POLICY not in json.dumps(result)


def test_only_unconditional_universal_format_conflicts_are_flagged():
    text = "Always use markdown. Never use markdown."
    data = dict(system=text, tools={}, active_owners=[], required_owners={}, phase="cold",
                spans=[InstructionSpan("system", 0, len(text), "system", "universal", "kernel")])
    assert "PROMPT_SURFACE_CONFLICT" in rules(audit_request(**data))
    data["system"] = "If voice, never use markdown. Otherwise always use markdown."
    data["spans"][0] = replace(data["spans"][0], end=len(data["system"]))
    assert "PROMPT_SURFACE_CONFLICT" not in rules(audit_request(**data))


def test_empty_request_is_not_clean_evidence():
    with pytest.raises(ValueError):
        audit_request(system="", tools={}, spans=[], active_owners=[],
                      required_owners={}, phase="cold")


def test_doctor_rejects_symlink_capture_without_leaking_content(tmp_path):
    from adkl.doctor_instructions import collect
    config = {"version": 1, "captures": [{"id": "cold", "phase": "cold", "source": "alias.json"}]}
    (tmp_path / "doctor-instructions.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "private.json").write_text(json.dumps(serializable(capture())), encoding="utf-8")
    (tmp_path / "alias.json").symlink_to(tmp_path / "private.json")
    with pytest.raises(ValueError):
        collect(tmp_path)


def test_explicit_byte_budgets_not_token_estimates():
    data = capture()
    result = audit_request(**data, max_system_bytes=10, max_tool_description_bytes=10)
    assert rules(result) == {"INSTRUCTION_SYSTEM_BUDGET", "INSTRUCTION_TOOL_BUDGET"}
    assert result["metrics"]["systemBytes"] == len(data["system"].encode())


@pytest.mark.parametrize("change", ["overlap", "outside", "owner", "surface", "budget", "missing_owners"])
def test_invalid_attribution_fails_instead_of_passing(change):
    data = capture()
    if change == "overlap":
        data["spans"].append(data["spans"][0])
    elif change == "outside":
        data["spans"][0] = replace(data["spans"][0], end=10**9)
    elif change == "owner":
        data["active_owners"] = ["customer email is not an owner label"]
    elif change == "surface":
        data["spans"][0] = replace(data["spans"][0], surface="not-present")
    elif change == "budget":
        data["max_system_bytes"] = True
    else:
        data["required_owners"]["search_supports"] = []
    with pytest.raises(ValueError):
        audit_request(**data)


def test_adapter_reads_outgoing_declarations_not_dispatch_only_denials():
    data = capture()
    request = SimpleNamespace(config=SimpleNamespace(system_instruction=data.pop("system"), tools=[
        SimpleNamespace(function_declarations=[SimpleNamespace(name=name, description=text)
            for name, text in data.pop("tools").items()])]), tools_dict={"stale_tool": object()})
    before = request.config.system_instruction
    assert inspect_request(request, **data)["passed"]
    assert request.config.system_instruction == before
    request.config.tools.append(request.config.tools[0])
    with pytest.raises(ValueError, match="Duplicate"):
        inspect_request(request, **data)


def test_cli_status_and_sanitized_invalid_capture(tmp_path, capsys):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(serializable(capture())), encoding="utf-8")
    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]
    path.write_text('{"secret": "private raw prompt"}', encoding="utf-8")
    assert main([str(path)]) == 2
    assert "private raw prompt" not in capsys.readouterr().out
    data = capture()
    data["active_owners"] = []
    path.write_text(json.dumps(serializable(data)), encoding="utf-8")
    assert main([str(path)]) == 1


def test_doctor_opt_in_and_changed_capture_never_claim_runtime_resolution(tmp_path):
    (tmp_path / "agent.yaml").write_text("version: 1\nagent:\n  name: demo\n  factory: agent:build\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("raise RuntimeError('do not import')", encoding="utf-8")
    with patch.object(execution, "Worker", side_effect=AssertionError("no host execution")):
        before = doctor.diagnose(tmp_path)
        assert next(c for c in before["checks"] if c["id"] == "instruction_layers")["status"] == "not_checked"
        config = {"version": 1, "captures": [{"id": "web-cold", "phase": "cold", "source": "capture.json"}]}
        (tmp_path / "doctor-instructions.json").write_text(json.dumps(config), encoding="utf-8")
        data = capture()
        data["active_owners"] = []
        path = tmp_path / "capture.json"
        path.write_text(json.dumps(serializable(data)), encoding="utf-8")
        baseline = doctor.diagnose(tmp_path)
        ids = {f["id"] for f in baseline["findings"] if f["check"] == "instruction_layers"}
        assert len(ids) == 2
        assert POLICY not in json.dumps(baseline)
        path.write_text(json.dumps(serializable(capture())), encoding="utf-8")
        after = doctor.diagnose(tmp_path, previous=baseline)
        assert ids <= set(after["comparison"]["notVerified"])
        path.unlink()
        missing = doctor.diagnose(tmp_path)
        assert next(c for c in missing["checks"] if c["id"] == "instruction_layers")["status"] == "error"
