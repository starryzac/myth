"""Server-only dynamic contributions inside the original confirmed MVP range.

The proof is a deterministic supplement to the original goal amount check. It
does not replace source, ownership, reservation, confirmation or bank checks.
"""

import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.decision_trace_types import DecisionTrace
from app.domain.dynamic_goal_reserve import (
    DynamicGoalReserveInput,
    DynamicGoalReserveResult,
    compute_dynamic_goal_reserve,
)
from app.domain.execution_types import CashUse, ExecutionContext, ExecutionEffect
from app.domain.full_policy_configuration import LongTermGoalPolicy
from app.domain.full_protection_projection import (
    FullProtectionPolicySource,
    FullProtectionProjectionInput,
    project_full_protection,
)
from app.domain.income_ledger import IncomeLedger, IncomeUse
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    AllocationIncomeLot,
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
)
from app.domain.policy_configuration import (
    UUIDReference,
    configuration_hash,
    validate_configuration,
)
from pydantic import BaseModel, Field, StrictStr, field_validator

ALGORITHM: Literal["full-dynamic-goal-execution-v1"] = "full-dynamic-goal-execution-v1"
MARKER = "full_dynamic_goal_execution"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(min_length=1, max_length=120)]


class FullDynamicGoalPrepareRequest(BoundaryModel):
    goal_id: UUIDReference
    expected_policy_version_id: UUIDReference
    expected_model_evidence_id: UUIDReference
    expected_model_evidence_hash: Hash
    expected_epoch_id: UUIDReference
    idempotency_key: Key

    @field_validator("idempotency_key")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The original key must not be blank")
        return value


def native_income_original_matches(raw: dict[str, Any], expected: IncomeLedger) -> bool:
    """Compare native V2 values without replacing the original JSON or its hash.

    Original bank observations retain their datetime spelling. JSON parsing
    normalizes Z and +00:00 to the same instant; all amounts, identities,
    locations, reservations and protocol fields must still match exactly.
    Legacy V1 data is not a native V2 original.
    """
    try:
        original = IncomeLedger.model_validate_json(json.dumps(raw))
    except (ValueError, TypeError, OverflowError):
        return False
    return original == expected


class FullDynamicGoalInput(BoundaryModel):
    """Private actual-source input; never accepted by an HTTP request."""

    context: ExecutionContext
    request: FullDynamicGoalPrepareRequest
    epoch_id: UUID
    model_original: dict[str, Any]
    model_evidence_id: UUID
    model_evidence_hash: Hash
    income: IncomeLedger
    income_evidence_id: UUID
    income_evidence_hash: Hash
    source_refs: list[SourceReference]
    protection_policies: list[FullProtectionPolicySource]
    protection_inventory_complete: Literal[True]
    protection_issues: list[str] = Field(default_factory=list)
    own_effect: ExecutionEffect | None = None


class FullDynamicGoalProof(BoundaryModel):
    protocol: Literal["full-dynamic-goal-execution-v1"] = ALGORITHM
    simulation: Literal[True] = True
    bank_authority: Literal[False] = False
    preserves_original_permission_checks: Literal[True] = True
    user_id: UUID
    goal_id: UUID
    epoch_id: UUID
    policy_version_id: UUID
    model_evidence_id: UUID
    model_evidence_hash: Hash
    as_of: datetime
    status: Literal["VERIFIED_RANGE", "BLOCKED", "UNKNOWN"]
    minimum_cents: int | None
    dynamic_cap_cents: int | None
    remaining_max_cents: int | None
    nominal_remaining_target_cents: int | None
    context_hash: Hash
    input_hash: Hash
    full_projection_input_hash: Hash | None
    effect_hash: Hash | None
    proof_hash: Hash
    reserve: DynamicGoalReserveResult | None
    reasons: list[str]
    # Same-invocation validation must recompute this actual typed input. It is
    # deliberately excluded from the compact Action.request/HTTP proof.
    inputs: FullDynamicGoalInput = Field(exclude=True)


