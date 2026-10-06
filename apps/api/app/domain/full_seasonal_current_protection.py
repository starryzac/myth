"""Explicit v4 seasonal binding: replay ended originals without changing v3 semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.full_seasonal_adoption import SeasonalAdoptionProof
from app.domain.full_seasonal_ended_adoption import (
    REFERENCE_KIND,
    EndedSeasonalAdoptionProof,
    verify_ended_seasonal_adoption,
)
from app.domain.full_seasonal_protection import (
    SeasonalProtectionBindings,
    derive_seasonal_protection_bindings,
)

if TYPE_CHECKING:
    from app.domain.full_protection_projection import FullProtectionPolicySource

ALGORITHM = "registered-full-protection-ended-seasonal-v4"


@dataclass(frozen=True)
class CurrentSeasonalProtectionBindings:
    active: SeasonalProtectionBindings
    ended_proofs: tuple[EndedSeasonalAdoptionProof, ...]
    requested: bool
    reasons: tuple[str, ...]

    @property
    def proofs(self) -> tuple[SeasonalAdoptionProof, ...]:
        return self.active.proofs

    def amount_on(self, day: date) -> int:
        # Ended originals release a protection floor only. There is no cash credit.
        return self.active.amount_on(day)

    def evidence(self) -> dict[UUID, str]:
        values = self.active.evidence()
        for proof in self.ended_proofs:
            if proof.inputs is None:
                raise ValueError("ENDED_SEASONAL_INPUTS_NOT_PROVEN")
            for record in proof.inputs.records:
                for raw in record.current_evidence_originals:
                    identity, digest = UUID(raw["id"]), raw["content_hash"]
                    if identity in values and values[identity] != digest:
                        raise ValueError("ENDED_SEASONAL_CURRENT_SOURCE_HASH_CONFLICT")
                    values[identity] = digest
        return values


def derive_current_seasonal_protection_bindings(
    sources: list[FullProtectionPolicySource],
    as_of: datetime,
    timezone: str,
    *,
    expected_user_id: UUID | None = None,
) -> CurrentSeasonalProtectionBindings:
    active = derive_seasonal_protection_bindings(
        sources, as_of, timezone, expected_user_id=expected_user_id
    )
    reasons = list(active.reasons)
    ended: list[EndedSeasonalAdoptionProof] = []
    requested = active.requested
    for source in sources:
        wrappers = [row for row in source.reference_snapshots if row.get("kind") == REFERENCE_KIND]
        if not wrappers:
            continue
        requested = True
        try:
            if len(wrappers) != 1 or set(wrappers[0]) != {"kind", "proof"}:
                raise ValueError("ENDED_SEASONAL_PROOF_WRAPPER_NOT_EXACT")
            proof = EndedSeasonalAdoptionProof.model_validate_json(json.dumps(wrappers[0]["proof"]))
            if not verify_ended_seasonal_adoption(
                source, proof, as_of, timezone, expected_user_id=expected_user_id
            ):
                raise ValueError("ENDED_SEASONAL_CURRENT_ORIGINAL_NOT_VERIFIED")
            ended.append(proof)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            reasons.append(f"{source.policy_id}:{error}")
    if ended:
        first = ended[0]
        if first.inputs is None:
            raise ValueError("ENDED_SEASONAL_VERIFIED_INPUT_MISSING")
        for proof in ended:
            if (
                proof.inputs is None
                or proof.user_id != first.user_id
                or proof.epoch_id != first.epoch_id
                or proof.inputs.epoch_original != first.inputs.epoch_original
                or set(proof.registered_adoption_evidence_ids)
                != set(first.registered_adoption_evidence_ids)
                or set(proof.retained_command_ids) != set(first.retained_command_ids)
            ):
                reasons.append("ENDED_SEASONAL_ALL_OWNER_OR_AUDIT_DENOMINATOR_DIFFERS")
        for active_proof in active.proofs:
            if (
                active_proof.user_id != first.user_id
                or active_proof.epoch_id != first.epoch_id
                or set(active_proof.retained_command_ids) != set(first.retained_command_ids)
            ):
                reasons.append("ACTIVE_AND_ENDED_SEASONAL_OWNER_OR_COMMAND_DENOMINATOR_DIFFERS")
        identities = [proof.policy_id for proof in ended] + [
            proof.policy_id for proof in active.proofs
        ]
        if len(set(identities)) != len(identities):
            reasons.append("SEASONAL_CURRENT_POLICY_BINDING_DUPLICATE")
    result = CurrentSeasonalProtectionBindings(
        active, tuple(ended), requested, tuple(sorted(set(reasons)))
    )
    try:
        result.evidence()
    except (ValueError, TypeError, KeyError) as error:
        return CurrentSeasonalProtectionBindings(
            active, tuple(ended), requested, (*result.reasons, str(error))
        )
    return result
