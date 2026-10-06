"""Deletion-minimal explanations and explicitly selected hypothetical cap repairs.

The original financial base and current execution cap never change. A preview
request registers a finite planning search, not an existing financial grant.
"""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    GoalRepairCandidate,
    MinimalGoalConflict,
    MinimalGoalRepair,
    MultiGoalAllocationInput,
    SourceReference,
    find_minimal_goal_conflict,
    propose_minimal_goal_repairs,
)
from app.domain.policy_configuration import MoneyCents, configuration_hash
from pydantic import Field, model_validator

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NAMESPACE = UUID("713d4469-dd75-5dd7-9aaa-5484e29a65a8")
RangeReason = Literal[
    "MINIMUM_CRITICAL_CAP_WITHIN_SELECTED_RANGE",
    "CURRENT_CAP_IS_NOT_A_HARD_CONFLICT",
    "SELECTED_RANGE_CANNOT_RESTORE_CAP_FEASIBILITY",
    "GOAL_NOT_CURRENTLY_AUTHORIZED",
]


class GoalConstraintExplanation(BoundaryModel):
    constraint_id: str
    kind: Literal["MINIMUM_GUARANTEE", "DEADLINE_COMPLETION", "MONTHLY_MAX"]
    goal_id: UUID
    policy_id: UUID
    current_version_id: UUID
    original_parameter_cents: MoneyCents | None
    current_owned_cents: MoneyCents
    current_month_contributed_cents: MoneyCents
    required_new_cents: MoneyCents | None
    allowed_new_cents: MoneyCents | None
    deadline: date | None
    source_refs: list[SourceReference]


class ImmutableBaseBlock(BoundaryModel):
    point_index: int
    date: date
    cash_cents: int
    protected_cents: int
    shortfall_cents: int
    source_refs: list[SourceReference]
    adjustable: Literal[False] = False


class GoalConflictExplanation(BoundaryModel):
    protocol: Literal["full-goal-minimal-conflict-v1"] = "full-goal-minimal-conflict-v1"
    original_input_hash: Hash
    registered_input_goal_ids: list[UUID]
    conflict: MinimalGoalConflict
    constraints: list[GoalConstraintExplanation]
    goals_outside_this_minimal_set: list[UUID]
    all_other_goal_constraints_proven_compatible: Literal[False] = False
    minimality: Literal["DELETION_MINIMAL_NOT_MINIMUM_CARDINALITY"] = (
        "DELETION_MINIMAL_NOT_MINIMUM_CARDINALITY"
    )
    removal_witness_scope: Literal["THIS_MINIMAL_SET_MINUS_ONE"] = "THIS_MINIMAL_SET_MINUS_ONE"
    immutable_financial_point_count: int
    immutable_base_blocks: list[ImmutableBaseBlock]
    grants_authority: Literal[False] = False


def explain_goal_conflict(request: MultiGoalAllocationInput) -> GoalConflictExplanation:
    request = MultiGoalAllocationInput.model_validate(request.model_dump())
    conflict = find_minimal_goal_conflict(request)
    goals = {row.goal_id: row for row in request.goals}
    details: list[GoalConstraintExplanation] = []
    for identifier in conflict.constraint_ids:
        kind, identity = identifier.split(":", 1)
        goal = goals[UUID(identity)]
        remaining = max(0, goal.target_cents - goal.current_owned_cents)
        common = {
            "constraint_id": identifier,
            "kind": kind,
            "goal_id": goal.goal_id,
            "policy_id": goal.policy_id,
            "current_version_id": goal.effective_policy_version_id,
            "current_owned_cents": goal.current_owned_cents,
            "current_month_contributed_cents": goal.current_month_contributed_cents,
            "source_refs": goal.source_refs,
        }
        details.append(
            GoalConstraintExplanation.model_validate(
                {
                    **common,
                    "original_parameter_cents": goal.monthly_max_cents
                    if kind == "MONTHLY_MAX"
                    else goal.minimum_guarantee_cents
                    if kind == "MINIMUM_GUARANTEE"
                    else goal.target_cents,
                    "required_new_cents": None
                    if kind == "MONTHLY_MAX"
                    else max(0, goal.minimum_guarantee_cents - goal.current_owned_cents)
                    if kind == "MINIMUM_GUARANTEE"
                    else remaining,
                    "allowed_new_cents": min(
                        remaining,
                        max(0, goal.monthly_max_cents - goal.current_month_contributed_cents),
                    )
                    if kind == "MONTHLY_MAX"
                    else None,
                    "deadline": goal.deadline if kind == "DEADLINE_COMPLETION" else None,
                }
            )
        )
    return GoalConflictExplanation(
        original_input_hash=configuration_hash(request.model_dump(mode="json")),
        registered_input_goal_ids=sorted(goals),
        conflict=conflict,
        constraints=details,
        goals_outside_this_minimal_set=sorted(set(goals) - {row.goal_id for row in details}),
        immutable_financial_point_count=len(request.hard_protection_points),
        immutable_base_blocks=[
            ImmutableBaseBlock(
                point_index=index,
                date=point.date,
                cash_cents=point.cash_cents,
                protected_cents=point.cash_cents - point.remaining_cents,
                shortfall_cents=-point.remaining_cents,
                source_refs=point.source_refs,
            )
            for index, point in enumerate(request.hard_protection_points)
            if not request.source_issues and point.remaining_cents < 0
        ],
    )


