"""Dedicated release economics; old execution effects and income buckets stay unchanged.

These pure contracts check data bindings. Only the production adapter can verify
the current original permission, full bank ledger, source inventory and emergency.
No constructor, receipt string or local three-leg check supplies those facts.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_release_authorization import GoalReleaseAuthorization
from app.domain.full_policy_configuration import EmergencyCondition
from app.domain.goal_release_provenance import GoalCashSourceProof
from app.domain.income_ledger import location_id
from app.domain.policy_configuration import configuration_hash
from pydantic import Field, StrictInt, model_validator

Cents = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
PositiveCents = Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
ZeroCents = Annotated[StrictInt, Field(ge=0, le=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class GoalReleaseUse(BoundaryModel):
    allocation_action_id: UUID
    original_policy_version_id: UUID
    fragment_id: UUID
    origin_transaction_id: UUID
    income_location_account_id: UUID
    allocation_effect_hash: Hash
    allocation_bank_request_hash: Hash
    allocation_action_request_hash: Hash
    amount_cents: PositiveCents

    @model_validator(mode="after")
    def original_fragment_identity(self) -> Self:
        if self.fragment_id != location_id(
            self.origin_transaction_id, self.income_location_account_id
        ):
            raise ValueError("Release use must retain the exact original income location")
        return self


class GoalReleaseEffect(BoundaryModel):
    protocol: Literal["full-goal-release-effect-v1"] = "full-goal-release-effect-v1"
    simulation: Literal[True] = True
    action_type: Literal["RELEASE_GOAL"] = "RELEASE_GOAL"
    operation_id: UUID
    user_id: UUID
    epoch_id: UUID
    business_key: Annotated[str, Field(min_length=1, max_length=160)]
    bank_idempotency_key: Annotated[str, Field(min_length=1, max_length=160)]
    policy_id: UUID
    policy_version_id: UUID
    policy_configuration_hash: Hash
    authorization_id: UUID
    authorization_evidence_id: UUID
    authorization_evidence_hash: Hash
    authorization_scope_hash: Hash
    authorization_request_hash: Hash
    source_goal_id: UUID
    original_goal_policy_id: UUID
    original_goal_policy_version_id: UUID
    full_model_evidence_id: UUID
    full_model_evidence_hash: Hash
    full_configuration_hash: Hash
    minimum_guarantee_cents: Cents
    source_account_id: UUID
    destination_account_id: UUID
    destination_scope: Literal["PROTECTED_CASH"] = "PROTECTED_CASH"
    amount_cents: PositiveCents
    emergency_conditions: Annotated[list[EmergencyCondition], Field(min_length=1, max_length=3)]
    release_uses: Annotated[list[GoalReleaseUse], Field(min_length=1, max_length=10000)]
    source_provenance_hash: Hash
    financial_input_hash: Hash
    valid_from: datetime
    expires_at: datetime
    cumulative_scope: Literal["POLICY_ID_ALL_VERSIONS"] = "POLICY_ID_ALL_VERSIONS"
    fee_cents: ZeroCents = 0
    loss_cents: ZeroCents = 0
    principal_change_cents: ZeroCents = 0
    other_goal_change_cents: ZeroCents = 0
    available_income_increase_cents: ZeroCents = 0
    assigned_income_decrease_cents: ZeroCents = 0

    @model_validator(mode="after")
    def exact_finite_economics(self) -> Self:
        identities = [(row.allocation_action_id, row.fragment_id) for row in self.release_uses]
        if (
            self.source_account_id == self.destination_account_id
            or not self.business_key.strip()
            or not self.bank_idempotency_key.strip()
            or self.valid_from >= self.expires_at
            or identities != sorted(set(identities))
            or self.emergency_conditions != sorted(set(self.emergency_conditions))
            or sum(row.amount_cents for row in self.release_uses) != self.amount_cents
        ):
            raise ValueError(
                "A release has exact unique original sources, two accounts and finite clocks"
            )
        return self


def goal_release_effect_hash(effect: GoalReleaseEffect) -> str:
    original = GoalReleaseEffect.model_validate(effect.model_dump())
    return configuration_hash(original.model_dump(mode="json"))


class GoalReleaseBankCommand(BoundaryModel):
    protocol: Literal["full-goal-release-bank-v1"] = "full-goal-release-bank-v1"
    effect: GoalReleaseEffect
    effect_hash: Hash

    @model_validator(mode="after")
    def original_effect_hash(self) -> Self:
        if self.effect_hash != goal_release_effect_hash(self.effect):
            raise ValueError("Dedicated release hash must bind its entire original effect")
        return self


def validate_release_authorization_binding(
    command: GoalReleaseBankCommand, authorization: GoalReleaseAuthorization, now: datetime
) -> None:
    """Check immutable scope only; current original DB status must be checked separately."""
    _aware(now)
    effect, scope = command.effect, authorization.scope
    goal = next((row for row in scope.source_goals if row.goal_id == effect.source_goal_id), None)
    if (
        effect.user_id != authorization.user_id
        or effect.epoch_id != authorization.epoch_id
        or effect.policy_id != authorization.policy_id
        or effect.policy_version_id != authorization.policy_version_id
        or effect.policy_configuration_hash != scope.policy_configuration_hash
        or effect.authorization_id != authorization.authorization_id
        or effect.authorization_scope_hash != authorization.scope_hash
        or effect.authorization_request_hash != authorization.request_hash
        or not authorization.confirmed_at <= now < authorization.valid_until
        or not effect.valid_from <= now < effect.expires_at <= scope.valid_until
        or not scope.valid_from <= effect.valid_from
        or not set(effect.emergency_conditions) <= set(scope.emergency_conditions)
        or effect.amount_cents > scope.single_action_cap_cents
        or goal is None
        or (
            effect.original_goal_policy_id,
            effect.original_goal_policy_version_id,
            effect.full_model_evidence_id,
            effect.full_model_evidence_hash,
            effect.full_configuration_hash,
            effect.minimum_guarantee_cents,
        )
        != (
            goal.original_policy_id,
            goal.original_policy_version_id,
            goal.full_model_evidence_id,
            goal.full_model_evidence_hash,
            goal.full_configuration_hash,
            goal.minimum_guarantee_cents,
        )
    ):
        raise ValueError("Release economics do not bind the original finite dedicated consent")


class GoalReleaseLegSpec(BoundaryModel):
    leg_ref: str
    ledger_key: str
    ledger_dimension: Literal["ECONOMIC", "GOAL_OWNERSHIP"]
    account_id: UUID
    delta_cents: StrictInt
    required_metadata: dict[str, str]


def goal_release_leg_specs(command: GoalReleaseBankCommand) -> tuple[GoalReleaseLegSpec, ...]:
    effect = command.effect
    return (
        GoalReleaseLegSpec(
            leg_ref=f"cash:{effect.source_account_id}",
            ledger_key=f"CASH:{effect.source_account_id}",
            ledger_dimension="ECONOMIC",
            account_id=effect.source_account_id,
            delta_cents=-effect.amount_cents,
            required_metadata={},
        ),
        GoalReleaseLegSpec(
            leg_ref=f"cash:{effect.destination_account_id}",
            ledger_key=f"CASH:{effect.destination_account_id}",
            ledger_dimension="ECONOMIC",
            account_id=effect.destination_account_id,
            delta_cents=effect.amount_cents,
            required_metadata={},
        ),
        GoalReleaseLegSpec(
            leg_ref="goal_cash",
            ledger_key=f"GOAL_CASH:{effect.source_goal_id}",
            ledger_dimension="GOAL_OWNERSHIP",
            account_id=effect.source_account_id,
            delta_cents=-effect.amount_cents,
            required_metadata={
                "goal_id": str(effect.source_goal_id),
                "account_id": str(effect.source_account_id),
            },
        ),
    )


class GoalReleasePostingOriginal(BoundaryModel):
    posting_id: UUID
    user_id: UUID
    operation_id: UUID
    ledger_key: str
    ledger_dimension: str
    ledger_metadata: dict[str, Any]
    leg_ref: str
    account_id: UUID
    position_id: Literal[None] = None
    redemption_id: Literal[None] = None
    external_fact_id: Literal[None] = None
    previous_posting_id: UUID
    sequence_number: Annotated[StrictInt, Field(gt=1)]
    entry_kind: str
    balance_before_cents: Cents
    delta_cents: StrictInt
    balance_after_cents: Cents
    occurred_at: datetime
    original_row_hash: Hash


class GoalReleaseSettlementOriginal(BoundaryModel):
    command: GoalReleaseBankCommand
    bank_operation_id: UUID
    action_plan_id: UUID
    user_id: UUID
    operation_type: Literal["RELEASE_GOAL"] = "RELEASE_GOAL"
    request_hash: Hash
    business_key: str
    idempotency_key: str
    status: Literal["ACCEPTED", "UNKNOWN", "SETTLED", "REJECTED"]
    requested_at: datetime
    available_at: datetime
    settled_at: datetime | None
    postings: list[GoalReleasePostingOriginal]
    original_bank_row_hash: Hash


def validate_goal_release_settlement(
    original: GoalReleaseSettlementOriginal, now: datetime
) -> None:
    """Local original identity and exact three legs; whole ledger proof stays mandatory."""
    _validate_original_bank_identity(original, now)
    command, effect = original.command, original.command.effect
    if original.status != "SETTLED":
        raise ValueError("A non-settled bank result does not prove three economic legs")
    if original.settled_at is None or not original.available_at <= original.settled_at <= now:
        raise ValueError("Actual original bank settlement time is missing or unknown")
    expected = {row.leg_ref: row for row in goal_release_leg_specs(command)}
    if len(original.postings) != 3 or {row.leg_ref for row in original.postings} != set(expected):
        raise ValueError(
            "Release requires exactly its original three legs, without income or principal legs"
        )
    for posting in original.postings:
        spec = expected[posting.leg_ref]
        if (
            posting.posting_id != uuid5(effect.operation_id, "posting:" + posting.leg_ref)
            or posting.user_id != effect.user_id
            or posting.operation_id != effect.operation_id
            or posting.ledger_key != spec.ledger_key
            or posting.ledger_dimension != spec.ledger_dimension
            or posting.account_id != spec.account_id
            or posting.delta_cents != spec.delta_cents
            or posting.entry_kind == "OPENING"
            or posting.occurred_at != original.settled_at
            or posting.balance_before_cents + posting.delta_cents != posting.balance_after_cents
            or any(
                posting.ledger_metadata.get(key) != value
                for key, value in spec.required_metadata.items()
            )
        ):
            raise ValueError(
                "Actual release posting changed owner, original identity, amount, or scope"
            )
    if sum(row.delta_cents for row in original.postings if row.ledger_dimension == "ECONOMIC") != 0:
        raise ValueError("Economic cash legs must conserve separately from ownership")


def _validate_original_bank_identity(
    original: GoalReleaseSettlementOriginal, now: datetime
) -> None:
    _aware(now)
    command, effect = original.command, original.command.effect
    if (
        original.bank_operation_id != original.action_plan_id
        or original.bank_operation_id != effect.operation_id
        or original.user_id != effect.user_id
        or original.request_hash != configuration_hash(command.model_dump(mode="json"))
        or original.business_key != effect.business_key
        or original.idempotency_key != effect.bank_idempotency_key
        or not effect.valid_from <= original.requested_at < effect.expires_at
        or not original.requested_at <= original.available_at <= now
    ):
        raise ValueError("Original release bank identity or request hash does not match")


def _aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Original observation time must be timezone-aware")


def _validate_without_settlement(original: GoalReleaseSettlementOriginal, now: datetime) -> None:
    _validate_original_bank_identity(original, now)
    if original.postings or original.settled_at is not None:
        raise ValueError("Unsettled or rejected release has unexplained economic legs")


class GoalReleaseResidual(BoundaryModel):
    allocation_action_id: UUID
    fragment_id: UUID
    original_allocated_cents: Cents
    bank_released_cents: Cents | None
    cash_remaining_cents: Cents | None


class GoalReleaseResidualResult(BoundaryModel):
    state: Literal["VERIFIED", "UNKNOWN"]
    remaining: list[GoalReleaseResidual]
    current_goal_cash_cents: Cents | None
    exactly_attributed_goal_cash_cents: Cents | None
    bank_released_cents: Cents | None
    original_assigned_unchanged: bool
    reasons: list[str]
    source_binding_hash: Hash
    bank_authority: Literal[False] = False
    funds_released: Literal[False] = False
    available_income_increase_cents: Literal[0] = 0
    assigned_income_decrease_cents: Literal[0] = 0


def replay_goal_release_residuals(
    basis: GoalCashSourceProof,
    originals: list[GoalReleaseSettlementOriginal],
    *,
    actual_release_operation_count: int | None,
    whole_bank_source_verified: bool,
    current_goal_cash_cents: int | None,
    current_goal_principal_cents: int | None,
    current_assigned_by_fragment: dict[UUID, int],
    current_source_binding_hash: str,
    now: datetime,
) -> GoalReleaseResidualResult:
    """A retained cash-only basis plus freshly verified complete release originals.

    Root must prove that the allocation basis still covers all current allocation
    originals. This function must not promote an UNKNOWN V1 source proof or invent
    a cash/principal split. Whole-bank and inventory flags are internal verified
    producer inputs, never client controls.
    """
    reasons: list[str] = []
    _aware(now)
    if basis.state != "VERIFIED_CASH_ONLY" or basis.as_of > now:
        reasons.append("ORIGINAL_CASH_ONLY_ALLOCATION_BASIS_NOT_VERIFIED")
    if (
        not whole_bank_source_verified
        or type(actual_release_operation_count) is not int
        or actual_release_operation_count != len(originals)
        or len({row.bank_operation_id for row in originals}) != len(originals)
    ):
        reasons.append("CURRENT_COMPLETE_RELEASE_BANK_SOURCE_NOT_VERIFIED")
    if (
        type(current_goal_cash_cents) is not int
        or current_goal_cash_cents < 0
        or type(current_goal_principal_cents) is not int
        or current_goal_principal_cents < 0
        or len(current_source_binding_hash) != 64
        or any(char not in "0123456789abcdef" for char in current_source_binding_hash)
    ):
        reasons.append("CURRENT_BANK_GOAL_OR_SOURCE_BINDING_MISSING")
    assigned = {row.fragment_id: row.original_assigned_cents for row in basis.sources}
    assigned_ok = assigned == current_assigned_by_fragment
    assigned_ok = assigned_ok and all(
        type(value) is int and value >= 0 for value in current_assigned_by_fragment.values()
    )
    if not assigned_ok:
        reasons.append("ORIGINAL_COMPLETE_ASSIGNED_DENOMINATOR_CHANGED")
    if current_goal_principal_cents != basis.original_goal_principal_cents:
        reasons.append("CURRENT_GOAL_PRINCIPAL_CHANGED_OR_UNVERIFIED")
    by_slice = {
        (row.allocation_action_id, row.fragment_id): row for row in basis.original_allocation_slices
    }
    consumed: dict[tuple[UUID, UUID], int] = {}
    total = 0
    for original in originals:
        effect = original.command.effect
        if effect.user_id != basis.user_id or effect.epoch_id != basis.epoch_id:
            reasons.append("RELEASE_OWNER_OR_EPOCH_MISMATCH")
        try:
            if original.status == "SETTLED":
                validate_goal_release_settlement(original, now)
            elif original.status in {"REJECTED", "ACCEPTED"}:
                _validate_without_settlement(original, now)
            else:
                _validate_original_bank_identity(original, now)
        except ValueError:
            reasons.append("RELEASE_ORIGINAL_SETTLEMENT_NOT_VERIFIED")
            continue
        if effect.source_goal_id != basis.goal_id:
            continue
        if effect.source_account_id != basis.goal_account_id:
            reasons.append("RELEASE_SOURCE_GOAL_ACCOUNT_MISMATCH")
        if original.status == "REJECTED":
            continue
        if original.status != "SETTLED":
            reasons.append("SOURCE_GOAL_RELEASE_KEY_NOT_SETTLED")
            continue
        total += effect.amount_cents
        for use in effect.release_uses:
            key = (use.allocation_action_id, use.fragment_id)
            source = by_slice.get(key)
            if source is None or (
                use.origin_transaction_id,
                use.income_location_account_id,
                use.original_policy_version_id,
                use.allocation_effect_hash,
                use.allocation_bank_request_hash,
                use.allocation_action_request_hash,
            ) != (
                source.origin_transaction_id,
                source.income_location_account_id,
                source.original_policy_version_id,
                source.allocation_effect_hash,
                source.allocation_bank_request_hash,
                source.allocation_action_request_hash,
            ):
                reasons.append("RELEASE_USES_DO_NOT_BIND_ORIGINAL_ALLOCATION_SLICE")
                continue
            consumed[key] = consumed.get(key, 0) + use.amount_cents
            if consumed[key] > source.original_allocated_cents:
                reasons.append("ORIGINAL_ALLOCATION_SLICE_RELEASED_TWICE_OR_OVERDRAWN")
    expected_cash = (
        basis.exactly_attributed_goal_cash_cents - total
        if basis.exactly_attributed_goal_cash_cents is not None
        else None
    )
    if expected_cash is None or expected_cash < 0 or current_goal_cash_cents != expected_cash:
        reasons.append("CURRENT_GOAL_CASH_DOES_NOT_MATCH_COMPLETE_RELEASE_RESIDUALS")
    verified = not reasons
    return GoalReleaseResidualResult(
        state="VERIFIED" if verified else "UNKNOWN",
        remaining=[
            GoalReleaseResidual(
                allocation_action_id=source.allocation_action_id,
                fragment_id=source.fragment_id,
                original_allocated_cents=source.original_allocated_cents,
                bank_released_cents=consumed.get(key, 0) if verified else None,
                cash_remaining_cents=source.original_allocated_cents - consumed.get(key, 0)
                if verified
                else None,
            )
            for key, source in sorted(by_slice.items())
        ],
        current_goal_cash_cents=current_goal_cash_cents,
        exactly_attributed_goal_cash_cents=expected_cash if verified else None,
        bank_released_cents=total if verified else None,
        original_assigned_unchanged=assigned_ok,
        reasons=list(dict.fromkeys(reasons)),
        source_binding_hash=configuration_hash(
            {
                "basis": basis.model_dump(mode="json"),
                "originals": [row.model_dump(mode="json") for row in originals],
                "actual_release_operation_count": actual_release_operation_count,
                "whole_bank_source_verified": whole_bank_source_verified,
                "as_of": now.isoformat(),
                "current_goal_cash_cents": current_goal_cash_cents,
                "current_goal_principal_cents": current_goal_principal_cents,
                "current_assigned_by_fragment": {
                    str(key): value for key, value in current_assigned_by_fragment.items()
                },
                "current_source_binding_hash": current_source_binding_hash,
            }
        ),
    )


class GoalReleasePolicyUsage(BoundaryModel):
    policy_id: UUID
    state: Literal["VERIFIED", "UNKNOWN"]
    settled_cents: Cents | None
    accepted_reserved_cents: Cents | None
    cap_occupied_cents: Cents | None
    actual_operation_count: Cents | None
    captured_operation_count: Cents
    pending_operation_ids: list[UUID]
    accepted_operation_ids: list[UUID]
    reasons: list[str]
    source_binding_hash: Hash
    cumulative_scope: Literal["POLICY_ID_ALL_VERSIONS"] = "POLICY_ID_ALL_VERSIONS"
    bank_authority: Literal[False] = False
    service_receipt_or_economic_success_claimed: Literal[False] = False


def compute_release_policy_usage(
    user_id: UUID,
    policy_id: UUID,
    originals: list[GoalReleaseSettlementOriginal],
    *,
    actual_release_operation_count: int | None,
    whole_bank_source_verified: bool,
    current_source_binding_hash: str,
    now: datetime,
) -> GoalReleasePolicyUsage:
    """All user release originals, all versions of one policy; missing stays null.

    Three settled bank legs consume permission even when application projection
    or the service response has failed. A pending original key is not terminal
    failure and cannot be silently excluded to restore cap capacity.
    """
    reasons: list[str] = []
    _aware(now)
    pending: list[UUID] = []
    accepted: list[UUID] = []
    total = 0
    reserved = 0
    complete = (
        type(actual_release_operation_count) is int
        and actual_release_operation_count == len(originals)
        and len({row.bank_operation_id for row in originals}) == len(originals)
    )
    if not complete or not whole_bank_source_verified:
        reasons.append("COMPLETE_CURRENT_RELEASE_BANK_INVENTORY_NOT_VERIFIED")
    if len(current_source_binding_hash) != 64 or any(
        char not in "0123456789abcdef" for char in current_source_binding_hash
    ):
        reasons.append("CURRENT_SOURCE_BINDING_MISSING")
    for original in originals:
        effect = original.command.effect
        if original.user_id != user_id or effect.user_id != user_id:
            reasons.append("CURRENT_RELEASE_INVENTORY_OWNER_MISMATCH")
        try:
            _validate_original_bank_identity(original, now)
            if original.status == "SETTLED":
                validate_goal_release_settlement(original, now)
                if effect.policy_id == policy_id:
                    total += effect.amount_cents
            elif original.status in {"REJECTED", "ACCEPTED"}:
                _validate_without_settlement(original, now)
                if original.status == "ACCEPTED" and effect.policy_id == policy_id:
                    reserved += effect.amount_cents
                    accepted.append(original.bank_operation_id)
            else:
                if effect.policy_id == policy_id:
                    pending.append(original.bank_operation_id)
                    reasons.append("ORIGINAL_RELEASE_KEY_UNRESOLVED")
        except ValueError:
            reasons.append("ORIGINAL_RELEASE_BANK_RESULT_NOT_VERIFIED")
    verified = not reasons
    return GoalReleasePolicyUsage(
        policy_id=policy_id,
        state="VERIFIED" if verified else "UNKNOWN",
        settled_cents=total if verified else None,
        accepted_reserved_cents=reserved if verified else None,
        cap_occupied_cents=total + reserved if verified else None,
        actual_operation_count=actual_release_operation_count if complete else None,
        captured_operation_count=len(originals),
        pending_operation_ids=sorted(pending),
        accepted_operation_ids=sorted(accepted),
        reasons=list(dict.fromkeys(reasons)),
        source_binding_hash=configuration_hash(
            {
                "user_id": str(user_id),
                "policy_id": str(policy_id),
                "as_of": now.isoformat(),
                "originals": [row.model_dump(mode="json") for row in originals],
                "actual_release_operation_count": actual_release_operation_count,
                "whole_bank_source_verified": whole_bank_source_verified,
                "current_source_binding_hash": current_source_binding_hash,
            }
        ),
    )