def _digest(value: BaseModel) -> str:
    return configuration_hash(value.model_dump(mode="json"))


def _effect_hash(effect: ExecutionEffect) -> str:
    from app.domain.execution import execution_effect_hash

    return execution_effect_hash(effect)


def _reserved(context: ExecutionContext) -> dict[UUID, int]:
    from app.domain.execution import _reservation_amounts

    return _reservation_amounts(context)[0]


def _originals(data: FullDynamicGoalInput) -> tuple[AllocationGoal, list[AllocationIncomeLot]]:
    context, body = data.context, data.request
    now = context.snapshot.as_of
    raw = data.model_original
    if (
        configuration_hash(raw) != data.model_evidence_hash
        or (body.expected_model_evidence_id, body.expected_model_evidence_hash)
        != (data.model_evidence_id, data.model_evidence_hash)
        or body.expected_epoch_id != data.epoch_id
        or raw.get("protocol") != "full-goal-model-v1"
        or raw.get("accepted") is not True
        or raw.get("user_id") != str(context.user_id)
        or raw.get("epoch_id") != str(data.epoch_id)
        or raw.get("goal_id") != str(body.goal_id)
        or raw.get("base_policy_version_id") != str(body.expected_policy_version_id)
    ):
        raise ValueError("ORIGINAL_MODEL_IDENTITY_OR_HASH_MISMATCH")
    full = LongTermGoalPolicy.model_validate_json(json.dumps(raw["full_configuration"])).model_dump(
        mode="json"
    )
    version = next(
        (row for row in context.versions if row.version_id == body.expected_policy_version_id), None
    )
    if version is None or raw.get("policy_id") != str(version.policy_id):
        raise ValueError("EXACT_ORIGINAL_MVP_VERSION_REQUIRED")
    base = validate_configuration(version.configuration)
    if (
        base["type"] != "goal_saving"
        or version.content_hash != configuration_hash(base)
        or raw.get("base_hash") != version.content_hash
        or raw.get("full_hash") != configuration_hash(full)
        or raw.get("reviewed") != {"base_hash": raw["base_hash"], "full_hash": raw["full_hash"]}
        or full["cross_goal_reallocation_allowed"] is not False
    ):
        raise ValueError("EXACT_ORIGINAL_DOUBLE_HASH_REQUIRED")
    projected_base = validate_configuration(
        {
            "type": "goal_saving",
            "name": full["name"],
            "valid_from": full["valid_from"],
            "valid_until": full["valid_until"],
            "target_cents": full["target_cents"],
            "deadline": full["deadline"],
            "monthly_contribution": full["monthly_contribution"],
            "priority": {
                "importance": full["importance"],
                "minimum_cents": full["minimum_guarantee_cents"],
                "reducible": full["allow_partial"],
                "deferrable": full["allow_deferral"],
            },
            "cross_goal_reallocation_allowed": False,
            "asset_policy_id": full["asset_policy_id"],
        }
    )
    if (
        projected_base != base
        or datetime.fromisoformat(raw["confirmed_at"]) != version.confirmed_at
    ):
        raise ValueError("CURRENT_MODEL_AND_ORIGINAL_GOAL_RANGE_DIFFER")
    if not max(version.confirmed_at, version.valid_from) <= now or (
        version.valid_until is not None and now >= version.valid_until
    ):
        raise ValueError("ORIGINAL_MVP_WINDOW_NOT_CURRENT")
    ownerships = [row for row in context.snapshot.goals if row.goal_id == body.goal_id]
    period = now.astimezone(ZoneInfo(context.snapshot.timezone)).strftime("%Y-%m")
    months = [
        row
        for row in context.snapshot.goal_month_contributions
        if row.goal_id == body.goal_id and row.period == period
    ]
    refs = {row.evidence_id: row for row in data.source_refs}
    if (
        len(refs) != len(data.source_refs)
        or any(row.user_id != context.user_id for row in refs.values())
        or len(ownerships) != 1
        or len(months) != 1
        or ownerships[0].policy_id != version.policy_id
        or ownerships[0].account_id is None
        or refs.get(data.model_evidence_id) is None
        or refs[data.model_evidence_id].content_hash != data.model_evidence_hash
        or refs.get(data.income_evidence_id) is None
        or refs[data.income_evidence_id].content_hash != data.income_evidence_hash
        or not ownerships[0].evidence_ids
        or not months[0].evidence_ids
        or any(
            identity not in refs
            for identity in (
                *ownerships[0].evidence_ids,
                *months[0].evidence_ids,
                *version.evidence_ids,
            )
        )
    ):
        raise ValueError("COMPLETE_ORIGINAL_OWNERSHIP_MONTH_AND_SOURCES_REQUIRED")
    owned = ownerships[0]
    account = next(
        (row for row in context.snapshot.cash_accounts if row.account_id == owned.account_id), None
    )
    if account is None or account.account_type not in {"CASH", "GOAL"}:
        raise ValueError("ORIGINAL_GOAL_DESTINATION_NOT_KNOWN")
    goal = AllocationGoal(
        goal_id=body.goal_id,
        account_id=account.account_id,
        policy_id=version.policy_id,
        effective_policy_version_id=version.version_id,
        policy_status="ACTIVE",
        target_cents=full["target_cents"],
        current_owned_cents=owned.allocated_cents,
        current_month_contributed_cents=months[0].contributed_cents,
        monthly_min_cents=base["monthly_contribution"]["min_cents"],
        monthly_target_cents=base["monthly_contribution"]["target_cents"],
        monthly_max_cents=base["monthly_contribution"]["max_cents"],
        minimum_guarantee_cents=full["minimum_guarantee_cents"],
        importance=full["importance"],
        deadline=LongTermGoalPolicy.model_validate_json(json.dumps(full)).deadline,
        allow_partial=full["allow_partial"],
        allow_deferral=full["allow_deferral"],
        deferral_cost_cents_per_day=full["deferral_cost_cents_per_day"],
        confirmed_at=version.confirmed_at,
        valid_from=version.valid_from,
        valid_until=version.valid_until,
        source_refs=list(refs.values()),
    )
    ledger = IncomeLedger.model_validate(data.income.model_dump())
    if ledger.user_id != context.user_id or ledger.as_of > now:
        raise ValueError("ORIGINAL_INCOME_OWNER_OR_TIME_DIFFERS")
    restored: dict[UUID, int] = {}
    if data.own_effect is not None:
        original = data.own_effect
        reservation = next(
            (row for row in ledger.reservations if row.action_id == original.operation_id), None
        )
        if reservation is not None:
            if reservation.operation != "ALLOCATE_GOAL" or {
                _digest(row) for row in reservation.uses
            } != {_digest(row) for row in original.income_uses}:
                raise ValueError("ONLY_EXACT_OWN_ORIGINAL_RESERVATION_MAY_BE_RESTORED")
            if reservation.state == "RESERVED":
                restored = {row.fragment_id: row.amount_cents for row in reservation.uses}
            elif reservation.state == "COMMITTED":
                raise ValueError("SETTLED_ORIGINAL_KEY_REQUIRES_RECOVERY_NOT_NEW_ACCEPTANCE")
    origins = {row.origin_transaction_id: row for row in ledger.origins}
    fragments = {row.fragment_id: row for row in ledger.fragments}
    actual_lots = {row.fragment_id: row for row in context.lots}
    if len(actual_lots) != len(context.lots) or set(actual_lots) != set(fragments):
        raise ValueError("COMPLETE_INCOME_FRAGMENT_DENOMINATOR_DIFFERS")
    lots = []
    cash_reserved = _reserved(context)
    per_account = {
        row.account_id: max(
            0,
            row.balance_cents
            - sum(
                g.cash_owned_cents for g in context.snapshot.goals if g.account_id == row.account_id
            )
            - cash_reserved.get(row.account_id, 0),
        )
        for row in context.snapshot.cash_accounts
        if row.account_type == "CASH"
    }
    for fragment in sorted(
        fragments.values(),
        key=lambda row: (
            origins[row.origin_transaction_id].occurred_at,
            str(row.origin_transaction_id),
            str(row.account_id),
            str(row.fragment_id),
        ),
    ):
        origin = origins[fragment.origin_transaction_id]
        actual = actual_lots[fragment.fragment_id]
        available = fragment.available_cents + restored.get(fragment.fragment_id, 0)
        if (
            (
                actual.origin_transaction_id,
                actual.account_id,
                actual.amount_cents,
                actual.available_cents,
                actual.occurred_at,
                actual.observed_at,
            )
            != (
                origin.origin_transaction_id,
                fragment.account_id,
                origin.amount_cents,
                available,
                origin.occurred_at,
                origin.observed_at,
            )
            or refs.get(origin.bank_evidence_id) is None
            or refs[origin.bank_evidence_id].content_hash != origin.bank_evidence_hash
            or not {data.income_evidence_id, origin.bank_evidence_id}.issubset(actual.evidence_ids)
        ):
            raise ValueError("ACTUAL_INCOME_LOCATION_OR_BANK_SOURCE_DIFFERS")
        if origin.occurred_at < max(version.confirmed_at, version.valid_from):
            continue
        usable = min(available, per_account.get(fragment.account_id, 0))
        per_account[fragment.account_id] = per_account.get(fragment.account_id, 0) - usable
        if usable:
            lots.append(
                AllocationIncomeLot(
                    fragment_id=fragment.fragment_id,
                    origin_transaction_id=origin.origin_transaction_id,
                    account_id=fragment.account_id,
                    received_cents=origin.amount_cents,
                    available_cents=usable,
                    occurred_at=origin.occurred_at,
                    observed_at=origin.observed_at,
                    bank_evidence_id=origin.bank_evidence_id,
                    bank_evidence_hash=origin.bank_evidence_hash,
                    source_refs=[refs[data.income_evidence_id], refs[origin.bank_evidence_id]],
                )
            )
    return goal, lots


