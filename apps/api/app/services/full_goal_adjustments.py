"""Current verified joint input and original FULL-model double-hash previews."""

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_adjustments import (
    ConditionalGoalAdjustmentPlan,
    GoalAdjustmentRequest,
    preview_goal_adjustments,
)
from app.domain.multi_goal_allocation import MultiGoalAllocationInput, SourceReference
from app.domain.policy_configuration import configuration_hash
from app.services.full_goal_conflicts import FullGoalConflictResponse, _current, _model_identity
from app.services.full_goals import (
    FullGoalModelResponse,
    FullGoalPreviewResponse,
    canonical_goal_bridge,
    preview_full_goal_model,
    read_full_goal_model,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


class AdjustableGoalOriginal(BoundaryModel):
    goal_id: UUID
    policy_id: UUID
    current_version_id: UUID
    monthly_min_cents: int
    monthly_target_cents: int
    monthly_max_cents: int
    minimum_guarantee_cents: int
    deadline: date
    allow_partial: bool
    allow_deferral: bool
    current_owned_cents: int
    current_month_contributed_cents: int
    valid_until_exclusive: datetime | None
    original_model: FullGoalModelResponse


class GoalAdjustmentReadResponse(BoundaryModel):
    protocol: Literal["full-goal-adjustment-read-v1"] = "full-goal-adjustment-read-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    state: Literal["COMPUTED", "UNKNOWN"]
    original_conflicts: FullGoalConflictResponse
    goals: list[AdjustableGoalOriginal]
    reasons: list[str]
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    writes_performed: Literal[False] = False


class GoalAdjustmentVersionPreview(BoundaryModel):
    goal_id: UUID
    current_version_id: UUID
    field: Literal["monthly_min_cents", "deadline"]
    scope: Literal["SOFT_PREFERENCE_ONLY", "CURRENT_PERIOD_HARD_DEADLINE_REPAIR"]
    original_value: int | date
    proposed_value: int | date
    original_full_configuration: dict[str, Any]
    original_full_configuration_hash: str
    actual_existing_preview: FullGoalPreviewResponse
    preview_endpoint: str
    preview_request: dict[str, Any]
    confirmation_endpoint: str
    confirmation_bindings: dict[str, Any]
    missing_explicit_user_fields: list[Literal["accepted", "reason", "idempotency_key"]]
    ready_to_submit_confirmation: Literal[False] = False
    current_execution_permission_changed: Literal[False] = False


class GoalAdjustmentPreviewResponse(BoundaryModel):
    protocol: Literal["full-goal-adjustment-preview-v1"] = "full-goal-adjustment-preview-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    original: GoalAdjustmentReadResponse
    proposal: ConditionalGoalAdjustmentPlan | None
    version_previews: list[GoalAdjustmentVersionPreview]
    state: Literal[
        "PROPOSAL",
        "SOFT_PREFERENCE_PREVIEW",
        "NOT_NEEDED",
        "NO_PERMITTED_REPAIR",
        "BASE_INFEASIBLE",
        "UNKNOWN",
    ]
    reasons: list[str]
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    writes_performed: Literal[False] = False
    confirmation_is_separate: Literal[True] = True
    multi_version_atomic_confirmation_supported: Literal[False] = False
    limitations: list[str]


def _capture(
    session: Session,
    user: UUID,
    now: datetime,
) -> tuple[
    GoalAdjustmentReadResponse, MultiGoalAllocationInput | None, dict[UUID, SourceReference]
]:
    planning, conflicts = _current(session, user, now)
    candidate = planning.binding.candidate if planning.binding is not None else None
    ready = (
        conflicts.state == "COMPUTED" and candidate is not None and conflicts.epoch_id is not None
    )
    originals: list[AdjustableGoalOriginal] = []
    refs: dict[UUID, SourceReference] = {}
    reasons = set(conflicts.reasons)
    if ready and candidate is not None and conflicts.epoch_id is not None:
        for goal in sorted(candidate.goals, key=lambda row: row.goal_id):
            model = read_full_goal_model(session, user, goal.goal_id, now)
            identity = _model_identity(
                model, user, conflicts.epoch_id, goal.effective_policy_version_id
            )
            config = model.full_configuration
            expected = {
                "target_cents": goal.target_cents,
                "deadline": goal.deadline.isoformat(),
                "monthly_contribution": {
                    "min_cents": goal.monthly_min_cents,
                    "target_cents": goal.monthly_target_cents,
                    "max_cents": goal.monthly_max_cents,
                },
                "minimum_guarantee_cents": goal.minimum_guarantee_cents,
                "importance": goal.importance,
                "allow_partial": goal.allow_partial,
                "allow_deferral": goal.allow_deferral,
                "deferral_cost_cents_per_day": goal.deferral_cost_cents_per_day,
            }
            if (
                model.goal_id != goal.goal_id
                or model.policy_id != goal.policy_id
                or config is None
                or identity not in goal.source_refs
                or any(config.get(key) != value for key, value in expected.items())
            ):
                raise PolicyLifecycleError(
                    "ADJUSTMENT_MODEL_INPUT_MISMATCH", "当前目标模型与原联合输入不一致", 409
                )
            refs[goal.goal_id] = identity
            originals.append(
                AdjustableGoalOriginal(
                    goal_id=goal.goal_id,
                    policy_id=goal.policy_id,
                    current_version_id=goal.effective_policy_version_id,
                    monthly_min_cents=goal.monthly_min_cents,
                    monthly_target_cents=goal.monthly_target_cents,
                    monthly_max_cents=goal.monthly_max_cents,
                    minimum_guarantee_cents=goal.minimum_guarantee_cents,
                    deadline=goal.deadline,
                    allow_partial=goal.allow_partial,
                    allow_deferral=goal.allow_deferral,
                    current_owned_cents=goal.current_owned_cents,
                    current_month_contributed_cents=goal.current_month_contributed_cents,
                    valid_until_exclusive=goal.valid_until,
                    original_model=model,
                )
            )
    if not ready:
        reasons.add("COMPLETE_CURRENT_ORIGINAL_GOAL_INPUT_MISSING")
    return (
        GoalAdjustmentReadResponse(
            user_id=user,
            as_of=now,
            state="COMPUTED" if ready else "UNKNOWN",
            original_conflicts=conflicts,
            goals=originals,
            reasons=sorted(reasons),
        ),
        candidate if ready else None,
        refs,
    )


def read_goal_adjustments(
    session: Session, user: UUID, now: datetime
) -> GoalAdjustmentReadResponse:
    response, _, _ = _capture(session, user, _now(now))
    return response


def preview_current_goal_adjustments(
    session: Session,
    user: UUID,
    body: GoalAdjustmentRequest,
    now: datetime,
) -> GoalAdjustmentPreviewResponse:
    now = _now(now)
    original, candidate, refs = _capture(session, user, now)
    conflicts = original.original_conflicts
    if conflicts.epoch_id != body.expected_epoch_id:
        raise PolicyLifecycleError("STALE_ADJUSTMENT_EPOCH", "原目标模拟期已变化", 409)
    if original.state == "COMPUTED" and conflicts.review_state_hash != body.reviewed_state_hash:
        raise PolicyLifecycleError(
            "STALE_ADJUSTMENT_INPUT", "当前保护、归属、收入或目标版本已变化，请重读", 409
        )
    plan = None
    previews: list[GoalAdjustmentVersionPreview] = []
    if candidate is not None:
        try:
            plan = preview_goal_adjustments(candidate, body, refs)
        except ValueError as error:
            raise PolicyLifecycleError(
                "INVALID_GOAL_ADJUSTMENT_SELECTION", str(error), 422
            ) from error
        if plan.state not in {"UNKNOWN", "BASE_INFEASIBLE"}:
            models = {row.goal_id: row for row in original.goals}
            chosen = [*plan.hard_repair.candidates, *plan.soft_preference_candidates]
            for change in chosen:
                model = models[change.goal_id].original_model
                assert (
                    model.full_configuration is not None
                    and model.full_configuration_hash is not None
                )
                config = deepcopy(model.full_configuration)
                field: Literal["monthly_min_cents", "deadline"]
                scope: Literal["SOFT_PREFERENCE_ONLY", "CURRENT_PERIOD_HARD_DEADLINE_REPAIR"]
                if change.monthly_min_cents is not None:
                    field, scope = "monthly_min_cents", "SOFT_PREFERENCE_ONLY"
                    old: int | date = config["monthly_contribution"]["min_cents"]
                    new: int | date = change.monthly_min_cents
                    config["monthly_contribution"]["min_cents"] = new
                else:
                    assert change.deadline is not None
                    field, scope = "deadline", "CURRENT_PERIOD_HARD_DEADLINE_REPAIR"
                    old, new = date.fromisoformat(config["deadline"]), change.deadline
                    config["deadline"] = new.isoformat()
                actual = preview_full_goal_model(
                    session, user, change.goal_id, change.source_policy_version_id, config, now
                )
                _, expected_base = canonical_goal_bridge(config)
                if (
                    actual.goal_id != change.goal_id
                    or actual.expected_version_id != change.source_policy_version_id
                    or actual.epoch_id != body.expected_epoch_id
                    or actual.full_configuration != config
                    or actual.base_configuration != expected_base
                    or actual.full_configuration_hash != configuration_hash(config)
                    or actual.base_configuration_hash
                    != configuration_hash(actual.base_configuration)
                    or actual.base_policy_impact.user_id != user
                    or actual.base_policy_impact.as_of != now
                    or actual.base_policy_impact.policy_id != models[change.goal_id].policy_id
                    or actual.base_policy_impact.expected_version_id
                    != change.source_policy_version_id
                    or actual.base_policy_impact.configuration != expected_base
                    or actual.base_policy_impact.configuration_hash
                    != actual.base_configuration_hash
                ):
                    raise PolicyLifecycleError(
                        "ADJUSTMENT_PREVIEW_BINDING_MISMATCH", "双hash原预览与候选不一致", 409
                    )
                previews.append(
                    GoalAdjustmentVersionPreview(
                        goal_id=change.goal_id,
                        current_version_id=change.source_policy_version_id,
                        field=field,
                        scope=scope,
                        original_value=old,
                        proposed_value=new,
                        original_full_configuration=model.full_configuration,
                        original_full_configuration_hash=model.full_configuration_hash,
                        actual_existing_preview=actual,
                        preview_endpoint=f"/api/v1/goals/{change.goal_id}/full-model/preview",
                        preview_request={
                            "expected_version_id": str(change.source_policy_version_id),
                            "configuration": config,
                        },
                        confirmation_endpoint=f"/api/v1/goals/{change.goal_id}/full-model/confirm",
                        confirmation_bindings={
                            "expected_version_id": str(change.source_policy_version_id),
                            "expected_epoch_id": str(body.expected_epoch_id),
                            "configuration": config,
                            "reviewed_full_hash": actual.full_configuration_hash,
                            "reviewed_base_hash": actual.base_configuration_hash,
                        },
                        missing_explicit_user_fields=["accepted", "reason", "idempotency_key"],
                    )
                )
    return GoalAdjustmentPreviewResponse(
        user_id=user,
        as_of=now,
        original=original,
        proposal=plan,
        version_previews=previews,
        state=plan.state if plan is not None else "UNKNOWN",
        reasons=sorted(set(original.reasons) | set(plan.hard_repair.reasons if plan else [])),
        limitations=[
            "月最低额是原当前期偏好；降低它不修复最低保证、硬保护或到期硬冲突。",
            "期限修复仅当前期明确选择的临界日期与最多256子集；不承诺多期达标或全局延期最优。",
            "原allow_deferral、归属、收入、最低保证及全部365/FULL保护不变；未来收入始终零。",
            "新版本需重新原预览并独立双hash明确确认；本接口零写、无资金授权，多版本确认非原子。",
        ],
    )
