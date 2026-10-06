"""Actual v3 composition of independently proven current producer families."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_action_set_boundary import CandidateView, Hash
from app.domain.full_action_set_boundary_actual import (
    MAX_CANDIDATES,
    MAX_CAPTURE_BYTES,
    ActualActionSetSnapshot,
    derive_actual_action_set,
)
from app.domain.full_action_set_payment_producers import (
    PeriodicActionSetInput,
    PeriodicActionSetResult,
    derive_periodic_payment_producers,
)
from app.domain.policy_configuration import configuration_hash
from pydantic import StrictBool

ALGORITHM: Literal["full-policy-action-set-boundary-composed-v3"] = (
    "full-policy-action-set-boundary-composed-v3"
)
SCOPE: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_COMPOSED_V3"] = (
    "POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_COMPOSED_V3"
)


class ComposedActionSetInput(BoundaryModel):
    protocol: Literal["composed-action-set-input-v3"] = "composed-action-set-input-v3"
    # The original actual input is retained once, inside this exact family input.
    periodic: PeriodicActionSetInput


class ComposedActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-action-set-boundary-composed-v3"] = ALGORITHM
    scope: Literal["POLICY_BACKED_ACTUAL_SERVER_PRODUCERS_COMPOSED_V3"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    arbitrary_manual_intents_covered: Literal[False] = False
    notification_support: Literal["NOT_IMPLEMENTED_FOR_COMPOSED_V3"] = (
        "NOT_IMPLEMENTED_FOR_COMPOSED_V3"
    )
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: StrictBool
    original_actual_snapshot: ActualActionSetSnapshot
    periodic_family: PeriodicActionSetResult
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


def derive_composed_action_set(supplied: ComposedActionSetInput) -> ComposedActionSetSnapshot:
    data = ComposedActionSetInput.model_validate(supplied.model_dump())
    original = derive_actual_action_set(data.periodic.original_actual_input)
    family = derive_periodic_payment_producers(data.periodic)
    reasons = [
        reason
        for reason in original.reasons
        if reason not in {"ACTUAL_UNSUPPORTED_CURRENT_PRODUCERS", "ACTUAL_CURRENT_PRODUCER_UNKNOWN"}
    ]
    reasons.extend(family.reasons)
    if family.original_actual_input_hash != original.input_hash or (
        family.user_id,
        family.epoch_id,
        family.as_of,
    ) != (original.user_id, original.epoch_id, original.as_of):
        reasons.append("COMPOSED_EXACT_ORIGINAL_SNAPSHOT_BINDING_DIFFERS")
    original_views = {row.candidate_key: row for row in original.candidates}
    expected = set(original.expected_candidate_keys)
    views = dict(original_views)
    replaced: list[str] = []
    unsupported = set(original.unsupported_producers)
    shadows = [
        row.shadow_original_candidate_key
        for row in family.results
        if row.shadow_original_candidate_key is not None
    ]
    expected_family_keys = {"full-periodic:" + str(x) for x in family.expected_full_policy_ids}
    family_keys = [row.candidate_key for row in family.results]
    exact = (
        family.periodic_family_complete
        and set(family_keys) == expected_family_keys
        and len(family_keys) == len(expected_family_keys)
        and len(shadows) == len(set(shadows))
        and set(shadows).issubset(original_views)
        and not set(family_keys).intersection(original_views)
        and all(row.view.candidate_key == row.candidate_key for row in family.results)
        and all(row.view.state != "UNKNOWN" for row in family.results)
    )
    if exact:
        # This is a recomputed family result, not a supplied string allowlist.
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
        reasons.append("COMPOSED_PERIODIC_COMPLETE_UNIQUE_MAPPING_NOT_PROVEN")
        # The original producer stays visible while its replacement is unknown.
        for row in family.results:
            if row.candidate_key not in views:
                views[row.candidate_key] = row.view.model_copy(
                    update={
                        "state": "UNKNOWN",
                        "amount_cents": None,
                        "autonomy_level": None,
                        "signature": None,
                        "reasons": [*row.view.reasons, "COMPOSED_FAMILY_BINDING_NOT_COMPLETE"],
                    }
                )
        expected.update(expected_family_keys)
    if set(views) != expected:
        reasons.append("COMPOSED_COMPLETE_PRODUCER_DENOMINATOR_DIFFERS")
    if unsupported:
        reasons.append("COMPOSED_UNSUPPORTED_CURRENT_PRODUCERS")
    if any(row.state == "UNKNOWN" for row in views.values()):
        reasons.append("COMPOSED_CURRENT_PRODUCER_UNKNOWN")
    if (
        len(views) > MAX_CANDIDATES
        or len(data.model_dump_json().encode("utf8")) > MAX_CAPTURE_BYTES
    ):
        reasons.append("COMPOSED_CAPTURE_CAPACITY_EXCEEDED")
    reasons = sorted(set(reasons))
    result = ComposedActionSetSnapshot(
        user_id=original.user_id,
        epoch_id=original.epoch_id,
        as_of=original.as_of,
        status="UNKNOWN" if reasons else "COMPLETE",
        global_action_set_complete=not reasons,
        original_actual_snapshot=original,
        periodic_family=family,
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
