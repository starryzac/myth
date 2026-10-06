"""Synthetic original-adoption math only; no bank, PostgreSQL or human study evidence."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest
from app.domain.full_protection_projection import FullProtectionPolicySource
from app.domain.full_seasonal_adoption import REFERENCE_KIND, SeasonalAdoptionProof
from app.domain.full_seasonal_protection import derive_seasonal_protection_bindings
from app.tests.test_full_seasonal_adoption import proof_fixture


def bound_source() -> tuple[FullProtectionPolicySource, SeasonalAdoptionProof]:
    source, proof = proof_fixture()
    source = source.model_copy(
        update={
            "reference_snapshots": [
                {"kind": REFERENCE_KIND, "proof": proof.model_dump(mode="json")}
            ]
        }
    )
    return source, proof


def test_original_explicit_adoption_is_floor_not_payment_and_releases_after_end() -> None:
    source, proof = bound_source()
    original = source.model_dump_json()
    bindings = derive_seasonal_protection_bindings([source], proof.as_of, "Asia/Shanghai")
    assert bindings.requested and not bindings.reasons
    assert proof.original is not None and proof.evidence_id is not None
    assert bindings.amount_on(proof.original.scope.protection_start) == 1800
    assert bindings.amount_on(proof.original.scope.protection_end) == 1800
    assert bindings.amount_on(proof.original.scope.protection_end + timedelta(days=1)) == 0
    assert bindings.evidence()[proof.evidence_id] == proof.evidence_hash
    assert source.model_dump_json() == original


def test_unadopted_statistical_suggestion_does_not_create_floor_or_proof() -> None:
    source, proof = proof_fixture()
    bindings = derive_seasonal_protection_bindings([source], proof.as_of, "Asia/Shanghai")
    assert not bindings.requested and bindings.proofs == () and not bindings.reasons


@pytest.mark.parametrize(
    "change", ["unknown", "owner", "hash", "amount", "body", "wrapper", "duplicate", "clock"]
)
def test_missing_unknown_or_changed_originals_are_refused(change: str) -> None:
    source, proof = bound_source()
    refs = deepcopy(source.reference_snapshots)
    raw = refs[0]["proof"]
    if change == "unknown":
        raw["status"] = "UNKNOWN"
    elif change == "owner":
        raw["user_id"] = str(UUID(int=9999))
    elif change == "hash":
        raw["evidence_hash"] = "0" * 64
    elif change == "amount":
        raw["original"]["scope"]["adopted_adjustment_cents"] += 1
    elif change == "body":
        raw["original"]["original_request"]["accepted"] = False
    elif change == "wrapper":
        refs[0]["amount_cents"] = 1800
    elif change == "duplicate":
        refs.append(deepcopy(refs[0]))
    elif change == "clock":
        raw["as_of"] = (proof.as_of + timedelta(seconds=1)).isoformat()
    source = source.model_copy(update={"reference_snapshots": refs})
    bindings = derive_seasonal_protection_bindings([source], proof.as_of, "Asia/Shanghai")
    assert bindings.requested and bindings.reasons


def test_same_original_cannot_double_count_a_holiday_under_two_sources() -> None:
    source, proof = bound_source()
    bindings = derive_seasonal_protection_bindings([source, source], proof.as_of, "Asia/Shanghai")
    assert "SEASONAL_OFFICIAL_WINDOWS_OVERLAP" in bindings.reasons


def test_execution_consumer_owner_is_required_when_supplied() -> None:
    source, proof = bound_source()
    bindings = derive_seasonal_protection_bindings(
        [source], proof.as_of, "Asia/Shanghai", expected_user_id=UUID(int=9999)
    )
    assert bindings.reasons and bindings.proofs == ()
