"""Synthetic v4 bindings only: no actual adoption, bank, PG, or expiry acceptance."""

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest
from app.domain.full_seasonal_current_protection import derive_current_seasonal_protection_bindings
from app.domain.full_seasonal_ended_adoption import REFERENCE_KIND, derive_ended_seasonal_adoption
from app.domain.full_seasonal_protection import derive_seasonal_protection_bindings
from app.tests.test_full_seasonal_adoption import USER, proof_fixture
from app.tests.test_full_seasonal_ended_adoption import fixture


def test_verified_ended_original_releases_only_floor_retains_all_evidence_and_original_bytes() -> (
    None
):
    inputs = fixture()
    proof = derive_ended_seasonal_adoption(inputs)
    source, _ = proof_fixture()
    source = source.model_copy(
        update={
            "reference_snapshots": [
                {
                    "kind": REFERENCE_KIND,
                    "proof": proof.model_dump(mode="json"),
                }
            ]
        }
    )
    original = source.model_dump_json()
    result = derive_current_seasonal_protection_bindings(
        [source], inputs.as_of, inputs.timezone, expected_user_id=USER
    )
    assert result.requested and not result.reasons
    assert result.ended_proofs == (proof,) and not result.active.proofs
    assert result.amount_on(inputs.as_of.date()) == 0
    assert set(result.evidence()) == {
        UUID(raw["id"]) for record in inputs.records for raw in record.current_evidence_originals
    }
    assert source.model_dump_json() == original
    # Old v3 intentionally ignores the new wrapper and its semantics stay unchanged.
    assert not derive_seasonal_protection_bindings(
        [source], inputs.as_of, inputs.timezone
    ).requested


@pytest.mark.parametrize(
    "change", ["owner", "amount", "missing_row", "count", "hash", "end", "extra", "duplicate"]
)
def test_any_unproven_ended_wrapper_is_unknown_not_zero_floor_success(change: str) -> None:
    inputs = fixture()
    proof = derive_ended_seasonal_adoption(inputs)
    source, _ = proof_fixture()
    raw: dict[str, Any] = deepcopy(proof.model_dump(mode="json"))
    if change == "owner":
        raw["user_id"] = str(UUID(int=99))
    elif change == "amount":
        raw["original_adopted_cents"] = 0
    elif change == "missing_row":
        raw["inputs"]["records"] = []
    elif change == "count":
        raw["registered_adoption_evidence_count"] = 0
    elif change == "hash":
        raw["proof_hash"] = "0" * 64
    elif change == "end":
        raw["protection_end"] = "1900-01-01"
    wrapper: dict[str, Any] = {"kind": REFERENCE_KIND, "proof": raw}
    if change == "extra":
        wrapper["cash_credit_cents"] = 1
    wrappers = [wrapper, wrapper] if change == "duplicate" else [wrapper]
    source = source.model_copy(update={"reference_snapshots": wrappers})
    value = derive_current_seasonal_protection_bindings(
        [source], inputs.as_of, inputs.timezone, expected_user_id=USER
    )
    assert value.requested and value.reasons and not value.ended_proofs


def test_duplicate_policy_sources_are_not_a_reduced_successful_denominator() -> None:
    inputs = fixture()
    source, _ = proof_fixture()
    proof = derive_ended_seasonal_adoption(inputs)
    source = source.model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ]
        }
    )
    value = derive_current_seasonal_protection_bindings(
        [source, source], inputs.as_of, inputs.timezone
    )
    assert "SEASONAL_CURRENT_POLICY_BINDING_DUPLICATE" in value.reasons
