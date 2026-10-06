"""Finite cash-ownership release mathematics, never a bank or goal grant."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_configuration import CrossGoalReallocationPolicy, EmergencyCondition
from app.domain.policy_configuration import UUIDReference, configuration_hash
from pydantic import Field, StrictBool, StrictInt, model_validator

Cents = Annotated[StrictInt, Field(ge=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CONDITIONS: tuple[EmergencyCondition, ...] = (
    "HARD_OBLIGATION_SHORTFALL",
    "LIVING_RESERVE_SHORTFALL",
    "EMERGENCY_BUFFER_SHORTFALL",
)


class ReallocationPreviewRequest(BoundaryModel):
    policy_id: UUIDReference
    source_goal_id: UUIDReference
    expected_policy_version_id: UUIDReference
    expected_goal_policy_version_id: UUIDReference
    expected_epoch_id: UUIDReference


class RepairFinancialFacts(BoundaryModel):
    cash_cents: Cents
    locked_goal_cash_cents: Cents
    reserved_cash_cents: Cents
    required_by_condition: dict[EmergencyCondition, Cents]
    other_full_protection_cents: Cents
    verified: StrictBool

    @model_validator(mode="after")
    def complete_three_floors(self) -> Self:
        if set(self.required_by_condition) != set(CONDITIONS):
            raise ValueError("All three original core protection floors are required")
        return self


class RepairGoalFacts(BoundaryModel):
    goal_id: UUID
    account_id: UUID | None
    original_policy_version_id: UUID
    cash_owned_cents: Cents | None
    principal_owned_cents: Cents | None
    minimum_guarantee_cents: Cents | None
    reserved_goal_cash_cents: Cents
    ownership_verified: StrictBool
    model_verified: StrictBool
    dedicated_goal_grant_verified: Literal[False] = False


class RepairPolicyFacts(BoundaryModel):
    policy_id: UUID
    version_id: UUID
    epoch_id: UUID
    content_hash: Hash
    configuration: CrossGoalReallocationPolicy
    current_confirmed: StrictBool
    references_current: StrictBool
    effective_status: str


class RepairUsageFacts(BoundaryModel):
    status: Literal["MISSING", "VERIFIED"]
    consumed_cents: Cents | None
    policy_lifetime_across_versions: Literal[True] = True
    source_refs: list[str]

    @model_validator(mode="after")
    def original_usage_required(self) -> Self:
        if (self.status == "VERIFIED") != (self.consumed_cents is not None):
            raise ValueError("Verified usage requires an actual integer, missing usage stays null")
        if self.status == "VERIFIED" and not self.source_refs:
            raise ValueError("A verified zero also needs original complete usage evidence")
        return self


class ReallocationDecisionInput(BoundaryModel):
    user_id: UUID
    epoch_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    policy: RepairPolicyFacts
    goal: RepairGoalFacts
    financial: RepairFinancialFacts
    usage: RepairUsageFacts
    source_issues: list[str]


class EmergencyRepairMath(BoundaryModel):
    status: Literal["COMPUTED", "UNKNOWN"]
    cash_cents: Cents | None
    locked_goal_cash_cents: Cents | None
    reserved_cash_cents: Cents | None
    unowned_unreserved_cash_cents: Cents | None
    required_by_condition: dict[EmergencyCondition, Cents] | None
    shortfall_by_condition: dict[EmergencyCondition, Cents] | None
    minimum_repair_cents: Cents | None
    source_cash_releasable_above_minimum_cents: Cents | None
    unique_minimum_for_registered_current_scope: bool
    principal_release_cents: Literal[0] = 0
    future_income_used_cents: Literal[0] = 0


class ReallocationDecision(BoundaryModel):
    algorithm_version: Literal["current-core-cash-ownership-repair-v1"] = (
        "current-core-cash-ownership-repair-v1"
    )
    state: Literal["NO_EMERGENCY", "BLOCKED", "UNKNOWN"]
    math: EmergencyRepairMath
    triggered_conditions: list[EmergencyCondition]
    candidate_amount_cents: Literal[None] = None
    cumulative_used_cents: Cents | None
    cumulative_remaining_cents: Cents | None
    source_goal_id: UUID
    destination_scope: Literal["PROTECTED_CASH"] = "PROTECTED_CASH"
    planning_only: Literal[True] = True
    bank_authority: Literal[False] = False
    dedicated_confirmation_required: Literal[True] = True
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    preserves_principal_placement: Literal[True] = True
    repairs_performed: Literal[False] = False
    reasons: list[str]
    input_hash: Hash


def decide_cash_reallocation(data: ReallocationDecisionInput) -> ReallocationDecision:
    """Every current cent of repair is necessary; no future principal/income enters it.

    Source adapter verifies actual originals. The mathematical amount is separate
    from candidate authority: the dedicated grant/usage execution protocol is not
    implemented, so this contract deliberately cannot publish a bank candidate.
    """
    f, goal, p = data.financial, data.goal, data.policy
    issues = list(data.source_issues)
    math_verified = f.verified and f.locked_goal_cash_cents + f.reserved_cash_cents <= f.cash_cents
    if f.verified and not math_verified:
        issues.append("CASH_OWNERSHIP_AND_RESERVATIONS_EXCEED_BANK_CASH")
    shortfalls: dict[EmergencyCondition, int] = {}
    minimum: int | None = None
    available: int | None = None
    if math_verified:
        available = f.cash_cents - f.locked_goal_cash_cents - f.reserved_cash_cents
        prefix, prior = 0, 0
        for condition in CONDITIONS:
            prefix += f.required_by_condition[condition]
            deficit = max(0, prefix - available)
            shortfalls[condition], prior = deficit - prior, deficit
        minimum = prior
    releasable = None
    if (
        goal.ownership_verified
        and goal.model_verified
        and goal.cash_owned_cents is not None
        and goal.principal_owned_cents is not None
        and goal.minimum_guarantee_cents is not None
    ):
        releasable = max(
            0,
            min(
                goal.cash_owned_cents - goal.reserved_goal_cash_cents,
                goal.cash_owned_cents + goal.principal_owned_cents - goal.minimum_guarantee_cents,
            ),
        )
    math = EmergencyRepairMath(
        status="COMPUTED" if math_verified else "UNKNOWN",
        cash_cents=f.cash_cents if math_verified else None,
        locked_goal_cash_cents=f.locked_goal_cash_cents if math_verified else None,
        reserved_cash_cents=f.reserved_cash_cents if math_verified else None,
        unowned_unreserved_cash_cents=available,
        required_by_condition=f.required_by_condition if math_verified else None,
        shortfall_by_condition=shortfalls if math_verified else None,
        minimum_repair_cents=minimum,
        source_cash_releasable_above_minimum_cents=releasable,
        unique_minimum_for_registered_current_scope=math_verified,
    )
    triggers = [condition for condition in CONDITIONS if shortfalls.get(condition, 0) > 0]
    config = p.configuration
    denied: list[str] = []
    if not config.enabled:
        denied.append("CROSS_GOAL_REALLOCATION_DEFAULT_DISABLED")
    if p.epoch_id != data.epoch_id:
        denied.append("POLICY_EPOCH_NOT_CURRENT")
    if not p.current_confirmed or p.effective_status not in {"ACTIVE", "CONFIRMED"}:
        denied.append("CURRENT_PLANNING_CONFIRMATION_NOT_VALID")
    if not p.references_current:
        denied.append("POLICY_GOAL_REFERENCE_CHANGED")
    day = data.as_of.astimezone(ZoneInfo(data.timezone)).date()
    if config.enabled and (
        config.valid_from is None
        or config.valid_until is None
        or not config.valid_from <= day <= config.valid_until
    ):
        denied.append("POLICY_OUTSIDE_CURRENT_VALIDITY")
    if goal.goal_id not in config.source_goal_ids:
        denied.append("SOURCE_GOAL_NOT_EXPLICITLY_LISTED")
    if any(condition not in config.emergency_conditions for condition in triggers):
        denied.append("ACTUAL_EMERGENCY_CONDITION_NOT_ALLOWED")
    if minimum is not None and minimum > config.single_action_cap_cents:
        denied.append("MINIMUM_REPAIR_EXCEEDS_SINGLE_CAP")
    used = data.usage.consumed_cents
    remaining = max(0, config.total_cap_cents - used) if used is not None else None
    if minimum is not None and remaining is not None and minimum > remaining:
        denied.append("MINIMUM_REPAIR_EXCEEDS_LIFETIME_POLICY_CAP")
    if minimum is not None and releasable is not None and minimum > releasable:
        denied.append("SOURCE_CASH_OR_MINIMUM_GUARANTEE_INSUFFICIENT")
    if not math_verified:
        issues.append("CURRENT_CORE_REPAIR_MATH_NOT_VERIFIED")
    if not goal.ownership_verified:
        issues.append("ORIGINAL_GOAL_CASH_OWNERSHIP_NOT_VERIFIED")
    if not goal.model_verified or releasable is None:
        issues.append("FULL_GOAL_MINIMUM_GUARANTEE_NOT_VERIFIED")
    if f.other_full_protection_cents:
        issues.append("OTHER_FULL_PROTECTION_REPAIR_SCOPE_NOT_IMPLEMENTED")
    if data.usage.status != "VERIFIED":
        issues.append("LIFETIME_POLICY_USAGE_ORIGINALS_MISSING")
    issues.append("DEDICATED_ORIGINAL_GOAL_GRANT_AND_EXECUTION_NOT_IMPLEMENTED")
    state: Literal["NO_EMERGENCY", "BLOCKED", "UNKNOWN"] = (
        "NO_EMERGENCY" if minimum == 0 and math_verified else "BLOCKED" if denied else "UNKNOWN"
    )
    return ReallocationDecision(
        state=state,
        math=math,
        triggered_conditions=triggers,
        cumulative_used_cents=used,
        cumulative_remaining_cents=remaining,
        source_goal_id=goal.goal_id,
        reasons=list(dict.fromkeys([*denied, *issues])),
        input_hash=configuration_hash(data.model_dump(mode="json")),
    )