def derive_full_dynamic_goal_proof(
    data: FullDynamicGoalInput, effect: ExecutionEffect | None = None
) -> FullDynamicGoalProof:
    data = FullDynamicGoalInput.model_validate(data.model_dump())
    context, body = data.context, data.request
    reasons: list[str] = []
    reserve = None
    minimum = cap = maximum = nominal = None
    projection_hash = None
    status: Literal["VERIFIED_RANGE", "BLOCKED", "UNKNOWN"] = "UNKNOWN"
    try:
        if context.source_issues or context.snapshot.source_issues or data.protection_issues:
            raise ValueError("CURRENT_SOURCES_NOT_FULLY_VERIFIED")
        goal, lots = _originals(data)
        today = context.snapshot.as_of.astimezone(ZoneInfo(context.snapshot.timezone)).date()
        if today > goal.deadline:
            raise ValueError("ORIGINAL_MVP_DEADLINE_PASSED")
        cash_reserved = _reserved(context)
        full_input = FullProtectionProjectionInput(
            snapshot=context.snapshot,
            boundary_versions=context.versions,
            positions=context.positions,
            boundary_products=context.boundary_products,
            policies=data.protection_policies,
            reserved_cash_by_account=cash_reserved,
            full_source_inventory_complete=data.protection_inventory_complete,
            full_source_issues=data.protection_issues,
        )
        projection = project_full_protection(full_input)
        projection_hash = projection.input_hash
        curve = projection.full_annual_projection
        if projection.status != "READY" or curve is None or len(curve.calculation_trace) != 1098:
            raise ValueError("FULL_365_ALL_1098_HARD_POINTS_NOT_READY")
        refs = list(data.source_refs)
        points = [
            HardProtectionPoint(
                date=row.date,
                cash_cents=row.cash_cents,
                obligation_floor_cents=row.protected_cents_by_reason.get("obligations", 0),
                living_floor_cents=row.protected_cents_by_reason.get("living", 0),
                emergency_floor_cents=row.protected_cents_by_reason.get("emergency", 0),
                owned_goal_cash_cents=row.protected_cents_by_reason.get("goal_cash", 0),
                other_protection_floor_cents=sum(row.protected_cents_by_reason.values())
                - sum(
                    row.protected_cents_by_reason.get(name, 0)
                    for name in ("obligations", "living", "emergency", "goal_cash")
                ),
                source_refs=refs,
            )
            for row in curve.calculation_trace
        ]
        facts = MultiGoalAllocationInput(
            user_id=context.user_id,
            as_of=context.snapshot.as_of,
            timezone=context.snapshot.timezone,
            income_lots=lots,
            hard_protection_points=points,
            goals=[goal],
        )
        reserve = compute_dynamic_goal_reserve(
            DynamicGoalReserveInput(
                facts=facts,
                contribution_period=context.snapshot.as_of.astimezone(
                    ZoneInfo(context.snapshot.timezone)
                ).strftime("%Y-%m"),
            )
        )
        remaining = max(0, goal.target_cents - goal.current_owned_cents)
        contributed = goal.current_month_contributed_cents
        minimum = min(remaining, max(0, goal.monthly_min_cents - contributed))
        maximum = min(remaining, max(0, goal.monthly_max_cents - contributed))
        nominal = min(remaining, max(0, goal.monthly_target_cents - contributed))
        cap = reserve.suggested_additional_cents
        status = "BLOCKED"
        if reserve.status != "READY" or cap is None or not 0 < cap <= maximum or cap < minimum:
            reasons.append("READ_ONLY_OVERDUE_PARTIAL_OR_ZERO_IS_NOT_EXECUTABLE")
        else:
            status = "VERIFIED_RANGE"
            if effect is not None:
                _fixed_effect(data, goal, lots, effect, minimum, cap)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reasons.append(str(error))
        status = "UNKNOWN" if reserve is None else "BLOCKED"
    fields: dict[str, Any] = {
        "user_id": context.user_id,
        "goal_id": body.goal_id,
        "epoch_id": data.epoch_id,
        "policy_version_id": body.expected_policy_version_id,
        "model_evidence_id": data.model_evidence_id,
        "model_evidence_hash": data.model_evidence_hash,
        "as_of": context.snapshot.as_of,
        "status": status,
        "minimum_cents": minimum,
        "dynamic_cap_cents": cap,
        "remaining_max_cents": maximum,
        "nominal_remaining_target_cents": nominal,
        "context_hash": _digest(context),
        "input_hash": _digest(data),
        "full_projection_input_hash": projection_hash,
        "effect_hash": _effect_hash(effect) if effect is not None else None,
        "reserve": reserve,
        "reasons": reasons,
    }
    temporary = FullDynamicGoalProof(**fields, proof_hash="0" * 64, inputs=data)
    digest = configuration_hash(temporary.model_dump(mode="json", exclude={"proof_hash"}))
    return temporary.model_copy(update={"proof_hash": digest})


