"""Current-month pacing from original ownership and already available income only."""

from datetime import UTC, date, timedelta, timezone
from typing import Literal, Self
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.multi_goal_allocation import MultiGoalAllocationInput
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, StrictInt, model_validator


class DynamicGoalReserveInput(BoundaryModel):
    facts: MultiGoalAllocationInput
    contribution_period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")

    @model_validator(mode="after")
    def one_actual_goal_month(self) -> Self:
        if len(self.facts.goals) != 1:
            raise ValueError("One dynamic reserve view requires exactly one current goal")
        zone = UTC if self.facts.timezone == "UTC" else timezone(timedelta(hours=8))
        if self.contribution_period != self.facts.as_of.astimezone(zone).strftime("%Y-%m"):
            raise ValueError("Only the actual current calendar month contribution may be used")
        return self


ReserveStatus = Literal[
    "READY",
    "PARTIAL",
    "MINIMUM_SHORTFALL",
    "HARD_GUARANTEE_SHORTFALL",
    "DEADLINE_BLOCKED",
    "OVERDUE_READY",
    "COMPLETE",
    "MONTHLY_MAX_ALREADY_EXCEEDED",
    "INACTIVE_POLICY",
    "EXPIRED_POLICY",
    "LIQUIDITY_RISK",
    "INSUFFICIENT_EVIDENCE",
]


class DynamicGoalReserveResult(BoundaryModel):
    algorithm_version: Literal["remaining-month-gross-pacing-v1"] = (
        "remaining-month-gross-pacing-v1"
    )
    goal_id: UUID
    policy_version_id: UUID
    period: str
    status: ReserveStatus
    grants_authority: Literal[False] = False
    preview_only: Literal[True] = True
    future_income_included_cents: Literal[0] = 0
    input_hash: str
    current_owned_cents: MoneyCents | None = None
    current_month_contributed_cents: MoneyCents | None = None
    remaining_goal_cents: MoneyCents | None = None
    excess_owned_cents: MoneyCents | None = None
    progress_basis_points: StrictInt | None = None
    remaining_calendar_month_slots: StrictInt | None = None
    uncapped_gross_pace_cents: StrictInt | None = None
    dynamic_month_total_cents: StrictInt | None = None
    nominal_month_target_cents: MoneyCents | None = None
    pace_delta_from_nominal_cents: StrictInt | None = None
    eligible_available_income_cents: StrictInt | None = None
    independently_protected_budget_cents: StrictInt | None = None
    desired_additional_cents: MoneyCents | None = None
    suggested_additional_cents: MoneyCents | None = None
    minimum_shortfall_cents: MoneyCents | None = None
    guarantee_shortfall_cents: MoneyCents | None = None
    overdue_days: StrictInt | None = None
    deferral_cost_to_date_cents: StrictInt | None = None
    actual_completion_date: Literal[None] = None
    reasons: list[str]


