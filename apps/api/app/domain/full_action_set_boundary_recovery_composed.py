"""Explicit v4 composition; old v3 and every unproved producer remain visible."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_boundary import CandidateView, GlobalBoundaryObserveRequest, Hash
from app.domain.full_action_set_boundary_actual import MAX_CANDIDATES, MAX_CAPTURE_BYTES
from app.domain.full_action_set_boundary_composed import (
    ComposedActionSetInput,
    ComposedActionSetSnapshot,
    derive_composed_action_set,
)
from app.domain.full_action_set_recovery_producers import (
    RecoveryActionSetInput,
    RecoveryActionSetResult,
    derive_recovery_producers,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import StrictBool

ALGORITHM: Literal["full-policy-action-set-boundary-recovery-composed-v4"] = (
    "full-policy-action-set-boundary-recovery-composed-v4"
)
SCOPE: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_RECOVERY_COMPOSED_V4"] = (
    "POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_RECOVERY_COMPOSED_V4"
)


class RecoveryComposedActionSetInput(BoundaryModel):
    protocol: Literal["recovery-composed-action-set-input-v4"] = (
        "recovery-composed-action-set-input-v4"
    )
    composed: ComposedActionSetInput
    recovery: RecoveryActionSetInput


class RecoveryComposedActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-action-set-boundary-recovery-composed-v4"] = ALGORITHM
    scope: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_RECOVERY_COMPOSED_V4"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    arbitrary_manual_intents_covered: Literal[False] = False
    notification_support: Literal["NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"] = (
        "NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: StrictBool
    original_composed_snapshot: ComposedActionSetSnapshot
    recovery_family: RecoveryActionSetResult
    original_inventory_hash: Hash
    financial_input_hash: Hash
    input_hash: Hash
    snapshot_hash: Hash
    action_set_signature: Hash | None
    expected_candidate_keys: list[str]
    candidates: list[CandidateView]
    replaced_original_candidate_keys: list[str]
    unsupported_producers: list[str]
    reasons: list[str]


class RecoveryComposedGlobalObservation(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    notification_support: Literal["NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"] = (
        "NOT_IMPLEMENTED_FOR_RECOVERY_COMPOSED_V4"
    )
    user_id: UUID
    epoch_id: UUID
    observation_run_id: UUID
    previous_observation_run_id: UUID | None
    original_request: GlobalBoundaryObserveRequest
    request_hash: Hash
    snapshot: RecoveryComposedActionSetSnapshot
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] | None
    semantic_key: Hash | None
    requires_user_attention: StrictBool
    previous_snapshot_hash: Hash | None
    previous_action_set_signature: Hash | None
    global_action_set_complete: StrictBool
    idempotent_replay: StrictBool = False


def compare_recovery_composed_action_sets(
    before: RecoveryComposedActionSetSnapshot | None,
    after: RecoveryComposedActionSetSnapshot,
) -> tuple[Literal["BoundaryCrossed", "BoundaryObserved"] | None, str | None]:
    if not after.global_action_set_complete or after.action_set_signature is None:
        return None, None
    if before is None:
        return "BoundaryObserved", None
    if (
        not before.global_action_set_complete
        or before.action_set_signature is None
        or (before.user_id, before.epoch_id, before.scope, before.algorithm_version)
        != (after.user_id, after.epoch_id, after.scope, after.algorithm_version)
        or before.as_of > after.as_of
    ):
        return None, None
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] = (
        "BoundaryObserved"
        if before.action_set_signature == after.action_set_signature
        else "BoundaryCrossed"
    )
    return kind, configuration_hash(
        {
            "user_id": str(after.user_id),
            "epoch_id": str(after.epoch_id),
            "scope": SCOPE,
            "before": before.action_set_signature,
            "after": after.action_set_signature,
        }
    )


def derive_recovery_composed_action_set(
    supplied: RecoveryComposedActionSetInput,
) -> RecoveryComposedActionSetSnapshot:
    data = RecoveryComposedActionSetInput.model_validate(supplied.model_dump())
    original = derive_composed_action_set(data.composed)
    family = derive_recovery_producers(data.recovery)
    reasons = [
        reason
        for reason in original.reasons
        if reason
        not in {"COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS", "COMPOSED_CURRENT_PRODUCER_UNKNOWN"}
    ]
    reasons.extend(family.reasons)
    binding = (
        data.composed.periodic.original_actual_input.model_dump(mode="json")
        == data.recovery.original_actual_input.model_dump(mode="json")
        and family.original_actual_input_hash == original.original_actual_snapshot.input_hash
        and (family.user_id, family.epoch_id, family.as_of)
        == (original.user_id, original.epoch_id, original.as_of)
    )
    if not binding:
        reasons.append("RECOVERY_COMPOSED_EXACT_ORIGINAL_SNAPSHOT_BINDING_DIFFERS")
    views = {row.candidate_key: row for row in original.candidates}
    expected = set(original.expected_candidate_keys)
    replaced = list(original.replaced_original_candidate_keys)
    unsupported = set(original.unsupported_producers)
    shadows = [
        row.shadow_original_candidate_key
        for row in family.results
        if row.shadow_original_candidate_key is not None
    ]
    family_keys = [row.candidate_key for row in family.results]
    expected_family_keys = {"full-recovery:" + str(key) for key in family.expected_full_policy_ids}
    exact = (
        binding
        and family.recovery_family_complete
        and set(family_keys) == expected_family_keys
        and len(family_keys) == len(expected_family_keys)
        and len(shadows) == len(set(shadows))
        and set(shadows).issubset(views)
        and not set(family_keys).intersection(views)
        and all(row.view.candidate_key == row.candidate_key for row in family.results)
        and all(row.view.state != "UNKNOWN" for row in family.results)
    )
    if exact:
        unsupported.difference_update(family.handled_unsupported_codes)
        for row in family.results:
            if row.shadow_original_candidate_key is not None:
                key = row.shadow_original_candidate_key
                views.pop(key)
                expected.remove(key)
                replaced.append(key)
            views[row.candidate_key] = row.view
            expected.add(row.candidate_key)
    else:
        reasons.append("RECOVERY_COMPOSED_COMPLETE_UNIQUE_MAPPING_NOT_PROVEN")
        for row in family.results:
            if row.candidate_key not in views:
                views[row.candidate_key] = row.view.model_copy(
                    update={
                        "state": "UNKNOWN",
                        "amount_cents": None,
                        "autonomy_level": None,
                        "signature": None,
                        "reasons": [
                            *row.view.reasons,
                            "RECOVERY_COMPOSED_FAMILY_BINDING_NOT_COMPLETE",
                        ],
                    }
                )
        expected.update(expected_family_keys)
    if set(views) != expected:
        reasons.append("RECOVERY_COMPOSED_COMPLETE_PRODUCER_DENOMINATOR_DIFFERS")
    if unsupported:
        reasons.append("RECOVERY_COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS")
    if any(row.state == "UNKNOWN" for row in views.values()):
        reasons.append("RECOVERY_COMPOSED_CURRENT_PRODUCER_UNKNOWN")
    if (
        len(views) > MAX_CANDIDATES
        or len(data.model_dump_json().encode("utf8")) > MAX_CAPTURE_BYTES
    ):
        reasons.append("RECOVERY_COMPOSED_CAPTURE_CAPACITY_EXCEEDED")
    reasons = sorted(set(reasons))
    result = RecoveryComposedActionSetSnapshot(
        user_id=original.user_id,
        epoch_id=original.epoch_id,
        as_of=original.as_of,
        status="UNKNOWN" if reasons else "COMPLETE",
        global_action_set_complete=not reasons,
        original_composed_snapshot=original,
        recovery_family=family,
        original_inventory_hash=original.original_inventory_hash,
        financial_input_hash=original.financial_input_hash,
        input_hash=configuration_hash(data.model_dump(mode="json")),
        snapshot_hash="0" * 64,
        action_set_signature=None
        if reasons
        else configuration_hash(
            {
                "scope": SCOPE,
                "members": sorted(
                    {row.signature for row in views.values() if row.signature is not None}
                ),
            }
        ),
        expected_candidate_keys=sorted(expected),
        candidates=sorted(views.values(), key=lambda row: row.candidate_key),
        replaced_original_candidate_keys=sorted(replaced),
        unsupported_producers=sorted(unsupported),
        reasons=reasons,
    )
    return result.model_copy(
        update={
            "snapshot_hash": configuration_hash(
                result.model_dump(mode="json", exclude={"snapshot_hash"})
            )
        }
    )