class MonthlyMaxPlanningAdjustment(BoundaryModel):
    goal_id: UUID
    expected_version_id: UUID
    minimum_new_monthly_max_cents: MoneyCents
    maximum_new_monthly_max_cents: MoneyCents

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum_new_monthly_max_cents > self.maximum_new_monthly_max_cents:
            raise ValueError("A user-selected planning range must be ordered")
        return self


class GoalRepairPreviewRequest(BoundaryModel):
    expected_epoch_id: UUID
    reviewed_state_hash: Hash
    adjustments: Annotated[list[MonthlyMaxPlanningAdjustment], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def unique_goals(self) -> Self:
        if len({row.goal_id for row in self.adjustments}) != len(self.adjustments):
            raise ValueError("Each goal has exactly one user-selected planning range")
        return self


class RepairRangeOutcome(BoundaryModel):
    goal_id: UUID
    current_version_id: UUID
    original_monthly_max_cents: MoneyCents
    proposed_monthly_max_cents: MoneyCents | None
    reason: RangeReason


class PlanningGoalRepair(BoundaryModel):
    protocol: Literal["full-goal-user-selected-repair-v1"] = "full-goal-user-selected-repair-v1"
    original_input_hash: Hash
    search_input_hash: Hash
    selection_hash: Hash
    actual_preview_selection: GoalRepairPreviewRequest
    selection_source: Literal["USER_CURRENT_READONLY_PREVIEW_REQUEST"] = (
        "USER_CURRENT_READONLY_PREVIEW_REQUEST"
    )
    selected_ranges_are_existing_financial_permissions: Literal[False] = False
    source_refs_are_current_version_identity_only: Literal[True] = True
    candidates: list[GoalRepairCandidate]
    range_outcomes: list[RepairRangeOutcome]
    repair: MinimalGoalRepair
    minimality_scope: Literal["EXACT_INTEGER_CURRENT_PERIOD_WITH_SELECTED_MAX_RANGES"] = (
        "EXACT_INTEGER_CURRENT_PERIOD_WITH_SELECTED_MAX_RANGES"
    )
    original_execution_caps_unchanged: Literal[True] = True
    original_hard_protection_unchanged: Literal[True] = True
    future_income_used_cents: Literal[0] = 0
    grants_authority: Literal[False] = False
    requires_new_version_confirmation: Literal[True] = True


def _required_new(goal: AllocationGoal, request: MultiGoalAllocationInput) -> int:
    amount = max(0, goal.minimum_guarantee_cents - goal.current_owned_cents)
    today = request.as_of.astimezone(ZoneInfo(request.timezone)).date()
    if goal.deadline <= today and not goal.allow_partial and not goal.allow_deferral:
        amount = max(amount, max(0, goal.target_cents - goal.current_owned_cents))
    return amount


def _active(goal: AllocationGoal, request: MultiGoalAllocationInput) -> bool:
    return (
        goal.policy_status in {"ACTIVE", "CONFIRMED"}
        and max(goal.confirmed_at, goal.valid_from) <= request.as_of
        and (goal.valid_until is None or request.as_of < goal.valid_until)
    )


def goal_repair_review_state_hash(request: MultiGoalAllocationInput, epoch: UUID) -> str:
    """Stable review identity, re-evaluated at every actual server read clock.

    The full original input hash continues to bind its actual as_of. Only that read
    clock is absent here; current date and effective permissions are explicit.
    Every source/amount/version/protection point remains in the review basis.
    """
    values = request.model_dump(mode="json")
    values.pop("as_of")
    return configuration_hash(
        {
            "protocol": "full-goal-repair-review-state-v1",
            "epoch_id": str(epoch),
            "input_without_read_clock": values,
            "current_local_date": request.as_of.astimezone(ZoneInfo(request.timezone))
            .date()
            .isoformat(),
            "current_goal_permissions": [
                {"goal_id": str(row.goal_id), "currently_authorized": _active(row, request)}
                for row in sorted(request.goals, key=lambda row: row.goal_id)
            ],
        }
    )


def preview_selected_goal_repairs(
    request: MultiGoalAllocationInput,
    selection: GoalRepairPreviewRequest,
    version_identity_refs: dict[UUID, SourceReference],
) -> PlanningGoalRepair:
    """Exact max-range feasibility repair, preserving all original financial constraints.

    For this family each infeasible cap needs max >= contributed + mandatory new.
    The nearest legal integer in the selected range minimizes parameter deviation.
    Larger caps cannot improve feasibility of the lower-bound flow, so one critical
    candidate per goal is complete for these ranges. Subsets use the original
    finite policy-count/deviation/priority ordering, not a weighted surrogate.
    """
    request = MultiGoalAllocationInput.model_validate(request.model_dump())
    selection = GoalRepairPreviewRequest.model_validate(selection.model_dump())
    original_hash = configuration_hash(request.model_dump(mode="json"))
    if (
        goal_repair_review_state_hash(request, selection.expected_epoch_id)
        != selection.reviewed_state_hash
    ):
        raise ValueError("The reviewed original planning input is stale")
    if request.source_issues or any(
        point.remaining_cents < 0 for point in request.hard_protection_points
    ):
        return PlanningGoalRepair(
            original_input_hash=original_hash,
            search_input_hash=original_hash,
            selection_hash=configuration_hash(selection.model_dump(mode="json")),
            actual_preview_selection=selection,
            candidates=[],
            range_outcomes=[],
            repair=propose_minimal_goal_repairs(request, []),
        )
    goals = {row.goal_id: row for row in request.goals}
    outcomes: list[RepairRangeOutcome] = []
    candidates: list[GoalRepairCandidate] = []
    selected_fields: dict[UUID, list[Literal["monthly_max_cents"]]] = {}
    for option in sorted(selection.adjustments, key=lambda row: row.goal_id):
        goal = goals.get(option.goal_id)
        if goal is None or goal.effective_policy_version_id != option.expected_version_id:
            raise ValueError("The selected goal or original policy version is stale")
        identity = version_identity_refs.get(goal.goal_id)
        if identity is None or identity not in goal.source_refs:
            raise ValueError("The current FULL model identity must be an original verified source")
        amount = _required_new(goal, request)
        remaining = max(0, goal.target_cents - goal.current_owned_cents)
        cap = min(remaining, max(0, goal.monthly_max_cents - goal.current_month_contributed_cents))
        minimum = max(
            option.minimum_new_monthly_max_cents,
            goal.monthly_target_cents,
            goal.current_month_contributed_cents + amount,
        )
        reason: RangeReason
        proposed = None
        if not _active(goal, request):
            reason = "GOAL_NOT_CURRENTLY_AUTHORIZED"
        elif amount <= cap:
            reason = "CURRENT_CAP_IS_NOT_A_HARD_CONFLICT"
        elif minimum > option.maximum_new_monthly_max_cents:
            reason = "SELECTED_RANGE_CANNOT_RESTORE_CAP_FEASIBILITY"
        else:
            proposed = minimum
            reason = "MINIMUM_CRITICAL_CAP_WITHIN_SELECTED_RANGE"
            selected_fields[goal.goal_id] = ["monthly_max_cents"]
            candidate_values = {
                "selection": selection.model_dump(mode="json"),
                "goal_id": str(goal.goal_id),
                "monthly_max_cents": proposed,
                "identity": identity.model_dump(mode="json"),
            }
            candidates.append(
                GoalRepairCandidate(
                    candidate_id=uuid5(NAMESPACE, configuration_hash(candidate_values)),
                    goal_id=goal.goal_id,
                    source_policy_version_id=goal.effective_policy_version_id,
                    monthly_max_cents=proposed,
                    permission_ref=identity,
                )
            )
        outcomes.append(
            RepairRangeOutcome(
                goal_id=goal.goal_id,
                current_version_id=goal.effective_policy_version_id,
                original_monthly_max_cents=goal.monthly_max_cents,
                proposed_monthly_max_cents=proposed,
                reason=reason,
            )
        )
    # This distinct input is a hypothetical user-selected proposal registration.
    # It never changes current SQL policies or claims that original evidence
    # granted a larger bank cap. Both actual and hypothetical hashes are exposed.
    search = MultiGoalAllocationInput.model_validate(
        request.model_copy(
            update={
                "goals": [
                    row.model_copy(
                        update={"negotiable_fields": selected_fields.get(row.goal_id, [])}
                    )
                    for row in request.goals
                ]
            },
            deep=True,
        ).model_dump()
    )
    return PlanningGoalRepair(
        original_input_hash=original_hash,
        search_input_hash=configuration_hash(search.model_dump(mode="json")),
        selection_hash=configuration_hash(selection.model_dump(mode="json")),
        actual_preview_selection=selection,
        candidates=candidates,
        range_outcomes=outcomes,
        repair=propose_minimal_goal_repairs(search, candidates),
    )
