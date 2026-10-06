"""Synthetic original-shape projection risks; actual SQL is a separate node."""

from datetime import timedelta
from typing import Any

import pytest
from app.domain.full_future_dated_history import REFERENCE_KIND
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.policy_configuration import configuration_hash
from app.tests.test_full_future_dated_history import NOW, current_source, originals, seal
from app.tests.test_full_protection_projection import data, dated, source


def inputs(row: FullProtectionPolicySource) -> FullProtectionProjectionInput:
    result = data(row, cash=100000)
    return result.model_copy(update={"snapshot": result.snapshot.model_copy(update={"as_of": NOW})})


def test_complete_future_chain_selects_new_algorithm_and_retains_original_cash() -> None:
    proof = originals()
    row = current_source(proof)
    baseline = project_full_protection(inputs(row))
    row = row.model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ]
        }
    )
    request = inputs(row)
    raw = request.model_dump(mode="json")
    result = project_full_protection(request)
    curve = result.full_annual_projection
    assert baseline.status == "UNKNOWN" and baseline.full_annual_projection is None
    assert curve is not None and result.status == "READY"
    assert (
        result.algorithm_version
        == curve.algorithm_version
        == "registered-full-protection-future-dated-history-v2"
    )
    assert curve.safe_idle_cents == 100000 - 33305
    assert len(result.occurrences) == 1 and result.occurrences[0].original_paid_cents is None
    assert result.historical_full_settlement_complete is False and result.bank_authority is False
    assert result.original_execution_view == baseline.original_execution_view
    assert result.original_annual_projection == baseline.original_annual_projection
    assert request.model_dump(mode="json") == raw
    assert curve.boundary_hash == configuration_hash(
        {
            "algorithm": result.algorithm_version,
            "original_annual_hash": result.original_annual_projection.boundary_hash,
            "input": raw,
        }
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "clock",
        "missing",
        "duplicate",
        "denominator",
        "old_amount",
        "current_evidence",
        "wrapper_extra",
    ],
)
def test_invalid_future_history_never_relaxes_current_unknown_gate(mutation: str) -> None:
    proof = originals()
    row = current_source(proof)
    if mutation == "clock":
        proof = seal(proof.model_copy(update={"as_of": NOW - timedelta(seconds=1)}))
    elif mutation == "denominator":
        proof = seal(proof.model_copy(update={"actual_version_count": 3}))
    elif mutation == "old_amount":
        proof.versions[0]["configuration"]["amount"]["max_cents"] += 1
        proof = seal(proof)
    elif mutation == "current_evidence":
        row = row.model_copy(update={"evidence_ids": []})
    refs: list[dict[str, Any]] = [{"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}]
    if mutation == "missing":
        refs = []
    elif mutation == "duplicate":
        refs *= 2
    elif mutation == "wrapper_extra":
        refs[0]["grants_authority"] = False
    result = project_full_protection(inputs(row.model_copy(update={"reference_snapshots": refs})))
    assert result.status == "UNKNOWN" and result.full_annual_projection is None
    assert result.algorithm_version == "registered-full-protection-v1"
    assert "PRIOR_FULL_VERSION_UNPAID_HISTORY_NOT_PROVEN" in result.policy_states[0].reasons


def test_plain_original_inputs_keep_v1_algorithm_and_hash_identity() -> None:
    request = data(source("DatedExpensePolicy", dated()))
    result = project_full_protection(request)
    curve = result.full_annual_projection
    assert (
        curve is not None
        and result.algorithm_version == curve.algorithm_version == "registered-full-protection-v1"
    )
    assert curve.boundary_hash == configuration_hash(
        {
            "algorithm": "registered-full-protection-v1",
            "original_annual_hash": result.original_annual_projection.boundary_hash,
            "input": request.model_dump(mode="json"),
        }
    )
