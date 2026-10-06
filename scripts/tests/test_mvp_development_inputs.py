"""TOOL_ONLY source/normalization risks; no financial execution or expected success."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts import mvp_development_inputs as tool
from scripts.mvp_native_schema import SOURCES, NativeAdapter, canonical

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tool_temp() -> Path:
    path = ROOT / ".runtime/W1-development-input-tool-fixtures" / ("TOOL_ONLY-" + uuid4().hex)
    path.mkdir(parents=True)
    return path


def case(event: str = "SALARY_RECEIVED") -> dict[str, Any]:
    return tool.build_request(event, seed_sha256="1" * 64, source_sha256="2" * 64)


@pytest.fixture(scope="module")
def native() -> NativeAdapter:
    files = set(SOURCES) | {"scripts/mvp_native_schema.py", "scripts/mvp_corpus_v2.py"}
    return NativeAdapter(ROOT, {name: tool.digest((ROOT / name).read_bytes()) for name in files})


@pytest.mark.parametrize("event", tool.EVENTS)
def test_each_whole_public_input_is_native_symbolic_request_not_runtime_evidence(
    event: str, native: NativeAdapter
) -> None:
    original = case(event)
    execution = original["execution_input"]
    result = native.validate_execution(canonical(execution), "DEVELOPMENT")
    assert result["runtime_source_results_verified"] is False
    assert result["financial_effect_evidence"] is False
    assert result["unresolved_original_references"] > 0
    assert execution["initial_state"] == {
        "mode": "SEED_NEW",
        "expected_epoch_id": None,
        "seed_version": "mvp-301-v6",
    }
    assert original["intended_purpose"] == "DEVELOPMENT"
    assert original["data_origin"]["runtime_execution"] == "NOT_RUN"
    assert original["data_origin"]["captured_historical_input"] is False
    assert execution["steps"][0]["kind"] == execution["steps"][-1]["kind"] == "SNAPSHOT"
    assert len(execution["steps"]) > 2
    assert any(step["kind"] == "DEMO_EVENT" for step in execution["steps"])
    assert not any(step["kind"] == "CONFIRM_ACTION" for step in execution["steps"])
    assert "frozen_case_sha256" not in execution


def test_actual_six_sources_and_preconditions_are_read_without_imported_services() -> None:
    raw = (ROOT / "apps/api/app/services/demo_console.py").read_bytes()
    result = tool.source_contract(raw)
    assert result["events"] == list(tool.EVENTS)
    assert result["requirements"] == tool.REQUIREMENTS
    assert result["original_functions"]["run_demo_event"]["line"] > 0


def test_salary_preconditions_original_approvals_and_goal_precede_bank_request() -> None:
    steps = case()["execution_input"]["steps"]
    assert [(step["kind"], step["inputs"].get("kind")) for step in steps[:5]] == [
        ("SNAPSHOT", None),
        ("PREPARE_TEMPLATE", "CAR_GOAL"),
        ("CONFIRM_TEMPLATE", None),
        ("PREPARE_TEMPLATE", "LIQUID_ASSET"),
        ("CONFIRM_TEMPLATE", None),
    ]
    assert steps[5]["inputs"]["event_kind"] == "CREATE_CAR_GOAL"
    assert steps[6]["inputs"]["event_kind"] == "SALARY_RECEIVED"
    assert steps[1]["inputs"]["expected_epoch_id"] == tool.reference(
        "seed-original", "/result/tables/audit_epochs/0/id"
    )
    assert steps[2]["inputs"] == {
        "proposal_id": tool.reference("prepare-car-goal", "/result/proposal_id"),
        "reviewed_hash": tool.reference("prepare-car-goal", "/result/configuration_hash"),
        "accepted": True,
    }


def test_fixed_request_retains_conditional_actor_gap_and_real_all_prerequisites() -> None:
    original = case("FIXED_EARLY_WITHDRAWAL")
    events = [
        step["inputs"]["event_kind"]
        for step in original["execution_input"]["steps"]
        if step["kind"] == "DEMO_EVENT"
    ]
    assert events == [
        "CREATE_CAR_GOAL",
        "SALARY_RECEIVED",
        "LARGE_CONSUMPTION",
        "AUTO_REDEEM",
        "FIXED_EARLY_WITHDRAWAL",
    ]
    assert original["data_origin"]["business_completion"] == "UNVERIFIED"
    assert (
        original["data_origin"]["continuation_boundary"]
        == "BLOCKED_LINEAR_SCENARIO_HAS_NO_CONDITIONAL_ASK_ACTION_SELECTOR"
    )


def test_rent_change_uses_original_full_change_result_and_separate_exact_user_consent() -> None:
    steps = case("CHANGE_RENT")["execution_input"]["steps"]
    mutation = next(step for step in steps if step["kind"] == "CHANGE_POLICY")
    assert mutation["inputs"]["accepted"] is True
    for key in {
        "configuration",
        "reviewed_hash",
        "policy_id",
        "expected_version_id",
        "reason",
        "idempotency_key",
    }:
        assert mutation["inputs"][key] == tool.reference(
            "rent-original-change", "/result/policy_change/" + key
        )
    assert [step["inputs"]["event_kind"] for step in steps if step["kind"] == "DEMO_EVENT"] == [
        "CHANGE_RENT",
        "CHANGE_RENT",
    ]


def test_renamed_formal_whole_request_fails_original_strict_novelty_without_ignoring_topology() -> (
    None
):
    original = case()
    renamed = copy.deepcopy(original)
    renamed["case_id"] = "TOOL_ONLY-formal-rename"
    renamed["execution_input"]["scenario_id"] = "TOOL_ONLY-formal-rename"
    renamed["execution_input"]["dataset_id"] = "TOOL_ONLY-another-label"
    renamed["execution_input"]["purpose"] = "MVP_FROZEN"
    formal = [{"case_id": "TOOL_ONLY-F1", "variants": {"ORIGINAL_AUTHORED_FULL_SCENARIO": renamed}}]
    result = tool.compare_requests([original], formal, [])
    assert result["status"] == "FAILED_WHOLE_REQUEST_ISOMORPHISM"
    assert result["whole_flow_conflicts"] == [
        {
            "case_id": "TOOL_ONLY-F1",
            "variant": "ORIGINAL_AUTHORED_FULL_SCENARIO",
            "development_case_id": original["case_id"],
        }
    ]
    assert result["complete_workspace_novelty"] == "MISSING_COMPLETE_LEGACY_AND_ACL_INPUTS"


def test_money_dates_and_titles_alone_do_not_create_novelty() -> None:
    original = case("CHANGE_RENT")
    changed = copy.deepcopy(original)
    for step in changed["execution_input"]["steps"]:
        step["at"] = "2027-01-01T00:00:00+00:00"
    original["execution_input"]["steps"][0]["inputs"] = {"amount_cents": 123, "title": "A"}
    changed["execution_input"]["steps"][0]["inputs"] = {"amount_cents": 456, "title": "B"}
    result = tool.compare_requests(
        [original], [{"case_id": "TOOL_ONLY-F2", "variants": {"ORIGINAL": changed}}], []
    )
    assert result["status"] == "FAILED_WHOLE_REQUEST_ISOMORPHISM"


def test_original_request_topology_change_remains_different_under_original_fingerprint() -> None:
    result = tool.compare_requests(
        [case("CREATE_CAR_GOAL")],
        [{"case_id": "TOOL_ONLY-F3", "variants": {"ORIGINAL": case("SALARY_RECEIVED")}}],
        [],
    )
    assert result["status"] == "NO_WHOLE_REQUEST_MATCH"
    assert result["formal_frozen_acceptance"] == "NOT_RUN"


def test_both_seed_modes_compared_not_removed_to_manufacture_originality() -> None:
    original = case()
    converted = copy.deepcopy(original)
    converted["execution_input"]["initial_state"]["mode"] = "EXISTING"
    result = tool.compare_requests(
        [original],
        [
            {
                "case_id": "TOOL_ONLY-F4",
                "variants": {
                    "ORIGINAL_AUTHORED_FULL_SCENARIO": original,
                    "COMPILED_FULL_SCENARIO": converted,
                },
            }
        ],
        [],
    )
    assert len(result["formal_variants"]) == 2
    assert len(result["whole_flow_conflicts"]) == 1


def test_partial_local_patterns_are_diagnostic_and_cannot_close_missing_full_flow() -> None:
    request = {
        "idempotency_key": "local",
        "intent": {"kind": "purchase_asset", "policy_id": "11111111-1111-4111-8111-111111111111"},
    }
    formal = case()
    formal["execution_input"]["steps"].insert(
        1, {"step_id": "purchase", "kind": "PREPARE_ACTION", "inputs": request}
    )
    legacy = [
        {
            "registry_path": "TOOL_ONLY-local.json",
            "opportunity_index": 0,
            "flow_signature": tool.local_request_pattern(request),
        }
    ]
    result = tool.compare_requests(
        [case("CREATE_CAR_GOAL")],
        [{"case_id": "TOOL_ONLY-F5", "variants": {"ORIGINAL": formal}}],
        legacy,
    )
    assert len(result["legacy_local_request_overlaps"]) == 1
    assert (
        result["legacy_local_pattern_scope"]
        == "DIAGNOSTIC_ONLY_NOT_COMPLETE_SCENARIO_OR_NOVELTY_PASS"
    )
    assert result["whole_flow_conflicts"] == []


def test_empty_or_single_snapshot_is_never_built_as_whole_public_input() -> None:
    for event in tool.EVENTS:
        steps = case(event)["execution_input"]["steps"]
        assert len(steps) >= 5
        assert sum(step["kind"] == "SNAPSHOT" for step in steps) == 2


def test_unknown_public_event_rejected() -> None:
    with pytest.raises(tool.DevelopmentError, match="Unsupported"):
        case("RESET_FORMAL_HISTORY")


def test_mutated_source_event_contract_is_refused() -> None:
    raw = (ROOT / "apps/api/app/services/demo_console.py").read_bytes()
    with pytest.raises(tool.DevelopmentError, match="events changed"):
        tool.source_contract(raw.replace(b'    "CHANGE_RENT",', b'    "FAKE_EVENT",', 1))


def test_mutated_actual_template_preconditions_refused() -> None:
    raw = (ROOT / "apps/api/app/services/demo_console.py").read_bytes()
    with pytest.raises(tool.DevelopmentError, match="preconditions changed"):
        tool.source_contract(
            raw.replace(b'"CREATE_CAR_GOAL": ["CAR_GOAL"]', b'"CREATE_CAR_GOAL": []')
        )


def test_parent_traversal_and_outside_original_refused(tool_temp: Path) -> None:
    root = tool_temp / "TOOL_ONLY-root"
    root.mkdir()
    for value in ("../outside.json", str(tool_temp / "outside.json")):
        with pytest.raises(tool.DevelopmentError):
            tool.owned(root, value)


def test_capture_preserves_original_whole_bytes_and_refuses_drift_and_overwrite(
    tool_temp: Path,
) -> None:
    root = tool_temp / "TOOL_ONLY-root"
    root.mkdir()
    original = root / "raw.json"
    raw = b'{ "whole": [1,2], "status": "ORIGINAL_FAILED" }\n'
    original.write_bytes(raw)
    output = root / ".runtime/TOOL_ONLY"
    capture = tool.Capture(root, output)
    assert capture.read(original, tool.digest(raw)) == raw
    archive = Path(capture.archives[original]["archive"]["path"])
    assert archive.read_bytes() == raw
    with pytest.raises(tool.DevelopmentError, match="already exists"):
        tool.Capture(root, output)
    original.write_bytes(b"{}")
    with pytest.raises(tool.DevelopmentError, match="changed"):
        capture.unchanged()
    assert archive.read_bytes() == raw


def test_capture_hash_mismatch_refused_without_relabelling_success(tool_temp: Path) -> None:
    root = tool_temp / "TOOL_ONLY-root"
    root.mkdir()
    original = root / "raw.json"
    original.write_bytes(b"{}")
    capture = tool.Capture(root, root / ".runtime/TOOL_ONLY")
    with pytest.raises(tool.DevelopmentError, match="SHA mismatch"):
        capture.read(original, "0" * 64)


def test_no_output_cli_connects_to_nothing_or_writes_definition(capsys: Any) -> None:
    assert tool.main([]) == 0
    assert "PREPARED_NOT_WRITTEN" in capsys.readouterr().out


def test_source_derived_envelope_has_no_output_or_execution_binding() -> None:
    for event in tool.EVENTS:
        original = case(event)
        assert set(original) == {
            "protocol",
            "case_id",
            "family_id",
            "intended_purpose",
            "seed_version",
            "seed_sha256",
            "source_sha256",
            "execution_input",
            "data_origin",
        }
        assert "result" not in original and "receipt" not in original
        assert original["data_origin"]["financial_effect_evidence"] is False
