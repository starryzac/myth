"""Server-owned cumulative month slices from one independently projected income.

The original deadline dynamic world is retained unchanged. A selected cumulative
world uses the real MVP allocator; neither world grants bank or USER authority.
"""

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from app.domain.boundary_types import BoundaryModel
from app.domain.execution import execution_effect_hash, revalidate_execution
from app.domain.execution_types import CashUse, ExecutionEffect, ExecutionValidation
from app.domain.external_bank_fact import validate_external_fact_original
from app.domain.external_bank_fact_types import ExternalProjectionResult
from app.domain.full_dynamic_goal_execution import (
    FullDynamicGoalInput,
    _originals,
    derive_full_dynamic_goal_proof,
)
from app.domain.full_execution_protection import (
    FullExecutionProtectionResult,
    validate_full_execution_protection,
)
from app.domain.goal_allocation import GoalAllocationResult, IncomeLot, plan_goal_allocation
from app.domain.income_ledger import IncomeUse
from app.domain.policy_configuration import configuration_hash
from app.domain.standing_income_goal_scope import NATIVE_PROTOCOL
from app.services.zhiyu_standing_sources import ObservedOriginal


class GoalSliceModes(BoundaryModel):
    protocol: Literal["standing-income-goal-mode-originals-v1"] = (
        "standing-income-goal-mode-originals-v1"
    )
    modes: tuple[Literal["DEADLINE_DYNAMIC"], Literal["CUMULATIVE_EVENT_TARGET"]] = (
        "DEADLINE_DYNAMIC",
        "CUMULATIVE_EVENT_TARGET",
    )
    selected_mode: Literal["CUMULATIVE_EVENT_TARGET"] | None
    deadline_dynamic_original: dict[str, Any]
    cumulative_original: GoalAllocationResult
    event_key: str
    event_transaction_id: UUID
    event_available_cents: int
    event_source_hash: str
    current_input_hash: str
    effect_hash: str | None
    native_validation: ExecutionValidation | None
    full_protection: FullExecutionProtectionResult | None
    status: Literal["READY", "BLOCKED", "UNKNOWN"]
    reasons: list[str]
    original_hash: str
    bank_authority: Literal[False] = False


class SingleGoalIncomeSliceOriginal(BoundaryModel):
    kind: Literal["SINGLE_GOAL_EVENT_SLICE"] = "SINGLE_GOAL_EVENT_SLICE"
    current: FullDynamicGoalInput
    observed: ObservedOriginal
    effect: ExecutionEffect
    modes: GoalSliceModes


def event_lots(
    data: FullDynamicGoalInput, observed: ObservedOriginal
) -> tuple[UUID, list[IncomeLot]]:
    """Do not remove other context items; restrict funding to this actual event."""
    raw = observed.original_source
    command = validate_external_fact_original(raw)
    projection = ExternalProjectionResult.model_validate_json(
        str(raw["projection_result_canonical_text"])
    )
    origin_id = uuid5(UUID(str(raw["id"])), "transaction")
    now = data.context.snapshot.as_of
    if (
        observed.source.source_kind != "SETTLED_INCOME"
        or observed.source.user_id != data.context.user_id
        or observed.source.epoch_id != data.epoch_id
        or observed.source.original_source_id != str(raw["id"])
        or observed.source.original_source_hash != configuration_hash(raw)
        or command.user_id != data.context.user_id
        or command.kind != "INCOME"
        or raw["bank_status"] != "SETTLED"
        or raw["projection_status"] != "PROJECTED"
        or projection.transaction_id != origin_id
        or projection.external_fact_id != UUID(str(raw["id"]))
        or projection.projected_at > now
        or observed.occurred_at != command.occurred_at
        or command.occurred_at > now
    ):
        raise ValueError("CURRENT_EVENT_PROJECTED_ORIGINAL_DIFFERS")
    # Reuse the full original model/ledger/fragment/own-reservation verifier.
    _, complete = _originals(data)
    by_fragment = {lot.fragment_id: lot for lot in data.context.lots}
    origins = [row for row in data.income.origins if row.origin_transaction_id == origin_id]
    if len(origins) != 1 or (
        origins[0].origin_account_id != command.account_id
        or origins[0].amount_cents != command.amount_cents
        or origins[0].occurred_at != command.occurred_at
        or origins[0].bank_evidence_id != projection.transaction_evidence_id
    ):
        raise ValueError("CURRENT_EVENT_NATIVE_LEDGER_ORIGIN_DIFFERS")
    selected = [
        by_fragment[row.fragment_id].model_copy(update={"available_cents": row.available_cents})
        for row in complete
        if row.origin_transaction_id == origin_id
    ]
    if any(row.available_cents <= 0 for row in selected):
        raise ValueError("CURRENT_EVENT_AVAILABLE_FRAGMENT_DIFFERS")
    if sum(row.available_cents for row in selected) > command.amount_cents:
        raise ValueError("CURRENT_EVENT_BUDGET_EXCEEDS_SETTLED_ORIGINAL")
    return origin_id, selected