def _fixed_effect(
    data: FullDynamicGoalInput,
    goal: AllocationGoal,
    lots: list[AllocationIncomeLot],
    effect: ExecutionEffect,
    minimum: int,
    cap: int,
) -> None:
    now = data.context.snapshot.as_of
    if (
        effect.user_id != data.context.user_id
        or effect.action_type != "ALLOCATE_GOAL"
        or effect.goal_id != goal.goal_id
        or effect.policy_id != goal.policy_id
        or effect.policy_version_id != goal.effective_policy_version_id
        or effect.policy_version_ids != [goal.effective_policy_version_id]
        or effect.destination_account_id != goal.account_id
        or not effect.valid_from <= now < effect.expires_at
        or not max(1, minimum) <= effect.amount_cents <= cap
        or sum(row.amount_cents for row in effect.income_uses) != effect.amount_cents
    ):
        raise ValueError("FIXED_ORIGINAL_EFFECT_OUTSIDE_CURRENT_DYNAMIC_RANGE")
    # Recheck the frozen uses against original current fragments, not the new
    # greedy selection used to construct a fresh suggestion. Qualified sources
    # may change their order without changing the original economic command.
    by_fragment = {row.fragment_id: row for row in data.context.lots}
    version = next(
        row for row in data.context.versions if row.version_id == goal.effective_policy_version_id
    )
    uses_by_account: dict[UUID, int] = {}
    for use in effect.income_uses:
        lot = by_fragment.get(use.fragment_id)
        if (
            lot is None
            or (use.origin_transaction_id, use.account_id)
            != (lot.origin_transaction_id, lot.account_id)
            or use.amount_cents > lot.available_cents
            or lot.occurred_at < max(version.confirmed_at, version.valid_from)
        ):
            raise ValueError("FIXED_ORIGINAL_INCOME_USES_NOT_CURRENTLY_AVAILABLE")
        uses_by_account[use.account_id] = uses_by_account.get(use.account_id, 0) + use.amount_cents
    if uses_by_account != {row.account_id: row.amount_cents for row in effect.cash_uses}:
        raise ValueError("FIXED_ORIGINAL_CASH_AND_INCOME_SOURCES_DIFFER")
    reserved = _reserved(data.context)
    for identity, amount in uses_by_account.items():
        account = next(
            (
                row
                for row in data.context.snapshot.cash_accounts
                if row.account_id == identity and row.account_type == "CASH"
            ),
            None,
        )
        owned = sum(
            row.cash_owned_cents
            for row in data.context.snapshot.goals
            if row.account_id == identity
        )
        if account is None or amount > account.balance_cents - owned - reserved.get(identity, 0):
            raise ValueError("FIXED_ORIGINAL_SOURCE_CASH_NOT_AVAILABLE")


