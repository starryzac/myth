"""New v5 combines independently proven families over one complete original inventory."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_boundary import CandidateView, Hash
from app.domain.full_action_set_boundary_actual import MAX_CANDIDATES, MAX_CAPTURE_BYTES
from app.domain.full_action_set_boundary_recovery_composed import (
    RecoveryComposedActionSetInput,
    RecoveryComposedActionSetSnapshot,
    derive_recovery_composed_action_set,
)
from app.domain.full_action_set_joint_producers import (
    JointActionSetInput,
    JointActionSetResult,
    derive_joint_producers,
)
from app.domain.full_action_set_release_producers import (
    ReleaseActionSetInput,
    ReleaseActionSetResult,
    derive_release_producers,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import StrictBool

ALGORITHM: Literal["full-policy-registered-action-set-boundary-v5"] = (
    "full-policy-registered-action-set-boundary-v5"
)
SCOPE: Literal["POLICY_BACKED_ACTUAL_REGISTERED_SERVER_PRODUCERS_V5"] = (
    "POLICY_BACKED_ACTUAL_REGISTERED_SERVER_PRODUCERS_V5"
)


class RegisteredActionSetInput(BoundaryModel):
    protocol: Literal["registered-action-set-input-v5"] = "registered-action-set-input-v5"
    recovery_composed: RecoveryComposedActionSetInput
    release: ReleaseActionSetInput
    joint: JointActionSetInput


class RegisteredActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-registered-action-set-boundary-v5"] = ALGORITHM
    scope: Literal["POLICY_BACKED_ACTUAL_REGISTERED_SERVER_PRODUCERS_V5"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    arbitrary_manual_intents_covered: Literal[False] = False
    notification_support: Literal["NOT_IMPLEMENTED_FOR_REGISTERED_V5"] = (
        "NOT_IMPLEMENTED_FOR_REGISTERED_V5"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: StrictBool
    original_recovery_composed_snapshot: RecoveryComposedActionSetSnapshot
    release_family: ReleaseActionSetResult
    joint_family: JointActionSetResult
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


def derive_registered_action_set(supplied: RegisteredActionSetInput) -> RegisteredActionSetSnapshot:
    data = RegisteredActionSetInput.model_validate(supplied.model_dump())
    original = derive_recovery_composed_action_set(data.recovery_composed)
    release, joint = derive_release_producers(data.release), derive_joint_producers(data.joint)
    old_actual = data.recovery_composed.recovery.original_actual_input
    views = {row.candidate_key: row for row in original.candidates}
    expected, unsupported = (
        set(original.expected_candidate_keys),
        set(original.unsupported_producers),
    )
    replaced = list(original.replaced_original_candidate_keys)
    # Only aggregate reasons are recomputed; every original family/source issue survives.
    reasons = {
        row
        for row in original.reasons
        if row
        not in {
            "RECOVERY_COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS",
            "RECOVERY_COMPOSED_CURRENT_PRODUCER_UNKNOWN",
        }
    }
    reasons.update(release.reasons)
    reasons.update(joint.reasons)
    release_binding = data.release.original_actual_input.model_dump(
        mode="json"
    ) == old_actual.model_dump(mode="json") and (
        release.user_id,
        release.epoch_id,
        release.as_of,
    ) == (original.user_id, original.epoch_id, original.as_of)
    # The original actual input hash is derived, rather than an input field.
    actual_hash = original.original_composed_snapshot.original_actual_snapshot.input_hash
    release_binding = release_binding and release.original_actual_input_hash == actual_hash
    joint_binding = (
        data.joint.original_actual_input.model_dump(mode="json")
        == old_actual.model_dump(mode="json")
        and (joint.user_id, joint.epoch_id, joint.as_of)
        == (original.user_id, original.epoch_id, original.as_of)
        and joint.original_actual_input_hash == actual_hash
    )
    release_keys = [row.candidate_key for row in release.results]
    release_exact = (
        release_binding
        and release.release_family_complete
        and len(release_keys) == len(set(release_keys))
        and set(release_keys) == set(release.expected_candidate_keys)
        and not set(release_keys).intersection(views)
        and all(
            row.view.state != "UNKNOWN"
            and row.view.candidate_key == row.candidate_key
            and row.shadow_original_candidate_key is None
            for row in release.results
        )
    )
    if release_exact:
        unsupported.difference_update(release.handled_unsupported_codes)
        for row in release.results:
            views[row.candidate_key] = row.view
            expected.add(row.candidate_key)
    else:
        reasons.add("REGISTERED_RELEASE_COMPLETE_EXACT_BINDING_NOT_PROVEN")
        for row in release.results:
            if row.candidate_key not in views:
                views[row.candidate_key] = row.view.model_copy(
                    update={
                        "state": "UNKNOWN",
                        "amount_cents": None,
                        "autonomy_level": None,
                        "signature": None,
                        "reasons": [*row.view.reasons, "REGISTERED_RELEASE_BINDING_NOT_PROVEN"],
                    }
                )
        expected.update(release.expected_candidate_keys)
    joint_keys = [row.candidate_key for row in joint.results]
    shadows = [
        row.shadow_original_candidate_key
        for row in joint.results
        if row.shadow_original_candidate_key is not None
    ]
    joint_exact = (
        joint_binding
        and joint.joint_family_complete
        and len(joint_keys) == len(set(joint_keys))
        and set(joint_keys) == set(joint.expected_candidate_keys)
        and len(shadows) == len(set(shadows))
        and set(shadows).issubset(views)
        and not set(joint_keys).intersection(views)
        and all(
            row.view.state != "UNKNOWN" and row.view.candidate_key == row.candidate_key
            for row in joint.results
        )
    )
    if joint_exact:
        unsupported.difference_update(joint.handled_unsupported_codes)
        for member in joint.results:
            if member.shadow_original_candidate_key is not None:
                key = member.shadow_original_candidate_key
                views.pop(key)
                expected.remove(key)
                replaced.append(key)
            views[member.candidate_key] = member.view
            expected.add(member.candidate_key)
    else:
        reasons.add("REGISTERED_JOINT_COMPLETE_EXACT_BINDING_NOT_PROVEN")
        for member in joint.results:
            if member.candidate_key not in views:
                views[member.candidate_key] = member.view.model_copy(
                    update={
                        "state": "UNKNOWN",
                        "amount_cents": None,
                        "autonomy_level": None,
                        "signature": None,
                        "reasons": [*member.view.reasons, "REGISTERED_JOINT_BINDING_NOT_PROVEN"],
                    }
                )
        expected.update(joint.expected_candidate_keys)
    if unsupported:
        reasons.add("REGISTERED_UNSUPPORTED_CURRENT_PRODUCERS")
    if any(row.state == "UNKNOWN" for row in views.values()):
        reasons.add("REGISTERED_CURRENT_PRODUCER_UNKNOWN")
    if set(views) != expected:
        reasons.add("REGISTERED_COMPLETE_PRODUCER_DENOMINATOR_DIFFERS")
    if (
        len(views) > MAX_CANDIDATES
        or len(data.model_dump_json().encode("utf8")) > MAX_CAPTURE_BYTES
    ):
        reasons.add("REGISTERED_CAPTURE_CAPACITY_EXCEEDED")
    result = RegisteredActionSetSnapshot(
        user_id=original.user_id,
        epoch_id=original.epoch_id,
        as_of=original.as_of,
        status="UNKNOWN" if reasons else "COMPLETE",
        global_action_set_complete=not reasons,
        original_recovery_composed_snapshot=original,
        release_family=release,
        joint_family=joint,
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
        reasons=sorted(reasons),
    )
    return result.model_copy(
        update={
            "snapshot_hash": configuration_hash(
                result.model_dump(mode="json", exclude={"snapshot_hash"})
            ),
        }
    )