def derive_income_goal_modes(
    data: FullDynamicGoalInput,
    observed: ObservedOriginal,
    effect: ExecutionEffect | None = None,
    *,
    selected_mode: Literal["CUMULATIVE_EVENT_TARGET"] | None,
) -> GoalSliceModes:
    data = FullDynamicGoalInput.model_validate(data.model_dump())
    origin_id, lots = event_lots(data, observed)
    goal, _ = _originals(data)
    dynamic = derive_full_dynamic_goal_proof(data)
    current = data.context
    cumulative = plan_goal_allocation(
        goal.goal_id,
        goal.effective_policy_version_id,
        current.snapshot,
        current.versions,
        current.positions,
        current.boundary_products,
        lots,
        source_issues=current.source_issues,
    )
    reasons: list[str] = []
    status: Literal["READY", "BLOCKED", "UNKNOWN"] = "READY"
    validation: ExecutionValidation | None = None
    protection: FullExecutionProtectionResult | None = None
    if dynamic.status == "UNKNOWN" or dynamic.full_projection_input_hash is None:
        status = "UNKNOWN"
        reasons.append("COMPLETE_DEADLINE_WORLD_OR_1098_ORIGINAL_MISSING")
    if selected_mode is None:
        status = "UNKNOWN"
        reasons.append("EXPLICIT_CUMULATIVE_MODE_NOT_SELECTED")
    if cumulative.status != "READY" or not cumulative.suggested_cents:
        status = "BLOCKED" if status != "UNKNOWN" else status
        reasons.extend(["CURRENT_CUMULATIVE_EVENT_NOT_READY", *cumulative.reasons])
    if effect is not None:
        validation = revalidate_execution(effect, current)
        protection = validate_full_execution_protection(
            effect,
            current,
            validation,
            data.protection_policies,
            source_issues=tuple(data.protection_issues),
        )
        if len({row.fragment_id for row in effect.income_uses}) != len(effect.income_uses):
            reasons.append("FROZEN_SLICE_DUPLICATES_EVENT_FRAGMENT")
        available = {row.fragment_id: row for row in lots}
        by_account: dict[UUID, int] = {}
        for use in effect.income_uses:
            original = available.get(use.fragment_id)
            if (
                original is None
                or use.origin_transaction_id != origin_id
                or use.account_id != original.account_id
                or use.amount_cents > original.available_cents
            ):
                reasons.append("FROZEN_SLICE_USES_FOREIGN_OR_UNAVAILABLE_EVENT_INCOME")
            by_account[use.account_id] = by_account.get(use.account_id, 0) + use.amount_cents
        if (
            effect.business_key != NATIVE_PROTOCOL + ":" + observed.source.event_key
            or effect.user_id != current.user_id
            or effect.action_type != "ALLOCATE_GOAL"
            or effect.goal_id != goal.goal_id
            or effect.policy_id != goal.policy_id
            or effect.policy_version_id != goal.effective_policy_version_id
            or effect.policy_version_ids != [goal.effective_policy_version_id]
            or effect.destination_account_id != goal.account_id
            or effect.fee_cents != 0
            or effect.loss_cents != 0
            or sum(row.amount_cents for row in effect.income_uses) != effect.amount_cents
            or by_account != {row.account_id: row.amount_cents for row in effect.cash_uses}
            or not max(1, cumulative.remaining_min_cents or 0)
            <= effect.amount_cents
            <= (cumulative.suggested_cents or 0)
            or validation.status != "READY"
            or protection.status not in {"PASSED", "NO_ADDITIONAL_POLICY"}
        ):
            reasons.append("FROZEN_CUMULATIVE_SLICE_OR_CURRENT_PROTECTION_NOT_READY")
        if reasons and status != "UNKNOWN":
            status = "BLOCKED"
    fields = {
        "selected_mode": selected_mode,
        "deadline_dynamic_original": dynamic.model_dump(mode="json"),
        "cumulative_original": cumulative.model_dump(mode="json"),
        "event_key": observed.source.event_key,
        "event_transaction_id": str(origin_id),
        "event_available_cents": sum(row.available_cents for row in lots),
        "event_source_hash": observed.source.original_source_hash,
        "current_input_hash": configuration_hash(data.model_dump(mode="json")),
        "effect_hash": execution_effect_hash(effect) if effect else None,
        "native_validation": validation.model_dump(mode="json") if validation else None,
        "full_protection": protection.model_dump(mode="json") if protection else None,
        "status": status,
        "reasons": sorted(set(reasons)),
    }
    # Strict JSON decoding gives UUID/date native scalars their actual wire contract.
    import json

    return GoalSliceModes.model_validate_json(
        json.dumps({**fields, "original_hash": configuration_hash(fields)}, default=str)
    )


