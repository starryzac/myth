"""Actual RR read-only FULL protection consumed by the original goal solver."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.boundary_types import BoundaryModel
from app.domain.full_joint_goal_planning import (
    FullJointBinding,
    bind_full_joint_input,
    captured_references,
)
from app.domain.multi_goal_allocation import (
    MinimalGoalConflict,
    MultiGoalAllocationInput,
    MultiGoalAllocationResult,
    OptimizerVersion,
    SourceReference,
    find_minimal_goal_conflict,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services.full_policy_lifecycle import _read_snapshot
from app.services.full_protection_projection import (
    FullAnnualProtectionResponse,
    compute_full_annual_protection,
)
from app.services.multi_goal_planning import JointPlanningResponse, joint_goal_planning
from app.services.policy_lifecycle import PolicyLifecycleError, _evidence, _now
from sqlalchemy.orm import Session


class FullJointPlanningResponse(BoundaryModel):
    schema_version: Literal["full-current-joint-goal-planning-v1"] = (
        "full-current-joint-goal-planning-v1"
    )
    user_id: UUID
    as_of: datetime
    simulation: Literal[True] = True
    planning_only: Literal[True] = True
    grants_authority: Literal[False] = False
    execution_support: Literal["NOT_IMPLEMENTED"] = "NOT_IMPLEMENTED"
    funds_scope: Literal["ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY"] = (
        "ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY"
    )
    planning_scope: Literal["CURRENT_PERIOD_WITH_ALL_365_DAY_ORIGINAL_AND_FULL_PROTECTION"] = (
        "CURRENT_PERIOD_WITH_ALL_365_DAY_ORIGINAL_AND_FULL_PROTECTION"
    )
    original_joint: JointPlanningResponse
    full_protection: FullAnnualProtectionResponse
    binding: FullJointBinding | None
    allocation: MultiGoalAllocationResult | None
    conflict: MinimalGoalConflict | None
    state: Literal["COMPUTED", "UNKNOWN"]
    reasons: list[str]
    input_hash: str
    limitations: list[str]


def full_joint_goal_planning(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    optimizer_version: OptimizerVersion | None = None,
) -> FullJointPlanningResponse:
    _read_snapshot(session)
    now = _now(now)
    captures: list[MultiGoalAllocationInput] = []
    with session.no_autoflush:
        original = joint_goal_planning(
            session, user_id, now, capture_inputs=captures.append,
            optimizer_version=optimizer_version,
        )
        full = compute_full_annual_protection(session, user_id, now)
        reasons = {row.code for row in (*original.source_issues, *full.source_issues)}
        if (original.user_id, original.as_of) != (user_id, now) or (full.user_id, full.as_of) != (
            user_id,
            now,
        ):
            reasons.add("ACTUAL_OWNER_OR_CLOCK_BINDING_MISMATCH")
        if (
            not original.independent_bank_projection_matched
            or not full.audit.complete
            or full.audit.status != "VALID"
        ):
            reasons.add("ACTUAL_FINANCIAL_OR_AUDIT_CONTEXT_NOT_VERIFIED")
        binding = None
        allocation = None
        conflict = None
        if len(captures) != 1:
            reasons.add("ORIGINAL_GOAL_INPUT_NOT_CAPTURED")
        else:
            captured = captures[0]
            captured_hash = configuration_hash(captured.model_dump(mode="json"))
            if original.allocation is None or original.allocation.input_hash != captured_hash:
                reasons.add("ORIGINAL_ALLOCATION_INPUT_HASH_MISMATCH")
            if (captured.user_id, captured.as_of) != (user_id, now):
                reasons.add("CAPTURED_OWNER_OR_CLOCK_BINDING_MISMATCH")
            identifiers = set(original.source_evidence_ids) | set(full.source_evidence_ids)
            identifiers.update(row.evidence_id for row in captured_references(captured))
            sources: list[SourceReference] = []
            try:
                rows = _evidence(
                    session, user_id, [str(value) for value in sorted(identifiers)], now, lock=False
                )
                sources = [
                    SourceReference(
                        user_id=row.user_id, evidence_id=row.id, content_hash=row.content_hash
                    )
                    for row in sorted(rows, key=lambda row: row.id)
                ]
            except PolicyLifecycleError as error:
                reasons.add("FRESH_EVIDENCE_VERIFICATION_FAILED:" + error.code)
            binding = bind_full_joint_input(
                captured,
                full.projection,
                sources,
                source_issues=sorted(reasons),
                seasonal_proof_sources=full.full_policy_sources,
            )
            allocation = solve_multi_goal_allocation(binding.candidate)
            reasons.update(binding.reasons)
            reasons.update(allocation.reasons)
            if allocation.status == "INFEASIBLE":
                conflict = find_minimal_goal_conflict(binding.candidate)
    return FullJointPlanningResponse(
        user_id=user_id,
        as_of=now,
        original_joint=original,
        full_protection=full,
        binding=binding,
        allocation=allocation,
        conflict=conflict,
        state="COMPUTED"
        if allocation is not None and allocation.status != "UNKNOWN"
        else "UNKNOWN",
        reasons=sorted(reasons),
        input_hash=configuration_hash(
            {
                "original_joint_hash": original.input_hash,
                "full_protection_hash": full.input_digest,
                "binding_hash": binding.binding_hash if binding else None,
                "reasons": sorted(reasons),
            }
        ),
        limitations=[
            "仅当前期规划，保留原全部365日目标最低和已归属余额，未证明多期全局最优或释放旧保护。",
            "FULL支出仅已登记未付款上界的条件现金曲线；旧版本欠付或当前证据未知时拒绝分配。",
            "未来指定来源账户的扣款未完整重放；周期转账存在时返回UNKNOWN，不能用其他账户现金冒充。",
            "节日建议未实际采纳时不增加或释放保护；未来收入不用于今天分配。",
            "不创建行动、不确认或执行银行效果；完成日期和延期仍是原当前期有标记下界。",
        ],
    )
