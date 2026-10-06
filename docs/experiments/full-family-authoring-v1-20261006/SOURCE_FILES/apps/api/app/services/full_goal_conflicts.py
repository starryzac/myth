"""Actual verified FULL joint input, conflict witnesses and read-only new-cap previews."""

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_goal_conflicts import (
    GoalConflictExplanation,
    GoalRepairPreviewRequest,
    PlanningGoalRepair,
    explain_goal_conflict,
    goal_repair_review_state_hash,
    preview_selected_goal_repairs,
)
from app.domain.multi_goal_allocation import (
    MinimalGoalRepair,
    SourceReference,
    propose_minimal_goal_repairs,
)
from app.domain.policy_configuration import configuration_hash
from app.services.audit_chain import current_audit_epoch
from app.services.full_goals import (
    FullGoalModelResponse,
    FullGoalPreviewResponse,
    preview_full_goal_model,
    read_full_goal_model,
)
from app.services.full_joint_goal_planning import (
    FullJointPlanningResponse,
    full_joint_goal_planning,
)
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from sqlalchemy.orm import Session


class FullGoalConflictResponse(BoundaryModel):
    protocol: Literal["full-goal-conflict-read-v1"] = "full-goal-conflict-read-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID | None
    as_of: datetime
    state: Literal["COMPUTED", "UNKNOWN"]
    registered_goal_count: int
    included_goal_ids: list[UUID]
    uncovered_goal_ids: list[UUID]
    current_input_hash: str | None
    review_state_hash: str | None
    planning_source_digest: str
    full_binding_hash: str | None
    explanation: GoalConflictExplanation | None
    current_permission_repair: MinimalGoalRepair | None
    source_evidence_ids: list[UUID]
    reasons: list[str]
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    limits: list[str]


class ExistingGoalVersionPreview(BoundaryModel):
    goal_id: UUID
    current_version_id: UUID
    original_full_configuration: dict[str, Any]
    original_full_configuration_hash: str
    original_monthly_max_cents: int
    proposed_monthly_max_cents: int
    preview_endpoint: str
    preview_request: dict[str, Any]
    actual_existing_preview: FullGoalPreviewResponse
    confirmation_endpoint: str
    confirmation_bindings: dict[str, Any]
    missing_explicit_user_fields: list[Literal["accepted", "reason", "idempotency_key"]]
    ready_to_submit_confirmation: Literal[False] = False
    current_execution_permission_changed: Literal[False] = False


class FullGoalRepairResponse(BoundaryModel):
    protocol: Literal["full-goal-repair-preview-v1"] = "full-goal-repair-preview-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    original_conflicts: FullGoalConflictResponse
    proposal: PlanningGoalRepair | None
    version_previews: list[ExistingGoalVersionPreview]
    state: Literal["PROPOSAL", "NOT_NEEDED", "NO_PERMITTED_REPAIR", "BASE_INFEASIBLE", "UNKNOWN"]
    reasons: list[str]
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    writes_performed: Literal[False] = False
    confirmation_is_separate: Literal[True] = True
    multi_version_atomic_confirmation_supported: Literal[False] = False
    limitations: list[str]


LIMITS = [
    "删除最小集不是最少基数集；逐删除见证只证明该最小集合去掉一项，并不证明其余所有冲突消失。",
    "原365日/FULL保护、收入来源时刻、已有归属、最低保证不放松；未来收入始终零。",
    "当前范围为真实已登记目标的当前期整数规划，保守保留全部旧目标保护，非多期全局问题。",
    "新预览仅支持用户主动选月max上调范围；月min/deadline/其他模板修复尚未接。",
    "候选不是已获财务权限；需原双hash明确确认生成新版本后再重读，旧版本和旧hash不变。",
]