def build_income_goal_slice(
    data: FullDynamicGoalInput,
    observed: ObservedOriginal,
    action_id: UUID,
) -> tuple[ExecutionEffect, GoalSliceModes]:
    modes = derive_income_goal_modes(data, observed, selected_mode="CUMULATIVE_EVENT_TARGET")
    if modes.status != "READY" or modes.cumulative_original.suggested_cents is None:
        raise ValueError("CUMULATIVE_EVENT_SLICE_NOT_READY:" + ",".join(modes.reasons))
    goal, _ = _originals(data)
    now = data.context.snapshot.as_of
    allocations = modes.cumulative_original.lot_allocations
    cash: dict[UUID, int] = {}
    for row in allocations:
        cash[row.account_id] = cash.get(row.account_id, 0) + row.amount_cents
    effect = ExecutionEffect(
        operation_id=action_id,
        user_id=data.context.user_id,
        business_key=NATIVE_PROTOCOL + ":" + observed.source.event_key,
        action_type="ALLOCATE_GOAL",
        amount_cents=modes.cumulative_original.suggested_cents,
        cash_uses=[CashUse(account_id=k, amount_cents=v) for k, v in sorted(cash.items())],
        income_uses=[
            IncomeUse(
                fragment_id=row.fragment_id,
                origin_transaction_id=row.origin_transaction_id,
                account_id=row.account_id,
                amount_cents=row.amount_cents,
            )
            for row in allocations
        ],
        destination_account_id=goal.account_id,
        goal_id=goal.goal_id,
        policy_id=goal.policy_id,
        policy_version_id=goal.effective_policy_version_id,
        policy_version_ids=[goal.effective_policy_version_id],
        valid_from=now,
        expires_at=min(now + timedelta(minutes=15), goal.valid_until)
        if goal.valid_until
        else now + timedelta(minutes=15),
    )
    bound = derive_income_goal_modes(
        data, observed, effect, selected_mode="CUMULATIVE_EVENT_TARGET"
    )
    if bound.status != "READY":
        raise ValueError("CUMULATIVE_FROZEN_EFFECT_NOT_READY:" + ",".join(bound.reasons))
    return effect, bound