def build_full_dynamic_goal_effect(
    data: FullDynamicGoalInput, action_id: UUID
) -> tuple[ExecutionEffect, FullDynamicGoalProof]:
    proof = derive_full_dynamic_goal_proof(data)
    if proof.status != "VERIFIED_RANGE" or proof.dynamic_cap_cents is None:
        raise ValueError("DYNAMIC_GOAL_NOT_READY:" + ",".join(proof.reasons))
    goal, lots = _originals(data)
    remaining = proof.dynamic_cap_cents
    uses = []
    cash: dict[UUID, int] = {}
    for lot in lots:
        amount = min(remaining, lot.available_cents)
        if amount:
            uses.append(
                IncomeUse(
                    fragment_id=lot.fragment_id,
                    origin_transaction_id=lot.origin_transaction_id,
                    account_id=lot.account_id,
                    amount_cents=amount,
                )
            )
            cash[lot.account_id] = cash.get(lot.account_id, 0) + amount
            remaining -= amount
        if not remaining:
            break
    if remaining:
        raise ValueError("COMPLETE_ORIGINAL_NEW_INCOME_CANNOT_FUND_DYNAMIC_AMOUNT")
    now = data.context.snapshot.as_of
    expires = (
        min(now + timedelta(minutes=15), goal.valid_until)
        if goal.valid_until
        else (now + timedelta(minutes=15))
    )
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=data.context.user_id,
        business_key=f"goal:{goal.goal_id}:{proof.reserve.period if proof.reserve else ''}",
        action_type="ALLOCATE_GOAL",
        amount_cents=proof.dynamic_cap_cents,
        cash_uses=[
            CashUse(account_id=identity, amount_cents=amount)
            for identity, amount in sorted(cash.items(), key=lambda item: str(item[0]))
        ],
        income_uses=uses,
        destination_account_id=goal.account_id,
        goal_id=goal.goal_id,
        policy_id=goal.policy_id,
        policy_version_id=goal.effective_policy_version_id,
        policy_version_ids=[goal.effective_policy_version_id],
        valid_from=now,
        expires_at=expires,
    )
    bound = derive_full_dynamic_goal_proof(data, effect)
    if bound.status != "VERIFIED_RANGE":
        raise ValueError("DYNAMIC_ORIGINAL_EFFECT_NOT_VERIFIED:" + ",".join(bound.reasons))
    return effect, bound