def compute_dynamic_goal_reserve(request: DynamicGoalReserveInput) -> DynamicGoalReserveResult:
    request = DynamicGoalReserveInput.model_validate(request.model_dump())
    facts = request.facts
    goal = facts.goals[0]
    input_hash = configuration_hash(request.model_dump(mode="json"))
    identity = {
        "goal_id": goal.goal_id,
        "policy_version_id": goal.effective_policy_version_id,
        "period": request.contribution_period,
        "input_hash": input_hash,
    }

    def blocked(status: ReserveStatus, reason: str) -> DynamicGoalReserveResult:
        return DynamicGoalReserveResult.model_validate(
            identity | {"status": status, "reasons": [reason]}
        )

    if facts.source_issues:
        return blocked("INSUFFICIENT_EVIDENCE", "CURRENT_ORIGINAL_FACTS_NOT_VERIFIED")
    if goal.policy_status == "EXPIRED" or (
        goal.valid_until is not None and facts.as_of >= goal.valid_until
    ):
        return blocked("EXPIRED_POLICY", "EXPIRED_VERSION_CANNOT_FUND_CURRENT_RESERVE")
    if goal.policy_status not in {"ACTIVE", "CONFIRMED"} or facts.as_of < max(
        goal.confirmed_at, goal.valid_from
    ):
        return blocked("INACTIVE_POLICY", "EXACT_CURRENT_GOAL_AUTHORITY_REQUIRED")
    if any(point.remaining_cents < 0 for point in facts.hard_protection_points):
        return blocked("LIQUIDITY_RISK", "ORIGINAL_HARD_PROTECTION_CANNOT_BE_RELAXED")
    zone = UTC if facts.timezone == "UTC" else timezone(timedelta(hours=8))
    today = facts.as_of.astimezone(zone).date()
    remaining = max(0, goal.target_cents - goal.current_owned_cents)
    contributed = goal.current_month_contributed_cents
    slots = _month_slots(today, goal.deadline)
    # Ownership includes this month's actual contribution already. Adding the
    # contribution back ONLY inside the pacing numerator reconstructs the gross
    # month target; it is never added to available funds or cash.
    gross = (remaining + contributed + slots - 1) // slots
    total = min(
        remaining + contributed, max(goal.monthly_min_cents, min(gross, goal.monthly_max_cents))
    )
    maximum = min(remaining, max(0, goal.monthly_max_cents - contributed))
    minimum = min(remaining, max(0, goal.monthly_min_cents - contributed))
    guarantee = max(0, goal.minimum_guarantee_cents - goal.current_owned_cents)
    desired = min(remaining, max(0, total - contributed), maximum)
    if goal.deadline <= today and not goal.allow_partial and not goal.allow_deferral:
        guarantee = max(guarantee, remaining)
    total = min(
        remaining + contributed,
        goal.monthly_max_cents,
        max(total, contributed + min(guarantee, maximum)),
    )
    eligible = sum(
        lot.available_cents
        for lot in facts.income_lots
        if lot.occurred_at >= max(goal.confirmed_at, goal.valid_from)
    )
    protected = min(point.remaining_cents for point in facts.hard_protection_points)
    available = min(eligible, protected, maximum)
    desired = max(desired, min(guarantee, maximum))
    minimum_shortfall, guarantee_shortfall = (
        max(0, minimum - available),
        max(0, guarantee - available),
    )
    suggested = min(desired, available)
    overdue = max(0, (today - goal.deadline).days)
    status: ReserveStatus = "READY"
    reasons = ["CURRENT_PERIOD_PREVIEW_ONLY", "OWNED_FUNDS_ARE_NOT_NEW_INCOME"]
    if remaining == 0:
        status, suggested = "COMPLETE", 0
        reasons.append("ACTUAL_OWNERSHIP_REACHED_TARGET_NO_NEW_FUNDS_REQUIRED")
    elif contributed > goal.monthly_max_cents:
        status, suggested = "MONTHLY_MAX_ALREADY_EXCEEDED", 0
        reasons.append("ACTUAL_OLD_CONTRIBUTIONS_PRESERVED_NO_ADDITIONAL_RESERVE")
    elif guarantee_shortfall:
        status = (
            "DEADLINE_BLOCKED"
            if guarantee == remaining and goal.deadline <= today
            else "HARD_GUARANTEE_SHORTFALL"
        )
        reasons.append("NO_FEASIBLE_CURRENT_AMOUNT_MEETS_ALL_HARD_GOAL_CONSTRAINTS")
    elif minimum_shortfall and not goal.allow_partial:
        status, suggested = "MINIMUM_SHORTFALL", 0
        reasons.append("ORIGINAL_NONREDUCIBLE_MONTH_MINIMUM_PRESERVED")
    elif suggested < desired:
        status = "PARTIAL"
        reasons.append("ACTUAL_SAFE_AVAILABLE_INCOME_BELOW_DYNAMIC_PACE")
    elif overdue:
        status = "OVERDUE_READY"
        reasons.append("OVERDUE_IS_OBSERVED_NOT_A_COMPLETION_FORECAST")
    return DynamicGoalReserveResult.model_validate(
        identity
        | {
            "status": status,
            "current_owned_cents": goal.current_owned_cents,
            "current_month_contributed_cents": contributed,
            "remaining_goal_cents": remaining,
            "excess_owned_cents": max(0, goal.current_owned_cents - goal.target_cents),
            "progress_basis_points": min(
                10000, goal.current_owned_cents * 10000 // goal.target_cents
            ),
            "remaining_calendar_month_slots": slots,
            "uncapped_gross_pace_cents": gross,
            "dynamic_month_total_cents": total,
            "nominal_month_target_cents": goal.monthly_target_cents,
            "pace_delta_from_nominal_cents": total - goal.monthly_target_cents,
            "eligible_available_income_cents": eligible,
            "independently_protected_budget_cents": protected,
            "desired_additional_cents": desired,
            "suggested_additional_cents": None
            if guarantee_shortfall and status in {"HARD_GUARANTEE_SHORTFALL", "DEADLINE_BLOCKED"}
            else suggested,
            "minimum_shortfall_cents": minimum_shortfall,
            "guarantee_shortfall_cents": guarantee_shortfall,
            "overdue_days": overdue,
            "deferral_cost_to_date_cents": overdue * goal.deferral_cost_cents_per_day,
            "reasons": reasons,
        }
    )


def _month_slots(today: date, deadline: date) -> int:
    return max(1, (deadline.year - today.year) * 12 + deadline.month - today.month + 1)
