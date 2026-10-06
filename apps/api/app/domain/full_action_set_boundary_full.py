"""Frozen FULL producer inputs; dynamic contributions replace the same Goal producer.

This is a private deterministic input, never an HTTP fact/permission schema.
Unimplemented FULL families retain their complete original denominator.
"""

import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid5

from app.domain.autonomy import classify_autonomy
from app.domain.autonomy_types import AuthorityAssessment, AutonomyFacts
from app.domain.boundary_action_events import action_signature
from app.domain.boundary_types import BoundaryModel
from app.domain.execution import revalidate_execution
from app.domain.full_action_set_boundary import (
    ActionSetInput,
    CandidateInput,
    CandidateView,
    GlobalBoundaryObserveRequest,
    Hash,
    derive_action_set,
    known_inactive_keys,
)
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalInput,
    build_full_dynamic_goal_effect,
    derive_full_dynamic_goal_proof,
    native_income_original_matches,
)
from app.domain.full_execution_protection import validate_full_execution_protection
from app.domain.full_protection_projection import (
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.full_registered_account_debits import derive_full_account_debit_bounds
from app.domain.policy_configuration import configuration_hash
from pydantic import Field

ALGORITHM: Literal["full-policy-action-set-boundary-full-v1"] = (
    "full-policy-action-set-boundary-full-v1"
)
SCOPE: Literal["POLICY_BACKED_FULL_SERVER_PRODUCERS_V1"] = "POLICY_BACKED_FULL_SERVER_PRODUCERS_V1"
NAMESPACE = UUID("d7e3dd24-17d9-49fb-8596-280ad375fa30")
MAX_CANDIDATES = 16


class DynamicGoalProducerInput(BoundaryModel):
    candidate_key: str
    # Includes every VALID model original for this source Goal, including older
    # versions. Only the uniquely current, actually verified model is consumed.
    model_evidence_ids: list[UUID]
    data: FullDynamicGoalInput | None = None
    authority: AuthorityAssessment | None = None
    excluded_by_current_policy: bool = False
    missing_reasons: list[str] = Field(default_factory=list)


class FullActionSetInput(BoundaryModel):
    base: ActionSetInput
    dynamic_goals: list[DynamicGoalProducerInput]


class FullActionSetSnapshot(BoundaryModel):
    algorithm_version: Literal["full-policy-action-set-boundary-full-v1"] = ALGORITHM
    scope: Literal["POLICY_BACKED_FULL_SERVER_PRODUCERS_V1"] = SCOPE
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    arbitrary_manual_intents_covered: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    status: Literal["COMPLETE", "UNKNOWN"]
    global_action_set_complete: bool
    original_inventory_hash: Hash
    financial_input_hash: Hash
    input_hash: Hash
    snapshot_hash: Hash
    action_set_signature: Hash | None
    expected_candidate_keys: list[str]
    candidates: list[CandidateView]
    dynamic_candidate_keys: list[str]
    unsupported_producers: list[str]
    reasons: list[str]


class FullGlobalBoundaryObservation(BoundaryModel):
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    grants_authority: Literal[False] = False
    financial_write: Literal[False] = False
    user_id: UUID
    epoch_id: UUID
    observation_run_id: UUID
    previous_observation_run_id: UUID | None
    original_request: GlobalBoundaryObserveRequest
    request_hash: Hash
    snapshot: FullActionSetSnapshot
    kind: Literal["BoundaryCrossed", "BoundaryObserved"] | None
    semantic_key: Hash | None
    requires_user_attention: bool
    previous_snapshot_hash: Hash | None
    previous_action_set_signature: Hash | None
    global_action_set_complete: bool
    idempotent_replay: bool = False
    question_delivery: Literal["ROOT_GLOBAL_INTERVENTION_BRANCH_REQUIRED"] = (
        "ROOT_GLOBAL_INTERVENTION_BRANCH_REQUIRED"
    )


def dynamic_model_inventory(base: ActionSetInput) -> dict[str, list[UUID]]:
    values: dict[str, list[UUID]] = {}
    for row in base.original_inventory.get("evidence_items", []):
        if row.get("source_type") == "FULL_GOAL_MODEL_V1" and row.get("status") == "VALID":
            key = "goal:" + str(row["source_ref"])
            values.setdefault(key, []).append(UUID(row["id"]))
    return {key: sorted(ids) for key, ids in sorted(values.items())}


def _unknown(key: str, reasons: list[str]) -> CandidateView:
    return CandidateView(
        candidate_key=key,
        state="UNKNOWN",
        action_type="ALLOCATE_GOAL",
        amount_cents=None,
        autonomy_level=None,
        signature=None,
        reasons=sorted(set(reasons)),
    )


def _raw_sources(
    base: ActionSetInput, value: DynamicGoalProducerInput, *, actual_v2_native_values: bool = False
) -> None:
    data = value.data
    assert data is not None and value.authority is not None
    if (
        value.candidate_key != "goal:" + str(data.request.goal_id)
        or data.context.user_id != base.user_id
        or data.epoch_id != base.epoch_id
        or data.context.snapshot.as_of != base.as_of
        or data.own_effect is not None
    ):
        raise ValueError("DYNAMIC_ORIGINAL_OWNER_EPOCH_CLOCK_OR_OWN_CLAIM_DIFFERS")
    evidence = {row["id"]: row for row in base.original_inventory.get("evidence_items", [])}
    required = {str(row.evidence_id): row.content_hash for row in data.source_refs}
    if len(required) != len(data.source_refs) or any(
        row.user_id != base.user_id for row in data.source_refs
    ):
        raise ValueError("DYNAMIC_ORIGINAL_SOURCE_DENOMINATOR_DIFFERS")
    required[str(data.model_evidence_id)] = data.model_evidence_hash
    required[str(data.income_evidence_id)] = data.income_evidence_hash
    for identity in value.authority.evidence_ids:
        raw = evidence.get(str(identity))
        if raw is None:
            raise ValueError("DYNAMIC_ORIGINAL_AUTHORITY_SOURCE_MISSING")
        required[str(identity)] = raw["content_hash"]
    for evidence_key, digest in required.items():
        raw = evidence.get(evidence_key)
        content = raw.get("content") if raw is not None else None
        if (
            raw is None
            or raw.get("user_id") != str(base.user_id)
            or raw.get("status") != "VALID"
            or raw.get("content_hash") != digest
            or not isinstance(content, dict)
            or configuration_hash(content) != digest
        ):
            raise ValueError("DYNAMIC_ORIGINAL_EVIDENCE_NOT_VERIFIED:" + evidence_key)
        observed = raw.get("observed_at")
        if observed is None or datetime.fromisoformat(observed) > base.as_of:
            raise ValueError("DYNAMIC_ORIGINAL_EVIDENCE_OBSERVATION_MISSING_OR_FUTURE")
        for field, past in (("valid_from", True), ("valid_until", False)):
            if raw.get(field) is not None:
                clock = datetime.fromisoformat(raw[field])
                if (past and base.as_of < clock) or (not past and base.as_of >= clock):
                    raise ValueError("DYNAMIC_ORIGINAL_EVIDENCE_WINDOW_DIFFERS")
    model = evidence[str(data.model_evidence_id)]
    income = evidence[str(data.income_evidence_id)]
    if (
        data.model_evidence_id not in value.model_evidence_ids
        or model.get("source_type") != "FULL_GOAL_MODEL_V1"
        or model.get("source_ref") != str(data.request.goal_id)
        or model.get("content") != data.model_original
        or (
            not native_income_original_matches(income["content"], data.income)
            if actual_v2_native_values
            else income.get("content") != data.income.model_dump(mode="json")
        )
    ):
        raise ValueError("DYNAMIC_ORIGINAL_MODEL_OR_INCOME_COPY_DIFFERS")
    original_income = base.financial_basis.get("income")
    if (
        not isinstance(original_income, dict)
        or not native_income_original_matches(original_income, data.income)
        if actual_v2_native_values
        else original_income != data.income.model_dump(mode="json")
    ):
        raise ValueError("DYNAMIC_BASE_COMPLETE_INCOME_DIFFERS")
    cash: dict[UUID, int] = {}
    goals: dict[UUID, int] = {}
    positions: set[UUID] = set()
    for claim in base.original_inventory.get("action_resource_reservations", []):
        if claim.get("status") != "RESERVED":
            continue
        if datetime.fromisoformat(claim["created_at"]) > base.as_of:
            raise ValueError("DYNAMIC_ORIGINAL_RESERVATION_IS_FUTURE")
        kind, identity = claim["resource_kind"], UUID(claim["resource_key"])
        amount = claim["amount_cents"]
        if type(amount) is not int or amount < 0:
            raise ValueError("DYNAMIC_ORIGINAL_RESERVATION_AMOUNT_INVALID")
        if kind == "CASH":
            cash[identity] = cash.get(identity, 0) + amount
        elif kind == "GOAL_CASH":
            goals[identity] = goals.get(identity, 0) + amount
        elif kind == "POSITION":
            positions.add(identity)
        else:
            raise ValueError("DYNAMIC_ORIGINAL_RESERVATION_KIND_UNKNOWN")
    for fragment in data.income.fragments:
        if fragment.legacy_reserved_cents:
            cash[fragment.account_id] = (
                cash.get(fragment.account_id, 0) + fragment.legacy_reserved_cents
            )
    if (
        cash != data.context.reserved_cash_by_account
        or goals != data.context.reserved_goal_cash_by_goal
        or positions != set(data.context.reserved_position_ids)
    ):
        raise ValueError("DYNAMIC_COMPLETE_ORIGINAL_CLAIMS_DIFFER")
    if data.context.exposure is not None:
        original_exposure = base.financial_basis.get("exposure")
        if not isinstance(original_exposure, dict) or any(
            data.context.exposure.model_dump(mode="json")[field] != original_exposure.get(field)
            for field in ("reserved_cash_by_account", "reserved_goal_cash_by_goal")
        ):
            raise ValueError("DYNAMIC_COMPLETE_ORIGINAL_EXPOSURE_CLAIMS_DIFFER")
    actual_protection = {
        row["id"]: row
        for row in base.original_inventory.get("full_policies", [])
        if row.get("template_name")
        in {"DatedExpensePolicy", "PeriodicTransferPolicy", "SeasonalReservePolicy"}
    }
    if {str(row.policy_id) for row in data.protection_policies} != set(actual_protection):
        raise ValueError("DYNAMIC_FULL_PROTECTION_DENOMINATOR_DIFFERS")
    full_versions = {
        row["id"]: row for row in base.original_inventory.get("full_policy_versions", [])
    }
    for source in data.protection_policies:
        raw = full_versions.get(str(source.version_id))
        latest = max(
            (
                row
                for row in full_versions.values()
                if row.get("policy_id") == str(source.policy_id)
            ),
            key=lambda row: row["version_number"],
            default=None,
        )
        if (
            raw is None
            or raw.get("policy_id") != str(source.policy_id)
            or latest is None
            or latest["id"] != str(source.version_id)
            or raw.get("configuration") != source.configuration
            or raw.get("content_hash") != source.content_hash
            or raw.get("confirmation") != source.confirmation
        ):
            raise ValueError("DYNAMIC_ORIGINAL_FULL_PROTECTION_VERSION_DIFFERS")
    version = next(
        (
            row
            for row in base.original_inventory.get("policy_versions", [])
            if row["id"] == str(data.request.expected_policy_version_id)
        ),
        None,
    )
    if version is None or value.authority.policy_version_ids != [UUID(version["id"])]:
        raise ValueError("DYNAMIC_ORIGINAL_AUTHORITY_VERSION_DIFFERS")
    confirmed = version.get("confirmed_at")
    confirmation = version.get("confirmation", {})
    expected = {
        "user_id": str(base.user_id),
        "policy_id": version["policy_id"],
        "version_id": version["id"],
        "reviewed_hash": version["content_hash"],
        "confirmed_at": confirmed,
        "accepted": True,
    }
    confirmation_matches = all(confirmation.get(key) == item for key, item in expected.items())
    if actual_v2_native_values:
        # The new actual-v2 reads a SQL datetime and native signed JSON together.
        # Their original spellings/hashes stay intact; only aware values compare.
        def exact_native_field(key: str, item: object) -> bool:
            original = confirmation.get(key)
            if type(original) is not type(item):
                return False
            if key != "confirmed_at":
                return original == item
            if not isinstance(original, str) or not isinstance(item, str):
                return False
            left, right = datetime.fromisoformat(original), datetime.fromisoformat(item)
            return left.utcoffset() is not None and right.utcoffset() is not None and left == right

        confirmation_matches = all(exact_native_field(key, item) for key, item in expected.items())
    version_sources = {UUID(identity) for identity in version.get("evidence_ids", [])}
    if (
        confirmed is None
        or datetime.fromisoformat(confirmed) > base.as_of
        or version.get("valid_from") is None
        or not confirmation_matches
        or set(value.authority.evidence_ids) != version_sources
        or not any(
            evidence[str(identity)].get("source_type") == "POLICY_CONFIRMATION"
            and evidence[str(identity)].get("source_ref") == version["id"]
            and evidence[str(identity)].get("evidence_level") == "USER_CONFIRMED_POLICY"
            and evidence[str(identity)].get("content") == confirmation
            for identity in version_sources
        )
    ):
        raise ValueError("DYNAMIC_ORIGINAL_POLICY_CONFIRMATION_NOT_BOUND")
    if value.authority.status != "AUTHORIZED" or value.authority.reasons:
        raise ValueError("DYNAMIC_CURRENT_ORIGINAL_AUTHORITY_NOT_VERIFIED")


def dynamic_candidate(
    base: ActionSetInput,
    value: DynamicGoalProducerInput,
    *,
    actual_v2_native_values: bool = False,
) -> tuple[CandidateView, CandidateInput | None]:
    """Return a view and original-binding shadow; never rewrite the saved base."""
    key = value.candidate_key
    if value.missing_reasons:
        return _unknown(key, value.missing_reasons), None
    if value.excluded_by_current_policy:
        if value.data is not None or key not in known_inactive_keys(
            base.original_inventory, base.as_of
        ):
            return _unknown(key, ["DYNAMIC_CURRENT_POLICY_DENIAL_NOT_PROVEN"]), None
        return CandidateView(
            candidate_key=key,
            state="EXCLUDED",
            action_type="ALLOCATE_GOAL",
            amount_cents=None,
            autonomy_level="BLOCKED",
            signature=None,
            reasons=["CURRENT_ORIGINAL_POLICY_NOT_ACTIVE"],
        ), None
    if value.data is None or value.authority is None:
        return _unknown(key, ["COMPLETE_ACTUAL_DYNAMIC_PRODUCER_INPUT_MISSING"]), None
    data = value.data
    try:
        _raw_sources(base, value, actual_v2_native_values=actual_v2_native_values)
        unbound = derive_full_dynamic_goal_proof(data)
        if unbound.status == "UNKNOWN":
            return _unknown(key, unbound.reasons), None
        if unbound.status == "BLOCKED":
            return CandidateView(
                candidate_key=key,
                state="EXCLUDED",
                action_type="ALLOCATE_GOAL",
                amount_cents=unbound.dynamic_cap_cents,
                autonomy_level="BLOCKED",
                signature=None,
                reasons=unbound.reasons,
            ), None
        effect, proof = build_full_dynamic_goal_effect(
            data, uuid5(NAMESPACE, f"{base.user_id}:{base.epoch_id}:{key}")
        )
        validation = revalidate_execution(effect, data.context, full_dynamic_goal_proof=proof)
        facts = AutonomyFacts(
            user_id=base.user_id,
            as_of=base.as_of,
            action_type="ALLOCATE_GOAL",
            initiation="CONFIRMED_POLICY",
            authority=value.authority,
            effect=effect,
            validation=validation,
            source_evidence_ids=sorted({row.evidence_id for row in data.source_refs}),
            source_context_hash=base.financial_input_hash,
        )
        bounds = None
        if validation.projected_snapshot is not None:
            projected_input = FullProtectionProjectionInput(
                snapshot=validation.projected_snapshot,
                boundary_versions=data.context.versions,
                positions=validation.projected_positions,
                boundary_products=data.context.boundary_products,
                policies=data.protection_policies,
                reserved_cash_by_account=data.context.reserved_cash_by_account,
            )
            projection = project_full_protection(projected_input)
            if projection.source_account_checks:
                bounds = derive_full_account_debit_bounds(
                    effect, data.context, validation, projected_input, projection
                )
        veto = validate_full_execution_protection(
            effect,
            data.context,
            validation,
            data.protection_policies,
            source_issues=tuple([*base.source_reasons, *data.protection_issues]),
            account_debit_bounds=bounds,
        )
        shadow = CandidateInput(
            candidate_key=key,
            facts=facts,
            execution_context=data.context,
            full_protection=veto,
            full_sources=data.protection_policies,
            full_source_issues=[*base.source_reasons, *data.protection_issues],
            full_account_debit_bounds=bounds,
        )
        if validation.status == "INSUFFICIENT_EVIDENCE" or veto.status == "UNKNOWN":
            return _unknown(key, [*validation.reasons, *veto.reasons]), shadow
        decision = classify_autonomy(facts)
        included = decision.level != "BLOCKED" and veto.status in {"PASSED", "NO_ADDITIONAL_POLICY"}
        return CandidateView(
            candidate_key=key,
            state="INCLUDED" if included else "EXCLUDED",
            action_type="ALLOCATE_GOAL",
            amount_cents=effect.amount_cents,
            autonomy_level=decision.level,
            signature=action_signature(effect, validation, decision.level) if included else None,
            reasons=[*decision.reasons, *veto.reasons],
        ), shadow
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        return _unknown(key, [str(error)]), None


def derive_full_action_set(value: FullActionSetInput) -> FullActionSetSnapshot:
    value = FullActionSetInput.model_validate(value.model_dump())
    base = value.base
    expected = dynamic_model_inventory(base)
    keys = [row.candidate_key for row in value.dynamic_goals]
    reasons: list[str] = []
    if len(keys) > MAX_CANDIDATES:
        reasons.append("DYNAMIC_PRODUCER_CAPACITY_EXCEEDED")
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        reasons.append("COMPLETE_DYNAMIC_PRODUCER_DENOMINATOR_DIFFERS")
    views: dict[str, CandidateView] = {}
    shadows: dict[str, CandidateInput] = {}
    handled: set[str] = set()
    for row in value.dynamic_goals:
        if sorted(row.model_evidence_ids) != expected.get(row.candidate_key):
            views[row.candidate_key] = _unknown(
                row.candidate_key, ["ORIGINAL_MODEL_DENOMINATOR_DIFFERS"]
            )
            continue
        result, shadow = dynamic_candidate(base, row)
        views[row.candidate_key] = result
        if shadow is not None:
            shadows[row.candidate_key] = shadow
        if result.state != "UNKNOWN":
            handled.add("DYNAMIC_GOAL_PRODUCER_ADAPTER_MISSING:" + row.candidate_key[5:])
    # Only an in-memory evaluation copy uses dynamic effects for v1 identity,
    # row, source and permission-copy checks. Persisted base bytes are untouched.
    original = derive_action_set(
        base.model_copy(
            update={"candidates": [shadows.get(row.candidate_key, row) for row in base.candidates]}
        )
    )
    reasons.extend(
        reason
        for reason in original.reasons
        if reason not in {"CURRENT_PRODUCER_UNKNOWN", "UNSUPPORTED_CURRENT_PRODUCERS"}
    )
    candidates = sorted(
        [views.get(row.candidate_key, row) for row in original.candidates],
        key=lambda row: row.candidate_key,
    )
    if not set(keys).issubset(base.expected_candidate_keys):
        reasons.append("DYNAMIC_GOAL_HAS_NO_ORIGINAL_GOAL_PRODUCER")
    unsupported = sorted(set(base.unsupported_producers) - handled)
    if unsupported:
        reasons.append("UNSUPPORTED_CURRENT_PRODUCERS")
    if any(row.state == "UNKNOWN" for row in candidates):
        reasons.append("CURRENT_PRODUCER_UNKNOWN")
    if len(candidates) > MAX_CANDIDATES:
        reasons.append("GLOBAL_PRODUCER_CAPACITY_EXCEEDED")
    reasons = sorted(set(reasons))
    fields = {
        "user_id": str(base.user_id),
        "epoch_id": str(base.epoch_id),
        "as_of": base.as_of.isoformat(),
        "status": "UNKNOWN" if reasons else "COMPLETE",
        "global_action_set_complete": not reasons,
        "original_inventory_hash": original.original_inventory_hash,
        "financial_input_hash": base.financial_input_hash,
        "input_hash": configuration_hash(value.model_dump(mode="json")),
        "action_set_signature": None
        if reasons
        else configuration_hash(
            {
                "scope": SCOPE,
                "members": sorted(
                    {row.signature for row in candidates if row.signature is not None}
                ),
            }
        ),
        "expected_candidate_keys": original.expected_candidate_keys,
        "candidates": [row.model_dump(mode="json") for row in candidates],
        "dynamic_candidate_keys": sorted(expected),
        "unsupported_producers": unsupported,
        "reasons": reasons,
    }
    return FullActionSetSnapshot.model_validate_json(
        json.dumps({**fields, "snapshot_hash": configuration_hash(fields)})
    )


def compare_full_action_sets(
    before: FullActionSetSnapshot | None, after: FullActionSetSnapshot
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