def _verified(planning: FullJointPlanningResponse, user: UUID, now: datetime) -> bool:
    binding = planning.binding
    return bool(
        planning.user_id == user
        and planning.as_of == now
        and planning.state == "COMPUTED"
        and binding is not None
        and binding.status == "VERIFIED"
        and not binding.reasons
        and not binding.candidate.source_issues
        and binding.candidate.user_id == user
        and binding.candidate.as_of == now
        and planning.allocation is not None
        and planning.allocation.status != "UNKNOWN"
        and planning.allocation.input_hash
        == configuration_hash(binding.candidate.model_dump(mode="json"))
        and planning.original_joint.registered_goal_count == len(binding.candidate.goals)
        and len(planning.original_joint.included_goal_ids) == len(binding.candidate.goals)
        and set(planning.original_joint.included_goal_ids)
        == {row.goal_id for row in binding.candidate.goals}
        and not planning.original_joint.uncovered_goal_ids
        and not planning.original_joint.source_issues
        and planning.original_joint.independent_bank_projection_matched
        and not planning.full_protection.source_issues
        and planning.full_protection.user_id == user
        and planning.full_protection.as_of == now
        and planning.full_protection.audit.complete
        and planning.full_protection.audit.status == "VALID"
    )


def _explanation(
    planning: FullJointPlanningResponse, user: UUID, epoch: UUID | None, now: datetime
) -> FullGoalConflictResponse:
    ready = epoch is not None and _verified(planning, user, now)
    binding = planning.binding
    candidate = binding.candidate if binding is not None else None
    reasons = set(planning.reasons)
    if not ready:
        reasons.add("ACTUAL_COMPLETE_CURRENT_GOAL_INPUT_NOT_VERIFIED")
    return FullGoalConflictResponse(
        user_id=user,
        epoch_id=epoch,
        as_of=now,
        state="COMPUTED" if ready else "UNKNOWN",
        registered_goal_count=planning.original_joint.registered_goal_count,
        included_goal_ids=planning.original_joint.included_goal_ids,
        uncovered_goal_ids=planning.original_joint.uncovered_goal_ids,
        current_input_hash=configuration_hash(candidate.model_dump(mode="json"))
        if candidate is not None
        else None,
        review_state_hash=goal_repair_review_state_hash(candidate, epoch)
        if ready and candidate is not None and epoch is not None
        else None,
        planning_source_digest=planning.input_hash,
        full_binding_hash=binding.binding_hash if binding is not None else None,
        explanation=explain_goal_conflict(candidate) if ready and candidate is not None else None,
        current_permission_repair=propose_minimal_goal_repairs(candidate, [])
        if ready and candidate is not None
        else None,
        source_evidence_ids=sorted(
            set(planning.original_joint.source_evidence_ids)
            | set(planning.full_protection.source_evidence_ids)
        ),
        reasons=sorted(reasons),
        limits=LIMITS,
    )


def _current(
    session: Session, user: UUID, now: datetime
) -> tuple[FullJointPlanningResponse, FullGoalConflictResponse]:
    _read_snapshot(session)
    with session.no_autoflush:
        planning = full_joint_goal_planning(session, user, now)
        epoch = current_audit_epoch(session, user)
        epoch_id = epoch.id if epoch is not None and epoch.status == "OPEN" else None
        return planning, _explanation(planning, user, epoch_id, now)


def read_full_goal_conflicts(
    session: Session, user: UUID, now: datetime
) -> FullGoalConflictResponse:
    _, response = _current(session, user, _now(now))
    return response


def _model_identity(
    model: FullGoalModelResponse, user: UUID, epoch: UUID, version: UUID
) -> SourceReference:
    if (
        model.status != "VERIFIED"
        or model.full_configuration is None
        or model.evidence_id is None
        or model.evidence_hash is None
        or model.full_configuration_hash is None
        or model.full_configuration_hash != configuration_hash(model.full_configuration)
        or model.epoch_id != epoch
        or model.base_policy_version_id != version
        or model.policy_effective_status not in {"ACTIVE", "CONFIRMED"}
    ):
        raise PolicyLifecycleError("STALE_REPAIR_GOAL_MODEL", "当前完整目标模型不再验真", 409)
    return SourceReference(
        user_id=user, evidence_id=model.evidence_id, content_hash=model.evidence_hash
    )


