"""Narrow original-number admission risks. All examples are TOOL_TEST_ONLY."""

import copy
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from scripts.full_experiment_claims import (
    ROOT,
    ExistingMvpClaim,
    OriginalFile,
    admit_existing_mvp_claim,
)
from scripts.mvp_observations import observe
from scripts.tests.test_mvp_observations import RUN, Fixture


def original(path: Path) -> OriginalFile:
    return OriginalFile(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.fixture
def claim() -> ExistingMvpClaim:
    # Retain fixtures under the repository inherited ACL; never touch real run records.
    root = ROOT / ".runtime" / "full-existing-claim-tool-fixtures" / uuid4().hex
    root.mkdir(parents=True)
    fixture = Fixture(root)
    result = observe(fixture.save())
    saved = root / "recorded-result.json"
    saved.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return ExistingMvpClaim(
        claim_id="tool-count-original-only",
        experiment_run_id=UUID(RUN),
        case_id="tool-case-only",
        arm_id="B0",
        metric_id="E2",
        metric_field="denominator",
        value=1,
        unit="synthetic_actor_events_per_case",
        run_manifest=original(root / "run.json"),
        recorded_result=original(saved),
        calculator_source=original(ROOT / "scripts/mvp_observations.py"),
        metric_definition=original(ROOT / "docs/spec/mvp-metrics.json"),
    )


def test_recompute_tool_number_never_promotes_to_effect(claim: ExistingMvpClaim) -> None:
    result = admit_existing_mvp_claim(claim)
    assert result.status == "RECOMPUTED_TOOL_NUMBER", result.missing_reason
    assert result.value == 1
    assert not result.permitted_in_mvp_scope
    assert not result.permitted_as_full_experiment_effect
    assert not result.financial_acceptance


@pytest.mark.parametrize("field,value", [("value", 2), ("unit", "ratio"), ("case_id", "another")])
def test_claim_identity_number_unit_not_declarations(
    claim: ExistingMvpClaim, field: str, value: str | int
) -> None:
    result = admit_existing_mvp_claim(claim.model_copy(update={field: value}))
    assert result.status == "MISSING"
    assert result.value is None


def test_rehashed_recorded_result_cannot_change_real_denominator(claim: ExistingMvpClaim) -> None:
    path = Path(claim.recorded_result.path)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["metrics"]["E2"]["denominator"] = 200
    path.write_text(json.dumps(result), encoding="utf-8")
    changed = claim.model_copy(update={"recorded_result": original(path), "value": 200})
    admitted = admit_existing_mvp_claim(changed)
    assert admitted.status == "MISSING"
    assert "independent recalculation" in str(admitted.missing_reason)


def test_boolean_json_cannot_equal_original_integer(claim: ExistingMvpClaim) -> None:
    path = Path(claim.recorded_result.path)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["metrics"]["E2"]["denominator"] = True
    path.write_text(json.dumps(result), encoding="utf-8")
    changed = claim.model_copy(update={"recorded_result": original(path)})
    assert admit_existing_mvp_claim(changed).status == "MISSING"


def test_missing_scalar_metrics_cannot_be_claimed(claim: ExistingMvpClaim) -> None:
    changed = claim.model_copy(
        update={"metric_field": "value"}  # E2 has two subseries, not a scalar.
    )
    assert admit_existing_mvp_claim(changed).status == "MISSING"


def test_source_substitution_cannot_choose_arbitrary_plugin(claim: ExistingMvpClaim) -> None:
    substituted = claim.model_copy(update={"calculator_source": claim.recorded_result})
    result = admit_existing_mvp_claim(substituted)
    assert result.status == "MISSING"
    assert "arbitrary calculator" in str(result.missing_reason)


def test_original_byte_drift_not_overlooked(claim: ExistingMvpClaim) -> None:
    path = Path(claim.run_manifest.path)
    path.write_bytes(path.read_bytes() + b" ")
    assert admit_existing_mvp_claim(claim).status == "MISSING"


def test_upgraded_full_flag_and_boolean_number_rejected(claim: ExistingMvpClaim) -> None:
    body = claim.model_dump(mode="python")
    for field, value in (("scope", "FULL"), ("value", True), ("arm_id", "B4")):
        changed = copy.deepcopy(body)
        changed[field] = value
        with pytest.raises(ValidationError):
            ExistingMvpClaim.model_validate(changed)


def test_relabelled_actual_mvp_fixture_does_not_prove_actual_run(claim: ExistingMvpClaim) -> None:
    path = Path(claim.run_manifest.path)
    run = json.loads(path.read_text(encoding="utf-8"))
    run["purpose"] = "MVP_FROZEN"
    run["execution_mode"] = "SERVICE_INTEGRATION"
    path.write_text(json.dumps(run), encoding="utf-8")
    result = admit_existing_mvp_claim(claim.model_copy(update={"run_manifest": original(path)}))
    assert result.status == "MISSING"
    assert not result.permitted_in_mvp_scope


def test_original_json_duplicate_refusal(claim: ExistingMvpClaim) -> None:
    path = Path(claim.recorded_result.path)
    path.write_bytes(b'{"metrics":{},"metrics":{}}')
    assert (
        admit_existing_mvp_claim(
            claim.model_copy(update={"recorded_result": original(path)})
        ).status
        == "MISSING"
    )
