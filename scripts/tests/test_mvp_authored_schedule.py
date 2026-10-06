"""Explicit conversion risks using retained author originals; never a 24-run result."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.mvp_authored_schedule import METHOD, ScheduleRefused, compile_case, load_compile, sha
from scripts.mvp_corpus_v2 import CorpusError, validate_authored_case_binding
from scripts.mvp_native_schema import SOURCES, NativeAdapter, canonical

ROOT = Path(__file__).resolve().parents[2]
AUTHORS = ROOT / ".runtime/W1-authored-24-schedule-draft-20261005T0920Z"
CHECKPOINT = AUTHORS / "final-author-checkpoint-20261005T095537Z-48e05b2d"
AUTHOR_MANIFEST_SHA = "6af98efadea59924fb70cf960e2db73fa1b353817f2517d67c7475f2924fe5e2"
TOOL_BINDINGS = {
    "seed_sha256": "a" * 64,
    "source_sha256": "b" * 64,
    "design_sha256": "c" * 64,
}


def author(identity: str = "N01") -> tuple[bytes, dict[str, str]]:
    manifest_raw = (CHECKPOINT / "manifest.json").read_bytes()
    assert sha(manifest_raw) == AUTHOR_MANIFEST_SHA
    proof = json.loads(manifest_raw)
    relative = "revision-3/inputs/" + identity + ".json"
    path = CHECKPOINT / "originals" / relative
    raw = path.read_bytes()
    assert sha(raw) == proof["originals"][relative]["sha256"]
    return raw, {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(raw)}


def test_all24_original_authored_inputs_bind_case_and_full_conditional_schedule() -> None:
    source_names = set(SOURCES) | {"scripts/mvp_native_schema.py", "scripts/mvp_corpus_v2.py"}
    inventory = {name: sha((ROOT / name).read_bytes()) for name in source_names}
    adapter = NativeAdapter(ROOT, inventory)
    cases = sorted((CHECKPOINT / "originals/revision-3/inputs").glob("*.json"))
    assert len(cases) == 24
    for path in cases:
        raw, ref = author(path.stem)
        original = json.loads(raw)
        case, schedule = compile_case(raw, ref, **TOOL_BINDINGS)
        assert case["case_id"] == original["case_id"] == path.stem
        assert case["data_origin"]["method"] == METHOD
        assert case["data_origin"]["complete_authored_input_ref"] == ref
        assert schedule["control_schedule"] == original["control_schedule"]
        assert schedule["arm_rule_author_inputs"] == original["arm_rule_author_inputs"]
        assert schedule["oracle_denominator_author_draft"] == original["oracle_registration_draft"]
        assert schedule["case_input_sha256"] == sha(canonical(case))
        assert schedule["financial_effect_evidence"] is False
        assert schedule["status"] == "REGISTERED_CONVERSION_NOT_FROZEN_NOT_EXECUTED"
        assert schedule["all_unattempted_opportunities_retained"] is True
        assert case["execution_input"]["initial_state"]["mode"] == "EXISTING"
        assert case["execution_input"]["steps"] == original["execution_input"]["steps"]
        checked = adapter.validate_execution(
            canonical(case["execution_input"]),
            "MVP_FROZEN",
            original_case_input_sha256=sha(canonical(case)),
        )
        assert checked["financial_effect_evidence"] is False
        assert checked["runtime_source_results_verified"] is False
        assert path.read_bytes() == raw
    adapter.unchanged()


@pytest.mark.parametrize(
    "mutation",
    [
        "development",
        "already_ran",
        "unconditional",
        "missing_control",
        "reordered_control",
        "unknown_role",
        "unknown_condition",
        "forward_dependency",
        "seed_replay",
        "foreign_family",
        "asserted_frozen_hash",
        "bad_seed_sha",
    ],
)
def test_conversion_refuses_protocol_control_and_initialization_drift(mutation: str) -> None:
    raw, ref = author()
    value = json.loads(raw)
    bindings = dict(TOOL_BINDINGS)
    if mutation == "development":
        value["intended_purpose"] = "DEVELOPMENT"
    elif mutation == "already_ran":
        value["status"] = "COMPLETE"
    elif mutation == "unconditional":
        value["control_schedule"]["direct_unconditional_scenario_run_allowed"] = True
    elif mutation == "missing_control":
        value["control_schedule"]["nodes"].pop()
    elif mutation == "reordered_control":
        value["control_schedule"]["nodes"].reverse()
    elif mutation == "unknown_role":
        value["control_schedule"]["nodes"][0]["role"] = "INSTALL_EXPECTED_SUCCESS"
    elif mutation == "unknown_condition":
        value["control_schedule"]["nodes"][0]["condition"] = "USE_OLD_P_OUTPUT"
    elif mutation == "forward_dependency":
        value["control_schedule"]["nodes"][0]["requires_original_success"] = ["N01_final"]
    elif mutation == "seed_replay":
        value["execution_input"]["initial_state"]["mode"] = "EXISTING"
    elif mutation == "foreign_family":
        value["execution_input"]["family_id"] = "ANOTHER"
    elif mutation == "asserted_frozen_hash":
        value["execution_input"]["frozen_case_sha256"] = "d" * 64
    else:
        bindings["seed_sha256"] = "not-a-hash"
    changed = canonical(value)
    with pytest.raises(ScheduleRefused):
        compile_case(changed, {**ref, "sha256": sha(changed)}, **bindings)


def test_t02_clarification_and_expected_rejection_remain_original_pending() -> None:
    raw, ref = author("T02")
    case, schedule = compile_case(raw, ref, **TOOL_BINDINGS)
    clarification = [
        node
        for node in schedule["control_schedule"]["nodes"]
        if node["role"] == "REGISTERED_SYNTHETIC_ACTOR_CLARIFICATION"
    ]
    assert len(clarification) == 1 and clarification[0]["not_nlu_result"] is True
    assert clarification[0]["actual_actor_log"] is None
    assert any(
        step["expected_error"] == {"code": "INVALID_SCENARIO_STEP", "status_code": 422}
        for step in case["execution_input"]["steps"]
    )
    before = copy.deepcopy(json.loads(raw))
    schedule["control_schedule"]["nodes"][0]["role"] = "changed-return-only"
    assert json.loads(raw) == before


def test_original_byte_sha_is_checked_before_parsing_and_no_file_is_rewritten() -> None:
    raw, ref = author()
    with pytest.raises(ScheduleRefused, match="BYTE_DRIFT"):
        compile_case(raw + b"\n", ref, **TOOL_BINDINGS)
    case, _ = load_compile(ROOT, ref, TOOL_BINDINGS)
    assert case["data_origin"]["complete_authored_input_ref"] == ref
    assert (ROOT / ref["path"]).read_bytes() == raw
    with pytest.raises(ScheduleRefused, match="OUTSIDE_ROOT"):
        load_compile(ROOT, {"path": "../outside.json", "sha256": "a" * 64}, TOOL_BINDINGS)


def test_all_false_execution_flags_are_preserved_and_never_create_outcomes() -> None:
    raw, ref = author("L01")
    case, schedule = compile_case(raw, ref, **TOOL_BINDINGS)
    values: dict[str, Any] = case["data_origin"]
    assert (
        values["financial_effect_evidence"] is False and values["human_research"] == "NOT_STARTED"
    )
    assert schedule["b3_recovery_confirmation"].endswith("NOT_IMPLEMENTED")
    assert "result" not in schedule and "results" not in case


@pytest.mark.parametrize(
    "mutation",
    ["none", "design_method", "missing_author", "source_missing", "source_drift", "case_drift"],
)
def test_original_design_graph_and_current_compiler_are_required(mutation: str) -> None:
    raw, ref = author()
    case, expected = compile_case(raw, ref, **TOOL_BINDINGS)
    compiler = "scripts/mvp_authored_schedule.py"
    paths = {compiler: sha((ROOT / compiler).read_bytes())}
    design: dict[str, Any] = {"schedule_method": METHOD, "additional_original_refs": [ref]}
    if mutation == "design_method":
        design["schedule_method"] = "UNREGISTERED"
    elif mutation == "missing_author":
        design["additional_original_refs"] = []
    elif mutation == "source_missing":
        paths.clear()
    elif mutation == "source_drift":
        paths[compiler] = "e" * 64
    elif mutation == "case_drift":
        case["execution_input"]["steps"][0]["inputs"] = {"invented": "success"}

    def load(descriptor: dict[str, str], *, parse: bool) -> bytes:
        assert descriptor == ref and parse is False
        return raw

    def verify() -> dict[str, Any] | None:
        return validate_authored_case_binding(
            case,
            design,
            load_ref=load,
            source_root=ROOT,
            source_paths=paths,
            global_refs={
                name + "_ref": {"sha256": TOOL_BINDINGS[name + "_sha256"]}
                for name in ("seed", "source", "design")
            },
        )

    if mutation == "none":
        assert verify() == expected
    else:
        with pytest.raises(CorpusError):
            verify()


def test_loaded_compiler_drift_is_rejected_before_reading_author_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import mvp_authored_schedule as authored

    raw, ref = author()
    case, _ = compile_case(raw, ref, **TOOL_BINDINGS)
    monkeypatch.setattr(authored, "compile_case", lambda *args, **kwargs: (case, {}))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Changed loaded compiler must be refused before original graph load")

    compiler = "scripts/mvp_authored_schedule.py"
    with pytest.raises(CorpusError, match="Loaded"):
        validate_authored_case_binding(
            case,
            {"schedule_method": METHOD, "additional_original_refs": [ref]},
            load_ref=forbidden,
            source_root=ROOT,
            source_paths={compiler: sha((ROOT / compiler).read_bytes())},
            global_refs={},
        )
