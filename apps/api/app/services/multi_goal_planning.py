"""Current-period joint planning from actual income, ownership and confirmed full models."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.db.models import Goal, PolicyVersion, User
from app.domain.boundary import compute_boundary
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    AllocationIncomeLot,
    HardProtectionPoint,
    MinimalGoalConflict,
    MultiGoalAllocationInput,
    MultiGoalAllocationResult,
    SourceReference,
    find_minimal_goal_conflict,
    solve_multi_goal_allocation,
)
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import BoundarySourceIssue, load_boundary_context
from app.services.dashboard_helpers import current_epoch_audit
from app.services.financial_read import finalize_financial_context
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError
from app.services.simulated_bank import validate_bank_projection
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session


class JointPlanningResponse(BaseModel):
    schema_version: Literal["verified-joint-goal-planning-v1"] = "verified-joint-goal-planning-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    grants_authority: Literal[False] = False
    funds_scope: Literal["ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY"] = (
        "ACTUAL_CURRENT_UNASSIGNED_INCOME_ONLY"
    )
    protection_scope: Literal["ALL_ORIGINAL_365_DAY_RESERVES_RETAINED"] = (
        "ALL_ORIGINAL_365_DAY_RESERVES_RETAINED"
    )
    full_model_rules_have_dedicated_audit_event: Literal[False] = False
    independent_bank_projection_matched: bool
    registered_goal_count: int
    included_goal_ids: list[UUID]
    uncovered_goal_ids: list[UUID]
    allocation: MultiGoalAllocationResult | None
    conflict: MinimalGoalConflict | None
    state: Literal["COMPUTED", "UNKNOWN"]
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    input_hash: str
    limitations: list[str]


def joint_goal_planning(
    session: Session,
    user_id: UUID,
    now: datetime,
    *,
    capture_inputs: Callable[[MultiGoalAllocationInput], None] | None = None,
) -> JointPlanningResponse:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    now = now.astimezone(UTC)
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError("INVALID_READ_CONTEXT", "联合规划要求干净只读会话", 409)
    if (
        session.scalar(text("SHOW transaction_isolation")) != "repeatable read"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError("INVALID_READ_CONTEXT", "联合规划要求RR只读事务", 409)
    user = session.get(User, user_id)
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    context = load_boundary_context(session, user_id, now)
    matched = True
    try:
        validate_bank_projection(session, user_id, now)
    except PolicyLifecycleError as error:
        matched = False
        context.sources.issue(error.code, "independent_bank", error.message)
    income = None
    try:
        income = read_income_state(session, user_id, now)
        context.sources.used.add(income.evidence_id)
        context.sources.used.update(origin.bank_evidence_id for origin in income.ledger.origins)
    except PolicyLifecycleError as error:
        context.sources.issue(error.code, "income_ledger", error.message)
    audit = current_epoch_audit(session, user_id, [])
    if not audit.complete or audit.status != "VALID":
        context.sources.issue("AUDIT_NOT_PROVEN", str(audit.epoch_id), "当前完整审计未证明")
    rows = list(session.scalars(select(Goal).where(Goal.user_id == user_id).order_by(Goal.id)))
    goals: list[AllocationGoal] = []
    uncovered: list[UUID] = []

    def refs(identities: list[UUID]) -> list[SourceReference]:
        return [
            SourceReference(
                user_id=user_id,
                evidence_id=identity,
                content_hash=context.sources.evidence[identity].content_hash,
            )
            for identity in sorted(set(identities))
            if identity in context.sources.evidence
        ]

    # Local import keeps the new full-model bridge independent of the legacy reader.
    from app.services.full_goals import read_full_goal_model

    ownership = {item.goal_id: item for item in context.snapshot.goals}
    contributions = {item.goal_id: item for item in context.snapshot.goal_month_contributions}
    for row in rows:
        try:
            model = read_full_goal_model(session, user_id, row.id, now)
            owned, contributed = ownership.get(row.id), contributions.get(row.id)
            version = session.get(PolicyVersion, row.policy_version_id)
            if (
                model.status != "VERIFIED"
                or model.full_configuration is None
                or model.evidence_id is None
                or owned is None
                or contributed is None
                or row.account_id is None
                or version is None
                or version.confirmed_at is None
                or model.policy_effective_status is None
            ):
                raise PolicyLifecycleError(
                    "MISSING_FULL_GOAL_MODEL", "目标完整模型或归属来源未证明", 409
                )
            context.sources.used.add(model.evidence_id)
            config = model.full_configuration
            goals.append(
                AllocationGoal(
                    goal_id=row.id,
                    account_id=row.account_id,
                    policy_id=row.policy_id,
                    effective_policy_version_id=row.policy_version_id,
                    policy_status=model.policy_effective_status,
                    target_cents=row.target_cents,
                    current_owned_cents=owned.allocated_cents,
                    current_month_contributed_cents=contributed.contributed_cents,
                    monthly_min_cents=row.monthly_min_cents,
                    monthly_target_cents=row.monthly_target_cents,
                    monthly_max_cents=row.monthly_max_cents,
                    minimum_guarantee_cents=config["minimum_guarantee_cents"],
                    importance=row.importance,
                    deadline=row.deadline,
                    allow_partial=config["allow_partial"],
                    allow_deferral=config["allow_deferral"],
                    deferral_cost_cents_per_day=config["deferral_cost_cents_per_day"],
                    confirmed_at=version.confirmed_at,
                    valid_from=version.valid_from or version.confirmed_at,
                    valid_until=version.valid_until,
                    source_refs=refs(
                        [
                            model.evidence_id,
                            *owned.evidence_ids,
                            *contributed.evidence_ids,
                            *(UUID(value) for value in version.evidence_ids),
                        ]
                    ),
                )
            )
        except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
            uncovered.append(row.id)
            context.sources.issue(
                error.code
                if isinstance(error, PolicyLifecycleError)
                else "INVALID_FULL_GOAL_MODEL",
                row.id,
                "完整目标来源、版本或属性尚未证明",
            )
    context, issues, digest = finalize_financial_context(context, audit)
    snapshot = context.snapshot.model_copy(update={"horizon_days": 365})
    boundary = compute_boundary(snapshot, context.versions, context.positions, context.products)
    allocation = None
    conflict = None
    if len(goals) > 8:
        context.sources.issue("JOINT_GOAL_CAPACITY", "goals", "本次联合求解最多8个完整目标")
        issues = sorted(context.sources.issues, key=lambda item: (item.code, item.source_ref))
        uncovered = sorted({*uncovered, *(goal.goal_id for goal in goals)})
    elif boundary.calculation_trace and income is not None:
        origins = {item.origin_transaction_id: item for item in income.ledger.origins}
        lots = [
            AllocationIncomeLot(
                fragment_id=fragment.fragment_id,
                origin_transaction_id=fragment.origin_transaction_id,
                account_id=fragment.account_id,
                received_cents=origins[fragment.origin_transaction_id].amount_cents,
                available_cents=fragment.available_cents,
                occurred_at=origins[fragment.origin_transaction_id].occurred_at,
                observed_at=origins[fragment.origin_transaction_id].observed_at,
                bank_evidence_id=origins[fragment.origin_transaction_id].bank_evidence_id,
                bank_evidence_hash=origins[fragment.origin_transaction_id].bank_evidence_hash,
                source_refs=refs(
                    [income.evidence_id, origins[fragment.origin_transaction_id].bank_evidence_id]
                ),
            )
            for fragment in income.ledger.fragments
            if fragment.available_cents > 0
        ]
        source_refs = refs(list(context.sources.used))
        points = [
            HardProtectionPoint(
                date=point.date,
                cash_cents=point.cash_cents,
                obligation_floor_cents=point.protected_cents_by_reason.get("obligations", 0),
                living_floor_cents=point.protected_cents_by_reason.get("living", 0),
                emergency_floor_cents=point.protected_cents_by_reason.get("emergency", 0),
                owned_goal_cash_cents=point.protected_cents_by_reason.get("goal_cash", 0),
                other_protection_floor_cents=point.protected_cents_by_reason.get("goal_minimum", 0),
                source_refs=source_refs,
            )
            for point in boundary.calculation_trace
        ]
        if len(lots) <= 128:
            candidate = MultiGoalAllocationInput(
                user_id=user_id,
                as_of=now,
                timezone=context.snapshot.timezone,
                income_lots=lots,
                hard_protection_points=points,
                goals=goals,
                source_issues=sorted({issue.code for issue in issues}),
            )
            if capture_inputs is not None:
                capture_inputs(candidate.model_copy(deep=True))
            allocation = solve_multi_goal_allocation(candidate)
            if allocation.status == "INFEASIBLE":
                conflict = find_minimal_goal_conflict(candidate)
        else:
            issues.append(
                BoundarySourceIssue(
                    code="INCOME_FRAGMENT_CAPACITY",
                    source_ref="income_ledger",
                    message="可用碎片超过128条",
                )
            )
    result_hash = configuration_hash(
        {
            "financial_sources": digest,
            "registered_goals": [str(row.id) for row in rows],
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "allocation_input": allocation.input_hash if allocation else None,
        }
    )
    return JointPlanningResponse(
        user_id=user_id,
        as_of=now,
        registered_goal_count=len(rows),
        independent_bank_projection_matched=matched,
        included_goal_ids=[goal.goal_id for goal in goals],
        uncovered_goal_ids=uncovered,
        allocation=allocation,
        conflict=conflict,
        state="COMPUTED" if allocation and allocation.status != "UNKNOWN" else "UNKNOWN",
        source_evidence_ids=sorted(context.sources.used),
        source_issues=issues,
        input_hash=result_hash,
        limitations=[
            "仅当前期规划；未完成目标延期为有标记的下界，不是实际完工日期。",
            "保留全部原365日目标最低储备；当前期联合资金池可能保守，未证明完整原问题全局最优。",
            "结果不产生行动、确认、回拨或银行授权；完整跨目标重分配与多期联合最优未接。",
        ],
    )