def validate_full_dynamic_goal_proof(
    effect: ExecutionEffect, context: ExecutionContext, proof: FullDynamicGoalProof
) -> bool:
    """Pure consumer gate; never accept a caller-supplied numeric cap alone."""
    if proof.context_hash != _digest(context) or proof.effect_hash != _effect_hash(effect):
        return False
    rebuilt = derive_full_dynamic_goal_proof(proof.inputs, effect)
    return rebuilt.status == "VERIFIED_RANGE" and rebuilt.model_dump() == proof.model_dump()


def bind_full_dynamic_goal_consumer_context(
    effect: ExecutionEffect, context: ExecutionContext, proof: FullDynamicGoalProof
) -> ExecutionContext:
    """Bind verified producer-only evidence IDs while preserving every current fact.

    This private new-protocol seam does not change original hashes or financial
    values. The service independently verifies the referenced current originals.
    The original consumer still checks the full unchanged proof/context hash.
    """
    producer = proof.inputs.context
    if not validate_full_dynamic_goal_proof(effect, producer, proof):
        raise ValueError("DYNAMIC_GOAL_PRODUCER_PROOF_INVALID")
    if context.exposure is None or producer.exposure is None:
        if context != producer:
            raise ValueError("DYNAMIC_GOAL_CURRENT_CONTEXT_CHANGED")
        return context
    expected_ids = producer.exposure.evidence_ids
    consumer_ids = context.exposure.evidence_ids
    refs = proof.inputs.source_refs
    identities = {row.evidence_id for row in refs}
    if (
        len(identities) != len(refs)
        or any(row.user_id != context.user_id for row in refs)
        or len(set(expected_ids)) != len(expected_ids)
        or len(set(consumer_ids)) != len(consumer_ids)
        or not set(consumer_ids) <= set(expected_ids) <= identities
    ):
        raise ValueError("DYNAMIC_GOAL_CONTEXT_EVIDENCE_NOT_CAPTURED")
    bound = context.model_copy(
        update={"exposure": context.exposure.model_copy(update={"evidence_ids": expected_ids})}
    )
    if bound != producer:
        raise ValueError("DYNAMIC_GOAL_CURRENT_CONTEXT_CHANGED")
    return bound