def preview_full_goal_repairs(
    session: Session, user: UUID, body: GoalRepairPreviewRequest, now: datetime
) -> FullGoalRepairResponse:
    now = _now(now)
    planning, original = _current(session, user, now)
    if original.epoch_id != body.expected_epoch_id:
        raise PolicyLifecycleError("STALE_REPAIR_EPOCH", "修复预览原模拟期已变化", 409)
    if original.state == "COMPUTED" and original.review_state_hash != body.reviewed_state_hash:
        raise PolicyLifecycleError("STALE_REPAIR_INPUT", "修复预览原财务输入已变化，请重读", 409)
    proposals: PlanningGoalRepair | None = None
    previews: list[ExistingGoalVersionPreview] = []
    reasons = set(original.reasons)
    state: Literal[
        "PROPOSAL", "NOT_NEEDED", "NO_PERMITTED_REPAIR", "BASE_INFEASIBLE", "UNKNOWN"
    ] = "UNKNOWN"
    if original.state == "COMPUTED" and planning.binding is not None:
        candidate = planning.binding.candidate
        goals = {row.goal_id: row for row in candidate.goals}
        models: dict[UUID, FullGoalModelResponse] = {}
        refs: dict[UUID, SourceReference] = {}
        try:
            for option in body.adjustments:
                goal = goals.get(option.goal_id)
                if goal is None or goal.effective_policy_version_id != option.expected_version_id:
                    raise PolicyLifecycleError(
                        "STALE_REPAIR_VERSION", "修复预览原目标版本已变化", 409
                    )
                model = read_full_goal_model(session, user, option.goal_id, now)
                if model.goal_id != goal.goal_id or model.policy_id != goal.policy_id:
                    raise PolicyLifecycleError(
                        "STALE_REPAIR_GOAL_MODEL", "修复目标身份与原模型不同", 409
                    )
                refs[option.goal_id] = _model_identity(
                    model, user, body.expected_epoch_id, option.expected_version_id
                )
                models[option.goal_id] = model
            proposals = preview_selected_goal_repairs(candidate, body, refs)
            state = proposals.repair.status
            reasons.update(proposals.repair.reasons)
            if state == "PROPOSAL":
                for chosen in proposals.repair.candidates:
                    model = models[chosen.goal_id]
                    assert (
                        model.full_configuration is not None
                        and model.full_configuration_hash is not None
                    )
                    config = deepcopy(model.full_configuration)
                    assert chosen.monthly_max_cents is not None
                    old_max = config["monthly_contribution"]["max_cents"]
                    config["monthly_contribution"]["max_cents"] = chosen.monthly_max_cents
                    actual = preview_full_goal_model(
                        session, user, chosen.goal_id, chosen.source_policy_version_id, config, now
                    )
                    if (
                        actual.epoch_id != body.expected_epoch_id
                        or actual.full_configuration != config
                    ):
                        raise PolicyLifecycleError(
                            "REPAIR_PREVIEW_BINDING_MISMATCH", "新版本预览与原候选不一致", 409
                        )
                    previews.append(
                        ExistingGoalVersionPreview(
                            goal_id=chosen.goal_id,
                            current_version_id=chosen.source_policy_version_id,
                            original_full_configuration=model.full_configuration,
                            original_full_configuration_hash=model.full_configuration_hash,
                            original_monthly_max_cents=old_max,
                            proposed_monthly_max_cents=chosen.monthly_max_cents,
                            preview_endpoint=f"/api/v1/goals/{chosen.goal_id}/full-model/preview",
                            preview_request={
                                "expected_version_id": str(chosen.source_policy_version_id),
                                "configuration": config,
                            },
                            actual_existing_preview=actual,
                            confirmation_endpoint=f"/api/v1/goals/{chosen.goal_id}/full-model/confirm",
                            confirmation_bindings={
                                "expected_version_id": str(chosen.source_policy_version_id),
                                "expected_epoch_id": str(body.expected_epoch_id),
                                "configuration": config,
                                "reviewed_full_hash": actual.full_configuration_hash,
                                "reviewed_base_hash": actual.base_configuration_hash,
                            },
                            missing_explicit_user_fields=["accepted", "reason", "idempotency_key"],
                        )
                    )
        except PolicyLifecycleError:
            raise
        except ValueError as error:
            raise PolicyLifecycleError(
                "INVALID_REPAIR_PLANNING_SELECTION", str(error), 422
            ) from error
    return FullGoalRepairResponse(
        user_id=user,
        as_of=now,
        original_conflicts=original,
        proposal=proposals,
        version_previews=previews,
        state=state,
        reasons=sorted(reasons),
        limitations=[
            *LIMITS,
            "多目标修复确认没有原子多版本接口；每次明确确认后必须重读当前财务和新版本，不能批量沿用旧候选。",
        ],
    )
