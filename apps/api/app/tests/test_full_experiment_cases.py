"""TOOL_ONLY authoring-contract fixtures, never a delivered/frozen 50-family corpus."""

import json
from hashlib import sha256
from typing import Any

import pytest
from app.domain.full_experiment_cases import (
    CATEGORIES,
    FULL_METRIC_IDS,
    TRUTH_DIMENSIONS,
    CaseFamily,
    FullCaseDesign,
    inspect_full_case_design,
    original_json,
    topology_digest,
)
from pydantic import ValidationError


def family_data(identity: str = "family", count: int = 1, **changes: Any) -> dict[str, Any]:
    original: dict[str, Any] = {
        "protocol": "full-family-case-input-v1",
        "profile": "FULL",
        "purpose": "FULL_FAMILY_FROZEN",
        "family_id": identity,
        "initial_state": {"mode": "ISOLATED_FRESH_SEED", "seed_version": "TOOL_ONLY"},
        "steps": [
            {
                "step_id": f"s{i}",
                "kind": "SNAPSHOT",
                "at": "2026-10-06T00:00:00Z",
                "inputs": {"declared_test_amount": 100},
            }
            for i in range(count)
        ],
    }
    variants: list[dict[str, Any]] = []
    for index in range(2):
        body = json.loads(json.dumps(original))
        body["steps"][0]["inputs"]["declared_test_amount"] += index
        raw = json.dumps(body, sort_keys=True).encode("utf-8")
        variants.append(
            {
                "variant_id": f"v{index}",
                "native_input_utf8": raw.decode("utf-8"),
                "native_input_sha256": sha256(raw).hexdigest(),
                "native_schema_source_sha256": "a" * 64,
                # Synthetic contract row; no claim that a native executor was validated.
                "native_adapter_status": "PURE_SCHEMA_VALIDATED_NOT_RUN",
                "truth": {
                    name: {
                        "provenance": "DETERMINISTIC_AUTHOR_DESCRIPTION",
                        "status": "DEFINED",
                        "value": [],
                        "author_original_sha256": "b" * 64,
                    }
                    for name in TRUTH_DIMENSIONS
                },
            }
        )
    body = {
        "family_id": identity,
        "category": "NORMAL",
        "split": "FROZEN",
        "mechanism_topology_sha256": topology_digest(original),
        "author_design_original_sha256": "c" * 64,
        "variants": variants,
    }
    body.update(changes)
    return body


def design(families: list[dict[str, Any]]) -> FullCaseDesign:
    return FullCaseDesign.model_validate(
        {
            "families": families,
            "seed_original_sha256": "d" * 64,
            "source_inventory_sha256": "e" * 64,
            "independent_oracle_source_sha256": "f" * 64,
        }
    )


def change_original(row: dict[str, Any], **changes: Any) -> None:
    body = original_json(row["native_input_utf8"].encode("utf-8"))
    body.update(changes)
    raw = json.dumps(body, sort_keys=True).encode("utf-8")
    row["native_input_utf8"] = raw.decode("utf-8")
    row["native_input_sha256"] = sha256(raw).hexdigest()


def test_supplied_originals_counted_with_all_null_metrics_never_frozen() -> None:
    result = inspect_full_case_design(design([family_data()]))
    assert result.status == "AUTHORING_INCOMPLETE"
    assert result.actual_family_count == result.frozen_family_count == 1
    assert result.actual_variant_count == 2
    assert result.execution_status == "NOT_RUN" and result.reviewer_approval is None
    assert set(result.metrics) == set(FULL_METRIC_IDS) and len(result.metrics) == 28
    assert all(value is None for value in result.metrics.values())


def test_fifty_synthetic_contract_flows_only_ready_for_review_not_formal() -> None:
    # Different flow lengths exercise the contract gate. These are not financial cases or truth.
    categories = sorted(CATEGORIES)
    rows = [
        family_data(f"f{i}", i + 1, category=categories[i % len(categories)]) for i in range(50)
    ]
    result = inspect_full_case_design(design(rows))
    assert result.frozen_family_count == 50 and result.actual_variant_count == 100
    assert result.status == "READY_FOR_REVIEW_NOT_FROZEN"
    assert result.reviewer_approval is None and all(
        value is None for value in result.metrics.values()
    )
    assert inspect_full_case_design(design(rows[:-1])).status == "AUTHORING_INCOMPLETE"


def test_fifty_labels_with_the_same_mechanism_are_not_fifty_families() -> None:
    rows = [
        family_data(f"f{i}", category=sorted(CATEGORIES)[i % len(CATEGORIES)]) for i in range(50)
    ]
    assert inspect_full_case_design(design(rows)).status == "AUTHORING_INCOMPLETE"


def test_whole_family_numeric_variants_cannot_leak_across_split() -> None:
    left = family_data("left")
    right = family_data("right", split="DEVELOPMENT")
    for variant in right["variants"]:
        change_original(variant, purpose="DEVELOPMENT")
    with pytest.raises(ValidationError, match="leaked across splits"):
        design([left, right])
    with pytest.raises(ValidationError, match="whole family"):
        design([left, left])


def test_missing_native_or_truth_keeps_full_denominators_and_null() -> None:
    row = family_data()
    row["variants"][0]["native_adapter_status"] = "NOT_REGISTERED"
    row["variants"][1]["truth"]["expected_recovery"].update(status="MISSING", value=None)
    result = inspect_full_case_design(design([row]))
    assert result.actual_variant_count == 2
    assert result.unregistered_native_variants == ["family/v0"]
    assert result.missing_truth_units == ["family/v1/expected_recovery"]
    assert result.status == "AUTHORING_INCOMPLETE" and all(
        v is None for v in result.metrics.values()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "duplicate",
        "purpose",
        "profile",
        "empty",
        "truth",
        "topology",
        "llm_truth",
        "null_truth",
        "extra",
    ],
)
def test_bad_complete_originals_and_false_truth_refused(mutation: str) -> None:
    row = family_data()
    if mutation == "bytes":
        row["variants"][0]["native_input_utf8"] += " "
    elif mutation == "duplicate":
        row["variants"][1] = {**row["variants"][0], "variant_id": "v1"}
    elif mutation == "purpose":
        change_original(row["variants"][0], purpose="DEVELOPMENT")
    elif mutation == "profile":
        change_original(row["variants"][0], profile="MVP")
    elif mutation == "empty":
        change_original(row["variants"][0], steps=[])
    elif mutation == "truth":
        row["variants"][0]["truth"].pop("user_authority")
    elif mutation == "topology":
        row["mechanism_topology_sha256"] = "f" * 64
    elif mutation == "llm_truth":
        row["variants"][0]["truth"]["user_authority"]["provenance"] = "LLM_OUTPUT"
    elif mutation == "null_truth":
        row["variants"][0]["truth"]["user_authority"]["value"] = None
    else:
        row["success"] = True
    with pytest.raises((ValidationError, ValueError)):
        CaseFamily.model_validate(row)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]"])
def test_strict_original_json(raw: bytes) -> None:
    with pytest.raises(ValueError):
        original_json(raw)


def test_review_or_measured_status_cannot_be_injected() -> None:
    value = design([family_data()]).model_dump()
    with pytest.raises(ValidationError):
        FullCaseDesign.model_validate({**value, "review_status": "APPROVED"})
    with pytest.raises(ValidationError):
        FullCaseDesign.model_validate({**value, "status": "FROZEN"})
