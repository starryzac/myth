"""Read-only dynamic goal pacing from actual model, income and protected context."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from app.db.models import Goal, Policy, PolicyVersion
from app.domain.boundary import compute_boundary
from app.domain.dynamic_goal_reserve import (
    DynamicGoalReserveInput,
    DynamicGoalReserveResult,
    compute_dynamic_goal_reserve,
)
from app.domain.multi_goal_allocation import (
    AllocationGoal,
    AllocationIncomeLot,
    HardProtectionPoint,
    MultiGoalAllocationInput,
    SourceReference,
)
from app.domain.policy_configuration import configuration_hash
from app.services.boundary import (
    OWNERSHIP_SOURCE,
    BoundarySourceIssue,
    _known_import,
    read_goal_month_fact,
)
from app.services.dashboard_helpers import current_epoch_audit
from app.services.financial_read import finalize_financial_context, load_verified_financial_context
from app.services.full_goals import _read_snapshot, read_full_goal_model
from app.services.income_ledger import read_income_state
from app.services.policy_lifecycle import PolicyLifecycleError, _now, effective_status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class DynamicGoalReserveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["verified-dynamic-goal-reserve-v1"] = "verified-dynamic-goal-reserve-v1"
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    user_id: UUID
    goal_id: UUID
    as_of: datetime
    policy_effective_status: str | None
    state: Literal["COMPUTED", "EXPIRED", "INACTIVE", "UNKNOWN"]
    reserve: DynamicGoalReserveResult | None
    protection_scope: Literal["ALL_ORIGINAL_365_DAY_RESERVES_RETAINED"] = (
        "ALL_ORIGINAL_365_DAY_RESERVES_RETAINED"
    )
    source_evidence_ids: list[UUID]
    source_issues: list[BoundarySourceIssue]
    input_hash: str
    limitations: list[str]


def read_dynamic_goal_reserve(
    session: Session, user_id: UUID, goal_id: UUID, now: datetime
) -> DynamicGoalReserveResponse:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        goal = session.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
        if goal is None:
            raise PolicyLifecycleError("NOT_FOUND", "目标不存在", 404)
        context, _, _ = load_verified_financial_context(session, user_id, now)
        policy = session.scalar(
            select(Policy).where(Policy.id == goal.policy_id, Policy.user_id == user_id)
        )
        version = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == goal.policy_id, PolicyVersion.user_id == user_id)
            .order_by(PolicyVersion.version_number.desc())
            .limit(1)
        )
        status = effective_status(policy, version, now) if policy is not None else None
        model = None
        income = None
        month = None
        owned = next((row for row in context.snapshot.goals if row.goal_id == goal_id), None)
        try:
            model = read_full_goal_model(session, user_id, goal_id, now)
            if (
                model.status != "VERIFIED"
                or model.full_configuration is None
                or model.evidence_id is None
            ):
                raise PolicyLifecycleError(
                    "MISSING_FULL_GOAL_MODEL", "动态节奏需要真实已确认的完整目标模型", 409
                )
            context.sources.used.add(model.evidence_id)
            income = read_income_state(session, user_id, now)
            context.sources.used.add(income.evidence_id)
            context.sources.used.update(origin.bank_evidence_id for origin in income.ledger.origins)
            if (
                owned is None
                or version is None
                or version.id != goal.policy_version_id
                or version.confirmed_at is None
                or goal.account_id is None
            ):
                raise PolicyLifecycleError(
                    "INVALID_GOAL_SOURCE", "动态目标归属或当前版本来源缺失", 409
                )
            ownership_proofs = context.sources.candidates(OWNERSHIP_SOURCE, "goal_id", goal_id)
            if len(ownership_proofs) != 1:
                raise ValueError("Ownership needs one original source")
            ownership_at = _known_import(ownership_proofs[0], context.sources)
            zone = UTC if context.snapshot.timezone == "UTC" else timezone(timedelta(hours=8))
            month = read_goal_month_fact(goal, context.sources, zone, ownership_at)
            if month is None or ownership_at != income.ledger.as_of:
                raise ValueError(
                    "Current income, ownership and month contribution must share an actual snapshot"
                )
        except (PolicyLifecycleError, ValueError, TypeError, KeyError) as error:
            context.sources.issue(
                error.code
                if isinstance(error, PolicyLifecycleError)
                else "INVALID_DYNAMIC_GOAL_SOURCE",
                goal_id,
                str(error),
            )
        audit = current_epoch_audit(session, user_id, [])
        if not audit.complete or audit.status != "VALID":
            context.sources.issue("AUDIT_NOT_PROVEN", str(audit.epoch_id), "当前完整审计未证明")
        context, issues, digest = finalize_financial_context(context, audit)
        reserve = None
        if (
            not issues
            and owned is not None
            and month is not None
            and model is not None
            and model.full_configuration is not None
            and model.evidence_id is not None
            and model.policy_effective_status is not None
            and version is not None
            and version.confirmed_at is not None
            and goal.account_id is not None
            and income is not None
        ):

            def refs(identities: list[UUID]) -> list[SourceReference]:
                result = []
                for identity in sorted(set(identities)):
                    proof = context.sources.evidence.get(identity)
                    if proof is None:
                        raise ValueError("A referenced original was not loaded in this snapshot")
                    result.append(
                        SourceReference(
                            user_id=user_id, evidence_id=identity, content_hash=proof.content_hash
                        )
                    )
                return result

            try:
                config = model.full_configuration
                original_goal = AllocationGoal(
                    goal_id=goal_id,
                    account_id=goal.account_id,
                    policy_id=goal.policy_id,
                    effective_policy_version_id=version.id,
                    policy_status=model.policy_effective_status,
                    target_cents=goal.target_cents,
                    current_owned_cents=owned.allocated_cents,
                    current_month_contributed_cents=month.contributed_cents,
                    monthly_min_cents=goal.monthly_min_cents,
                    monthly_target_cents=goal.monthly_target_cents,
                    monthly_max_cents=goal.monthly_max_cents,
                    minimum_guarantee_cents=config["minimum_guarantee_cents"],
                    importance=goal.importance,
                    deadline=goal.deadline,
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
                            *month.evidence_ids,
                            *(UUID(key) for key in version.evidence_ids),
                        ]
                    ),
                )
                origins = {row.origin_transaction_id: row for row in income.ledger.origins}
                lots = [
                    AllocationIncomeLot(
                        fragment_id=row.fragment_id,
                        origin_transaction_id=row.origin_transaction_id,
                        account_id=row.account_id,
                        received_cents=origins[row.origin_transaction_id].amount_cents,
                        available_cents=row.available_cents,
                        occurred_at=origins[row.origin_transaction_id].occurred_at,
                        observed_at=origins[row.origin_transaction_id].observed_at,
                        bank_evidence_id=origins[row.origin_transaction_id].bank_evidence_id,
                        bank_evidence_hash=origins[row.origin_transaction_id].bank_evidence_hash,
                        source_refs=refs(
                            [
                                income.evidence_id,
                                origins[row.origin_transaction_id].bank_evidence_id,
                            ]
                        ),
                    )
                    for row in income.ledger.fragments
                    if row.available_cents
                ]
                annual = compute_boundary(
                    context.snapshot.model_copy(update={"horizon_days": 365}),
                    context.versions,
                    context.positions,
                    context.products,
                )
                if not annual.calculation_trace:
                    raise ValueError("No proven full protection curve")
                points = [
                    HardProtectionPoint(
                        date=point.date,
                        cash_cents=point.cash_cents,
                        obligation_floor_cents=point.protected_cents_by_reason.get(
                            "obligations", 0
                        ),
                        living_floor_cents=point.protected_cents_by_reason.get("living", 0),
                        emergency_floor_cents=point.protected_cents_by_reason.get("emergency", 0),
                        owned_goal_cash_cents=point.protected_cents_by_reason.get("goal_cash", 0),
                        other_protection_floor_cents=point.protected_cents_by_reason.get(
                            "goal_minimum", 0
                        ),
                        source_refs=refs(list(context.sources.used)),
                    )
                    for point in annual.calculation_trace
                ]
                facts = MultiGoalAllocationInput(
                    user_id=user_id,
                    as_of=now,
                    timezone=context.snapshot.timezone,
                    income_lots=lots,
                    hard_protection_points=points,
                    goals=[original_goal],
                )
                reserve = compute_dynamic_goal_reserve(
                    DynamicGoalReserveInput(facts=facts, contribution_period=month.period)
                )
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                context.sources.issue("INVALID_DYNAMIC_GOAL_INPUT", goal_id, str(error))
                context, issues, digest = finalize_financial_context(context, audit)
        state: Literal["COMPUTED", "EXPIRED", "INACTIVE", "UNKNOWN"] = "UNKNOWN"
        if reserve is not None:
            state = "COMPUTED"
        elif status == "EXPIRED":
            state = "EXPIRED"
        elif status in {"SUSPENDED", "REVOKED", "CONFIRMED"}:
            state = "INACTIVE"
        return DynamicGoalReserveResponse(
            user_id=user_id,
            goal_id=goal_id,
            as_of=now,
            policy_effective_status=status,
            state=state,
            reserve=reserve,
            source_evidence_ids=sorted(context.sources.used),
            source_issues=issues,
            input_hash=configuration_hash(
                {
                    "source_digest": digest,
                    "goal_id": str(goal_id),
                    "reserve_input": reserve.input_hash if reserve else None,
                }
            ),
            limitations=[
                "只读规划不会消费原收入或执行资金动作。",
                "保留全部原365日目标最小保护；尚未释放参与目标的重复贡献保护。",
                "月节奏是显式确定性规划规则，不是未来收入或实际完成日预测。",
            ],
        )
