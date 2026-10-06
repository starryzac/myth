"""TOOL_ONLY authored negative DTO contract; no DB or observed financial effects."""

import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "scripts"
from scripts import mvp_native_schema as NATIVE  # noqa: E402


@pytest.fixture
def native() -> Any:
    paths = set(NATIVE.SOURCES) | {
        p.relative_to(ROOT).as_posix()
        for p in (HERE / "mvp_native_schema.py", HERE / "mvp_corpus_v2.py")
    }
    inventory = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    return NATIVE.NativeAdapter(ROOT, inventory)


def scenario(*, missing: bool = False) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "kind": "transfer_internal",
        "source_account_id": str(UUID(int=1)),
        "destination_account_id": None,
        "amount_cents": 47143,
    }
    if missing:
        del intent["destination_account_id"]
    return {
        "scenario_id": "TOOL_ONLY_negative_transfer",
        "purpose": "DEVELOPMENT",
        "dataset_id": "TOOL_ONLY_contract",
        "family_id": "TOOL_ONLY_ambiguity",
        "initial_state": {"mode": "EXISTING"},
        "steps": [
            {
                "step_id": "ambiguous",
                "kind": "PREPARE_ACTION",
                "at": "2026-10-05T00:00:00Z",
                "inputs": {"idempotency_key": "TOOL_ONLY_ambiguous", "intent": intent},
                "expected_error": {"code": "INVALID_SCENARIO_STEP", "status_code": 422},
            }
        ],
    }


@pytest.mark.parametrize("missing", [False, True])
def test_exact_missing_destination_is_pending_original_rejection(
    native: Any, missing: bool
) -> None:
    data = scenario(missing=missing)
    proof = native.validate_execution(NATIVE.canonical(data), "DEVELOPMENT")
    assert proof["status"] == "EXPECTED_DTO_REJECTION_PENDING"
    assert proof["validated_execution_input"]["steps"][0]["inputs"] == data["steps"][0]["inputs"]
    assert proof["expected_dto_rejections"] == [
        {
            "step_id": "ambiguous",
            "kind": "PREPARE_ACTION",
            "field": "intent.destination_account_id",
            "expected_error": {"code": "INVALID_SCENARIO_STEP", "status_code": 422},
            "runtime_rejection_observed": False,
        }
    ]
    assert proof["runtime_source_results_verified"] is False
    assert proof["financial_effect_evidence"] is False
    assert proof["literal_dto_steps"] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["steps"][0].pop("expected_error"),
        lambda d: d["steps"][0]["expected_error"].update(code="NOT_IMPLEMENTED"),
        lambda d: d["steps"][0]["expected_error"].update(status_code=409),
        lambda d: d["steps"][0]["inputs"]["intent"].update(amount_cents=True),
        lambda d: d["steps"][0]["inputs"]["intent"].update(amount_cents=-1),
        lambda d: d["steps"][0]["inputs"]["intent"].update(grant=True),
        lambda d: d["steps"][0]["inputs"].update(accepted=True),
        lambda d: d["steps"][0]["inputs"].update(idempotency_key=" "),
        lambda d: d["steps"][0]["inputs"]["intent"].update(kind="allocate_goal"),
        lambda d: d["steps"][0]["inputs"]["intent"].update(
            source_account_id={"$ref": {"step_id": "future", "pointer": "/result/id"}}
        ),
    ],
)
def test_expected_rejection_does_not_relax_other_native_boundaries(
    native: Any, mutate: Any
) -> None:
    data = scenario()
    mutate(data)
    with pytest.raises(ValueError):
        native.validate_execution(NATIVE.canonical(data), "DEVELOPMENT")


def test_valid_transfer_does_not_receive_negative_input_label(native: Any) -> None:
    data = scenario()
    data["steps"][0]["inputs"]["intent"]["destination_account_id"] = str(UUID(int=2))
    proof = native.validate_execution(NATIVE.canonical(data), "DEVELOPMENT")
    assert proof["status"] == "NATIVE_SCHEMA_VALID"
    assert proof["expected_dto_rejections"] == []
    assert proof["literal_dto_steps"] == 1


def test_original_backward_source_reference_remains_unresolved(native: Any) -> None:
    data = scenario()
    data["steps"].insert(
        0,
        {
            "step_id": "source",
            "kind": "LOOKUP_ACCOUNT",
            "at": "2026-10-05T00:00:00Z",
            "inputs": {"external_ref": "TOOL_ONLY_cash"},
        },
    )
    reference = {"$ref": {"step_id": "source", "pointer": "/result/account/id"}}
    data["steps"][1]["inputs"]["intent"]["source_account_id"] = reference
    proof = native.validate_execution(NATIVE.canonical(data), "DEVELOPMENT")
    assert proof["status"] == "EXPECTED_DTO_REJECTION_PENDING"
    assert proof["unresolved_original_references"] == 1
    assert (
        proof["validated_execution_input"]["steps"][1]["inputs"]["intent"]["source_account_id"]
        == reference
    )
