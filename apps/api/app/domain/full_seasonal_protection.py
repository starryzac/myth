"""New seasonal floor bindings; original JSON, policy grants and cash stay intact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.full_seasonal_adoption import (
    REFERENCE_KIND,
    SeasonalAdoptionProof,
    verify_seasonal_adoption,
)

if TYPE_CHECKING:
    from app.domain.full_protection_projection import FullProtectionPolicySource

ALGORITHM = "registered-full-protection-adopted-seasonal-v3"
FLOOR = "full_seasonal_adopted"


@dataclass(frozen=True)
class SeasonalProtectionBindings:
    requested: bool
    proofs: tuple[SeasonalAdoptionProof, ...]
    reasons: tuple[str, ...]

    def amount_on(self, day: date) -> int:
        # Adoption reserves current cash immediately. The official end releases
        # a floor; it is never a payment or a reduction in the cash balance.
        return sum(
            proof.original.scope.adopted_adjustment_cents
            for proof in self.proofs
            if proof.original is not None and day <= proof.original.scope.protection_end
        )

    def evidence(self) -> dict[UUID, str]:
        values: dict[UUID, str] = {}
        for proof in self.proofs:
            assert proof.current_scope is not None
            assert proof.evidence_id is not None and proof.evidence_hash is not None
            originals = {
                UUID(row["id"]): row["content_hash"]
                for row in proof.current_scope.source_evidence_originals
            }
            originals[proof.evidence_id] = proof.evidence_hash
            for identity, digest in originals.items():
                if identity in values and values[identity] != digest:
                    raise ValueError("SEASONAL_CURRENT_SOURCE_HASH_CONFLICT")
                values[identity] = digest
        return values


def derive_seasonal_protection_bindings(
    sources: list[FullProtectionPolicySource],
    as_of: datetime,
    timezone: str,
    *,
    expected_user_id: UUID | None = None,
) -> SeasonalProtectionBindings:
    proofs: list[SeasonalAdoptionProof] = []
    reasons: list[str] = []
    requested = False
    for source in sources:
        wrappers = [row for row in source.reference_snapshots if row.get("kind") == REFERENCE_KIND]
        if not wrappers:
            continue
        requested = True
        try:
            if len(wrappers) != 1 or set(wrappers[0]) != {"kind", "proof"}:
                raise ValueError("SEASONAL_ORIGINAL_PROOF_WRAPPER_NOT_EXACT")
            proof = SeasonalAdoptionProof.model_validate_json(json.dumps(wrappers[0]["proof"]))
            if not verify_seasonal_adoption(source, proof, as_of, timezone):
                raise ValueError("SEASONAL_CURRENT_ADOPTION_NOT_VERIFIED")
            if expected_user_id is not None and proof.user_id != expected_user_id:
                raise ValueError("SEASONAL_CURRENT_ADOPTION_OWNER_DIFFERS")
            proofs.append(proof)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reasons.append(f"{source.policy_id}:{error}")
    if proofs:
        first = proofs[0]
        for proof in proofs:
            if (
                proof.user_id != first.user_id
                or proof.epoch_id != first.epoch_id
                or set(proof.retained_command_ids) != set(first.retained_command_ids)
                or proof.actual_adoption_count != first.actual_adoption_count
            ):
                reasons.append("SEASONAL_COMPLETE_ORIGINAL_COMMAND_DENOMINATOR_DIFFERS")
        for index, left in enumerate(proofs):
            assert left.original is not None
            for right in proofs[index + 1 :]:
                assert right.original is not None
                if (
                    left.original.scope.official_start <= right.original.scope.official_end
                    and right.original.scope.official_start <= left.original.scope.official_end
                ):
                    reasons.append("SEASONAL_OFFICIAL_WINDOWS_OVERLAP")
    bound = SeasonalProtectionBindings(requested, tuple(proofs), tuple(sorted(set(reasons))))
    try:
        bound.evidence()
    except ValueError as error:
        return SeasonalProtectionBindings(requested, tuple(proofs), (*bound.reasons, str(error)))
    return bound
