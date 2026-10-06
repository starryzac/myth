"""Explicit conditional deadline repairs and separate monthly-min preferences.

The v1 solver and repair protocol are reused without changing their defaults.
This layer registers a hypothetical new deadline, never a deferral grant.
"""

from datetime import date, datetime, time, timedelta
from fractions import Fraction
from itertools import combinations
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_conflicts import _active, goal_repair_review_state_hash
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    GoalRepairCandidate,
    MinimalGoalRepair,
    MultiGoalAllocationInput,
    MultiGoalAllocationResult,
    SourceReference,
    propose_minimal_goal_repairs,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import (
    CalendarDate,
    MoneyCents,
    UUIDReference,
    configuration_hash,
)
from pydantic import Field, model_validator

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NAMESPACE = UUID("8a9e7025-4c98-5b33-a9e6-d5394aec7ff6")


class MonthlyMinimumRange(BoundaryModel):
    field: Literal["monthly_min_cents"]
    goal_id: UUIDReference
    expected_version_id: UUIDReference
    lower_cents: MoneyCents
    upper_cents: MoneyCents

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower_cents > self.upper_cents:
            raise ValueError("A selected monthly-min range must be ordered")
        return self


class DeadlineRange(BoundaryModel):
    field: Literal["deadline"]
    goal_id: UUIDReference
    expected_version_id: UUIDReference
    lower_date: CalendarDate
    upper_date: CalendarDate

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower_date > self.upper_date or self.upper_date == date.max:
            raise ValueError("A selected deadline range must be ordered and have a following day")
        return self


AdjustmentRange = Annotated[MonthlyMinimumRange | DeadlineRange, Field(discriminator="field")]


class GoalAdjustmentRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    reviewed_state_hash: Hash
    adjustments: Annotated[list[AdjustmentRange], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({row.goal_id for row in self.adjustments}) != len(self.adjustments):
            raise ValueError("Select one explicit parameter range per goal")
        return self


class AdjustmentOutcome(BoundaryModel):
    goal_id: UUID
    current_version_id: UUID
    field: Literal["monthly_min_cents", "deadline"]
    original_value: int | date
    proposed_value: int | date | None
    reason: str
    candidate: GoalRepairCandidate | None
    hypothetical_allocation: MultiGoalAllocationResult | None = None
    monthly_min_is_soft: Literal[True] = True
    existing_financial_permission: Literal[False] = False


class ConditionalGoalAdjustmentPlan(BoundaryModel):
    protocol: Literal["full-goal-conditional-adjustments-v1"] = (
        "full-goal-conditional-adjustments-v1"
    )
    original_input_hash: Hash
    selection_hash: Hash
    actual_selection: GoalAdjustmentRequest
    original_baseline_repair: MinimalGoalRepair
    original_allocation: MultiGoalAllocationResult
    hard_repair: MinimalGoalRepair
    outcomes: list[AdjustmentOutcome]
    soft_preference_candidates: list[GoalRepairCandidate]
    evaluated_subset_count: int
    subset_limit: Literal[256] = 256
    state: Literal[
        "PROPOSAL",
        "SOFT_PREFERENCE_PREVIEW",
        "NOT_NEEDED",
        "NO_PERMITTED_REPAIR",
        "BASE_INFEASIBLE",
        "UNKNOWN",
    ]
    minimality_scope: Literal["CURRENT_PERIOD_SELECTED_HARD_DEADLINE_CRITICAL_SUBSETS"] = (
        "CURRENT_PERIOD_SELECTED_HARD_DEADLINE_CRITICAL_SUBSETS"
    )
    selection_source: Literal["USER_CURRENT_READONLY_PREVIEW_REQUEST"] = (
        "USER_CURRENT_READONLY_PREVIEW_REQUEST"
    )
    identity_refs_are_financial_permissions: Literal[False] = False
    original_hard_protection_unchanged: Literal[True] = True
    minimum_guarantees_unchanged: Literal[True] = True
    original_ownership_and_income_unchanged: Literal[True] = True
    original_allow_deferral_unchanged: Literal[True] = True
    future_income_used_cents: Literal[0] = 0
    grants_authority: Literal[False] = False
    requires_new_version_confirmation: Literal[True] = True


def _modified(
    original: MultiGoalAllocationInput, selected: list[GoalRepairCandidate]
) -> MultiGoalAllocationInput:
    by_goal = {row.goal_id: row for row in selected}
    goals = []
    for goal in original.goals:
        row = by_goal.get(goal.goal_id)
        changes: dict[str, int | date] = {}
        if row is not None:
            if row.deadline is not None:
                changes["deadline"] = row.deadline
            if row.monthly_min_cents is not None:
                changes["monthly_min_cents"] = row.monthly_min_cents
        goals.append(AllocationGoal.model_validate({**goal.model_dump(), **changes}))
    return MultiGoalAllocationInput.model_validate({**original.model_dump(), "goals": goals})


def _repair(
    original: MultiGoalAllocationInput,
    status: Literal["PROPOSAL", "NOT_NEEDED", "NO_PERMITTED_REPAIR", "BASE_INFEASIBLE", "UNKNOWN"],
    reasons: list[str],
    selected: list[GoalRepairCandidate] | None = None,
    deviation: Fraction | None = None,
    allocation: MultiGoalAllocationResult | None = None,
) -> MinimalGoalRepair:
    chosen = selected or []
    return MinimalGoalRepair(
        status=status,
        original_input_hash=configuration_hash(original.model_dump(mode="json")),
        candidates=chosen,
        unaffected_goal_ids=sorted(
            {row.goal_id for row in original.goals} - {row.goal_id for row in chosen}
        ),
        changed_policy_count=len(chosen) if deviation is not None else None,
        parameter_deviation_numerator=deviation.numerator if deviation is not None else None,
        parameter_deviation_denominator=deviation.denominator if deviation is not None else None,
        priority_loss_cents=0 if deviation is not None else None,
        hypothetical_allocation=allocation,
        reasons=reasons,
    )


def preview_goal_adjustments(
    original: MultiGoalAllocationInput,
    selection: GoalAdjustmentRequest,
    model_identity_refs: dict[UUID, SourceReference],
) -> ConditionalGoalAdjustmentPlan:
    original = MultiGoalAllocationInput.model_validate(original.model_dump())
    selection = GoalAdjustmentRequest.model_validate(selection.model_dump())
    if (
        goal_repair_review_state_hash(original, selection.expected_epoch_id)
        != selection.reviewed_state_hash
    ):
        raise ValueError("The current reviewed planning basis is stale")
    goals = {row.goal_id: row for row in original.goals}
    for option in selection.adjustments:
        goal = goals.get(option.goal_id)
        if goal is None or goal.effective_policy_version_id != option.expected_version_id:
            raise ValueError("The actual selected goal or version changed")
        identity = model_identity_refs.get(goal.goal_id)
        if (
            identity is None
            or identity not in goal.source_refs
            or identity.user_id != original.user_id
        ):
            raise ValueError("A verified current FULL model original is required")
    baseline = propose_minimal_goal_repairs(original, [])
    original_result = solve_multi_goal_allocation(original)
    outcomes: list[AdjustmentOutcome] = []
    deadlines: list[GoalRepairCandidate] = []
    preferences: list[GoalRepairCandidate] = []
    subset_count = 0
    zone = ZoneInfo(original.timezone)
    today = original.as_of.astimezone(zone).date()

    def finish(hard: MinimalGoalRepair) -> ConditionalGoalAdjustmentPlan:
        state = (
            "SOFT_PREFERENCE_PREVIEW"
            if preferences and hard.status == "NOT_NEEDED"
            else hard.status
        )
        return ConditionalGoalAdjustmentPlan(
            original_input_hash=configuration_hash(original.model_dump(mode="json")),
            selection_hash=configuration_hash(selection.model_dump(mode="json")),
            actual_selection=selection,
            original_baseline_repair=baseline,
            original_allocation=original_result,
            hard_repair=hard,
            outcomes=outcomes,
            soft_preference_candidates=preferences,
            evaluated_subset_count=subset_count,
            state=state,
        )

    if baseline.status in {"UNKNOWN", "BASE_INFEASIBLE"} or original_result.status == "UNKNOWN":
        state: Literal["BASE_INFEASIBLE", "UNKNOWN"] = (
            "BASE_INFEASIBLE" if baseline.status == "BASE_INFEASIBLE" else "UNKNOWN"
        )
        return finish(
            _repair(original, state, ["ORIGINAL_FINANCIAL_BASE_NOT_REPAIRABLE_OR_UNVERIFIED"])
        )
    for option in sorted(selection.adjustments, key=lambda row: row.goal_id):
        goal = goals[option.goal_id]
        current: int | date = (
            goal.monthly_min_cents if isinstance(option, MonthlyMinimumRange) else goal.deadline
        )
        proposed: int | date | None = None
        reason = "GOAL_NOT_CURRENTLY_AUTHORIZED"
        candidate = None
        hypothetical = None
        if _active(goal, original):
            if isinstance(option, MonthlyMinimumRange):
                if option.upper_cents > goal.monthly_min_cents:
                    raise ValueError(
                        "This monthly-min adjustment only supports an explicit reduction range"
                    )
                if option.upper_cents == goal.monthly_min_cents:
                    reason = "SELECTED_RANGE_INCLUDES_CURRENT_SOFT_PREFERENCE"
                else:
                    proposed = option.upper_cents
                    reason = "NEAREST_SELECTED_SOFT_MINIMUM_NOT_A_HARD_FEASIBILITY_REPAIR"
            elif goal.deadline > today or goal.allow_partial or goal.allow_deferral:
                reason = "NOT_A_CURRENT_NONDEFERRABLE_HARD_DEADLINE"
            else:
                nearest = max(option.lower_date, today + timedelta(days=1), goal.deadline)
                end = datetime.combine(nearest + timedelta(days=1), time.min, zone)
                if nearest > option.upper_date or nearest <= goal.deadline:
                    reason = "SELECTED_RANGE_CANNOT_REMOVE_CURRENT_DEADLINE_BOUND"
                elif goal.valid_until is not None and end > goal.valid_until:
                    reason = "ORIGINAL_EXCLUSIVE_VALIDITY_DOES_NOT_COVER_DEADLINE_DAY"
                else:
                    proposed = nearest
                    reason = "CRITICAL_CURRENT_PERIOD_DEADLINE_NEW_VERSION_ONLY"
        if proposed is not None:
            candidate = GoalRepairCandidate(
                candidate_id=uuid5(
                    NAMESPACE,
                    configuration_hash(
                        {
                            "selection": selection.model_dump(mode="json"),
                            "goal": str(goal.goal_id),
                            "value": proposed.isoformat()
                            if isinstance(proposed, date)
                            else proposed,
                        }
                    ),
                ),
                goal_id=goal.goal_id,
                source_policy_version_id=goal.effective_policy_version_id,
                permission_ref=model_identity_refs[goal.goal_id],
                monthly_min_cents=proposed if isinstance(proposed, int) else None,
                deadline=proposed if isinstance(proposed, date) else None,
            )
            if isinstance(option, MonthlyMinimumRange):
                hypothetical = solve_multi_goal_allocation(_modified(original, [candidate]))
                preferences.append(candidate)
            else:
                deadlines.append(candidate)
        outcomes.append(
            AdjustmentOutcome(
                goal_id=goal.goal_id,
                current_version_id=goal.effective_policy_version_id,
                field=option.field,
                original_value=current,
                proposed_value=proposed,
                reason=reason,
                candidate=candidate,
                hypothetical_allocation=hypothetical,
            )
        )
    if any(
        row.hypothetical_allocation is not None and row.hypothetical_allocation.status == "UNKNOWN"
        for row in outcomes
    ):
        return finish(_repair(original, "UNKNOWN", ["SOFT_PREFERENCE_EFFECT_NOT_PROVED"]))
    if original_result.status == "OPTIMAL":
        return finish(_repair(original, "NOT_NEEDED", [], deviation=Fraction(0)))
    # The old repair helper forbids a nondeferrable deadline. This explicit new
    # conditional model changes only a proposed date and leaves allow_deferral
    # false. The original solver still enforces all of its current-period bounds.
    for count in range(1, len(deadlines) + 1):
        best: (
            tuple[Fraction, tuple[UUID, ...], list[GoalRepairCandidate], MultiGoalAllocationResult]
            | None
        ) = None
        for subset in combinations(sorted(deadlines, key=lambda row: row.candidate_id), count):
            subset_count += 1
            selected = list(subset)
            allocation = solve_multi_goal_allocation(_modified(original, selected))
            if allocation.status == "UNKNOWN":
                return finish(
                    _repair(original, "UNKNOWN", ["UNEXHAUSTED_REPAIR_LAYER_SOLVER_UNKNOWN"])
                )
            if allocation.status != "OPTIMAL":
                continue
            deviation = sum(
                (
                    Fraction(
                        (row.deadline - goals[row.goal_id].deadline).days,
                        max(1, abs((goals[row.goal_id].deadline - today).days)),
                    )
                    for row in selected
                    if row.deadline is not None
                ),
                Fraction(0),
            )
            key = (deviation, tuple(row.candidate_id for row in selected))
            if best is None or key < (best[0], best[1]):
                best = (deviation, key[1], selected, allocation)
        if best is not None:
            return finish(
                _repair(
                    original,
                    "PROPOSAL",
                    ["HYPOTHETICAL_DEADLINE_ONLY_REQUIRES_NEW_VERSION_CONFIRMATION"],
                    best[2],
                    best[0],
                    best[3],
                )
            )
    return finish(
        _repair(
            original,
            "NO_PERMITTED_REPAIR",
            ["SELECTED_SOFT_MIN_OR_DEADLINE_RANGES_CANNOT_RESTORE_HARD_FEASIBILITY"],
        )
    )