def dynamic_goal_bank_key(key: str) -> str:
    return "action:full-dynamic-goal:" + configuration_hash({"key": key})


def read_frozen_full_dynamic_goal_proof(trace: DecisionTrace) -> FullDynamicGoalProof:
    """New-protocol historical math from captured originals, never a live grant.

    The caller separately verifies the original typed trace and audit chain. This
    branch does not alter or reinterpret any legacy execution algorithm.
    """
    from app.domain.decision_trace import verify_trace

    verify_trace(trace)
    if trace.algorithm_versions.get(MARKER) != ALGORITHM:
        raise ValueError("EXACT_DYNAMIC_ALGORITHM_REQUIRED")
    saved = FullDynamicGoalInput.model_validate_json(
        json.dumps(trace.inputs["planning"][MARKER]["inputs"])
    )
    context = ExecutionContext.model_validate_json(json.dumps(trace.inputs["execution_context"]))
    effect = ExecutionEffect.model_validate_json(json.dumps(trace.inputs["effect"]))
    request = trace.inputs["action_request"]
    marker = request[MARKER]
    originals = {row.id: row for row in trace.sources}
    if len(originals) != len(trace.sources):
        raise ValueError("COMPLETE_FROZEN_DYNAMIC_SOURCES_REQUIRED")
    if (
        (trace.user_id, trace.as_of, trace.action_id)
        != (saved.context.user_id, saved.context.snapshot.as_of, effect.operation_id)
        or context != saved.context
        or marker.get("protocol") != ALGORITHM
        or marker.get("request") != saved.request.model_dump(mode="json")
        or marker.get("request_hash") != configuration_hash(saved.request.model_dump(mode="json"))
        or marker.get("user_id") != str(trace.user_id)
        or marker.get("epoch_id") != str(saved.epoch_id)
        or marker.get("effect_hash") != _effect_hash(effect)
        or request["execution"]
        != {"effect": effect.model_dump(mode="json"), "effect_hash": _effect_hash(effect)}
    ):
        raise ValueError("FROZEN_DYNAMIC_CONTEXT_EFFECT_REQUEST_BINDING_DIFFERS")
    for ref in saved.source_refs:
        original = originals.get(ref.evidence_id)
        if original is None or (
            original.user_id != trace.user_id
            or original.status_at_decision != "VALID"
            or not original.valid_from <= trace.as_of
            or original.observed_at > trace.as_of
            or original.valid_to is not None
            and trace.as_of >= original.valid_to
            or original.content_integrity != "VERIFIED"
            or original.content_hash != ref.content_hash
            or original.captured_content_hash != ref.content_hash
            or configuration_hash(original.content) != ref.content_hash
        ):
            raise ValueError("COMPLETE_FROZEN_DYNAMIC_SOURCES_REQUIRED")
    model = originals[saved.model_evidence_id]
    income = originals[saved.income_evidence_id]
    if (
        model.content != saved.model_original
        or model.source_type != "FULL_GOAL_MODEL_V1"
        or model.evidence_level != "USER_CONFIRMED_POLICY"
        or model.source_ref != str(saved.request.goal_id)
        or model.observed_at != datetime.fromisoformat(saved.model_original["confirmed_at"])
        or model.valid_from != model.observed_at
        or not native_income_original_matches(income.content, saved.income)
        or income.source_type != "SIMULATED_NEW_FUNDS_LEDGER"
        or income.evidence_level != "BANK_CONFIRMED"
    ):
        raise ValueError("FROZEN_MODEL_OR_V2_INCOME_IS_NOT_THE_CAPTURED_ORIGINAL")
    policies = {row.id: row for row in trace.policies}
    for version in saved.context.versions:
        original_version = policies.get(version.version_id)
        if original_version is None or (
            original_version.user_id != trace.user_id
            or original_version.policy_id != version.policy_id
            or original_version.configuration != version.configuration
            or original_version.configuration_hash != version.content_hash
            or original_version.configuration_integrity != "VERIFIED"
            or original_version.confirmed_at != version.confirmed_at
            or original_version.valid_from != version.valid_from
            or original_version.valid_to != version.valid_until
        ):
            raise ValueError("COMPLETE_FROZEN_ORIGINAL_MVP_VERSIONS_REQUIRED")
    proof = derive_full_dynamic_goal_proof(saved, effect)
    if (
        proof.status != "VERIFIED_RANGE"
        or proof.model_dump(mode="json") != (trace.inputs["planning"][MARKER]["proof"])
        or trace.phase == "PREPARE"
        and marker["original_proof"] != proof.model_dump(mode="json")
    ):
        raise ValueError("FROZEN_DYNAMIC_MATH_OR_ORIGINAL_PROOF_DIFFERS")
    return proof
