"""Server selection of one original zero-cost whole position; no new bank protocol."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_recovery_execution import FullRecoveryPrepareRequest, Key
from app.domain.full_recovery_planning import FullRecoveryPlanningResult
from app.domain.policy_configuration import UUIDReference, configuration_hash
from pydantic import field_validator

PROTOCOL: Literal["full-recovery-next-whole-v2"] = "full-recovery-next-whole-v2"


class FullRecoveryNextRequest(BoundaryModel):
    policy_id: UUIDReference
    expected_version_id: UUIDReference
    expected_epoch_id: UUIDReference
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The original root key must not be blank")
        return value


class NextWholeEligibility(BoundaryModel):
    position_id: UUID
    selected_by_original_plan: bool
    eligible_for_v1_preview: bool
    reasons: list[str]


class FullRecoveryNextSelection(BoundaryModel):
    protocol: Literal["full-recovery-next-whole-v2"] = PROTOCOL
    bank_authority: Literal[False] = False
    atomic_combination: Literal[False] = False
    current_candidate_denominator: int
    current_selected_denominator: int
    eligibility: list[NextWholeEligibility]
    next_v1_request: FullRecoveryPrepareRequest | None
    reasons: list[str]


def next_whole_v1_key(root_key: str) -> str:
    # Deliberately independent of position/version: the same root key can never
    # advance to another position or evade an original request conflict.
    if type(root_key) is not str or not 1 <= len(root_key) <= 120 or not root_key.strip():
        raise ValueError("Invalid original next-whole root key")
    return "next-whole-v2:" + configuration_hash({"protocol": PROTOCOL, "root_key": root_key})


def derive_next_whole_selection(
    request: FullRecoveryNextRequest,
    plan: FullRecoveryPlanningResult,
    user_id: UUID,
    now: datetime,
) -> FullRecoveryNextSelection:
    """Keep the entire original denominator/order; v1 still verifies all actual facts."""
    request = FullRecoveryNextRequest.model_validate_json(request.model_dump_json())
    plan = FullRecoveryPlanningResult.model_validate_json(plan.model_dump_json())
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or (plan.user_id, plan.policy_id, plan.policy_version_id, plan.as_of)
        != (user_id, request.policy_id, request.expected_version_id, now)
    ):
        raise ValueError("Original current planning owner/version/clock differs")
    ids = [row.position_id for row in plan.candidates]
    selected_ids = [row.position_id for row in plan.lossless_steps]
    candidates = {row.position_id: row for row in plan.candidates}
    if (
        len(ids) != len(set(ids))
        or len(selected_ids) != len(set(selected_ids))
        or not set(selected_ids) <= set(ids)
        or any(row != candidates[row.position_id] for row in plan.lossless_steps)
    ):
        raise ValueError("Complete original candidate and selected denominators differ")
    eligibility: list[NextWholeEligibility] = []
    for row in plan.candidates:
        reasons: list[str] = []
        quote = row.original_quote
        if row.position_id not in selected_ids:
            reasons.append("NOT_SELECTED_BY_ORIGINAL_PLAN")
        if plan.status not in {"CONDITIONAL_RECOVERY_PLAN", "LIQUIDITY_RISK"}:
            reasons.append("ORIGINAL_RECOVERY_TRIGGER_NOT_ACTIVE")
        if (
            not row.lossless_eligible
            or not row.within_full_planning_limits
            or row.decision != "ASK_ONCE"
            or row.original_policy_version_id is None
            or row.goal_id != plan.goal_id
        ):
            reasons.append("ORIGINAL_ZERO_COST_SCOPE_NOT_PROVEN")
        if row.liquidity_rank not in {0, 1}:
            reasons.append("MATURE_LOSS_OR_OTHER_CLASS_REQUIRES_SEPARATE_ORIGINAL_PROTOCOL")
        if quote is None or row.quote_source == "MISSING":
            reasons.append("ORIGINAL_QUOTE_NOT_PROVEN")
        elif (
            quote.kind != "REDEEM"
            or quote.user_id != user_id
            or quote.position_id != row.position_id
            or quote.product_id != row.product_id
            or quote.product_version_number != row.product_version_number
            or quote.terms_digest != row.terms_digest
            or quote.principal_cents != row.principal_cents
            or quote.net_cents != row.principal_cents
            or quote.fee_cents != 0
            or quote.loss_cents != 0
            or not quote.request_at <= now < quote.expires_at
            or row.liquidity_rank not in {0, 1}
            or quote.principal_available_at - quote.request_at != timedelta(days=row.liquidity_rank)
        ):
            reasons.append("ORIGINAL_WHOLE_ZERO_COST_QUOTE_DIFFERS_OR_EXPIRED")
        if (
            row.on_time is not True
            or plan.deadline_at is None
            or row.earliest_conditional_cash_at is None
            or row.earliest_conditional_cash_at > plan.deadline_at
            or row.liquidity_rank not in {0, 1}
            or now + timedelta(days=row.liquidity_rank) > plan.deadline_at
        ):
            reasons.append("ORIGINAL_REQUIRED_ARRIVAL_NOT_PROVEN_NO_DEADLINE_EXTENSION")
        eligibility.append(
            NextWholeEligibility(
                position_id=row.position_id,
                selected_by_original_plan=row.position_id in selected_ids,
                eligible_for_v1_preview=not reasons,
                reasons=reasons,
            )
        )
    eligible = {row.position_id for row in eligibility if row.eligible_for_v1_preview}
    selected = next((row for row in plan.lossless_steps if row.position_id in eligible), None)
    forwarded = (
        FullRecoveryPrepareRequest(
            policy_id=request.policy_id,
            expected_version_id=request.expected_version_id,
            expected_epoch_id=request.expected_epoch_id,
            position_id=selected.position_id,
            idempotency_key=next_whole_v1_key(request.idempotency_key),
        )
        if selected is not None
        else None
    )
    return FullRecoveryNextSelection(
        current_candidate_denominator=len(ids),
        current_selected_denominator=len(selected_ids),
        eligibility=eligibility,
        next_v1_request=forwarded,
        reasons=[] if forwarded is not None else ["NO_CURRENT_ELIGIBLE_T0_T1_WHOLE_POSITION"],
    )
