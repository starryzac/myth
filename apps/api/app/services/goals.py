"""Initialize a zero-owned goal from an already confirmed policy; never move cash."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid5

from app.db.models import Account, EvidenceItem, Goal, Policy, PolicyVersion, User
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.boundary import CONTRIBUTION_SOURCE, OWNERSHIP_SOURCE
from app.services.policy_lifecycle import PolicyLifecycleError, is_version_authorized
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session


class GoalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    policy_id: UUID
    policy_version_id: UUID
    account_id: UUID | None
    name: str
    target_cents: int
    allocated_cents: int
    deadline: date
    monthly_min_cents: int
    monthly_target_cents: int
    monthly_max_cents: int
    importance: int
    minimum_protection_cents: int
    reducible: bool
    deferrable: bool
    cross_goal_reallocation_allowed: bool
    asset_policy_id: UUID | None


class GoalResponse(BaseModel):
    simulation: Literal[True] = True
    goal: GoalView


class GoalList(BaseModel):
    simulation: Literal[True] = True
    items: list[GoalView]


def create_goal_projection(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    expected_version_id: UUID,
    account_id: UUID,
    now: datetime,
) -> GoalResponse:
    if now.tzinfo is None or now.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    now = now.astimezone(UTC)
    from app.db.audit_guard import transaction_gate

    transaction_gate(session, user_id)
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    if user.timezone not in {"UTC", "Asia/Shanghai"}:
        raise PolicyLifecycleError("UNSUPPORTED_TIMEZONE", "不支持此时区")
    zone = UTC if user.timezone == "UTC" else timezone(timedelta(hours=8))
    policy = session.scalar(
        select(Policy).where(Policy.id == policy_id, Policy.user_id == user_id).with_for_update()
    )
    if policy is None:
        raise PolicyLifecycleError("NOT_FOUND", "策略不存在", 404)
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.policy_id == policy_id, PolicyVersion.user_id == user_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )
    if version is None or version.id != expected_version_id:
        raise PolicyLifecycleError("VERSION_CONFLICT", "目标须使用最新已确认版本", 409)
    if not is_version_authorized(session, user_id, version.id, now):
        raise PolicyLifecycleError("POLICY_NOT_AUTHORIZED", "策略当前尚无有效授权", 409)
    config = validate_configuration(version.configuration)
    if policy.policy_type != "goal_saving" or config["type"] != "goal_saving":
        raise PolicyLifecycleError("NOT_A_GOAL_POLICY", "只有目标储备策略可创建目标")
    if configuration_hash(config) != version.content_hash:
        raise PolicyLifecycleError("INVALID_POLICY_SOURCE", "策略内容摘要不匹配", 409)
    account = session.scalar(
        select(Account).where(Account.id == account_id, Account.user_id == user_id)
    )
    if account is None:
        raise PolicyLifecycleError("NOT_FOUND", "目标账户不存在", 404)
    if account.account_type not in {"CASH", "GOAL"}:
        raise PolicyLifecycleError("INVALID_GOAL_ACCOUNT", "目标须关联活期或目标账户")
    existing = session.scalar(
        select(Goal).where(Goal.policy_id == policy_id, Goal.user_id == user_id).with_for_update()
    )
    if existing is not None:
        if existing.account_id != account_id:
            raise PolicyLifecycleError(
                "GOAL_ACCOUNT_CONFLICT", "已有目标不能通过重复创建改绑账户", 409
            )
        return GoalResponse(goal=GoalView.model_validate(existing))
    if date.fromisoformat(config["deadline"]) < now.astimezone(zone).date():
        raise PolicyLifecycleError("GOAL_DEADLINE_PASSED", "目标截止日已过，需重新确认策略", 409)
    from app.services.simulated_bank import validate_bank_projection, validate_recovery_exposure

    # Do not let a zero goal initialization republish unrelated, inconsistent bank facts.
    validate_bank_projection(session, user_id, now)
    validate_recovery_exposure(session, user_id, now)
    monthly, priority = config["monthly_contribution"], config["priority"]
    goal = Goal(
        id=uuid5(policy_id, "goal-projection-v1"),
        user_id=user_id,
        policy_id=policy_id,
        policy_version_id=version.id,
        account_id=account_id,
        name=config.get("name") or policy.name,
        target_cents=config["target_cents"],
        allocated_cents=0,
        deadline=date.fromisoformat(config["deadline"]),
        monthly_min_cents=monthly["min_cents"],
        monthly_target_cents=monthly["target_cents"],
        monthly_max_cents=monthly["max_cents"],
        importance=priority["importance"],
        minimum_protection_cents=priority["minimum_cents"],
        reducible=priority["reducible"],
        deferrable=priority["deferrable"],
        cross_goal_reallocation_allowed=config["cross_goal_reallocation_allowed"],
        asset_policy_id=UUID(config["asset_policy_id"]) if config.get("asset_policy_id") else None,
        created_at=now,
    )
    session.add(goal)
    session.flush()
    common: dict[str, Any] = {
        "simulation": True,
        "user_id": str(user_id),
        "goal_id": str(goal.id),
        "as_of": now.isoformat(),
    }
    payloads = {
        OWNERSHIP_SOURCE: {
            **common,
            "protocol": "goal-ownership-v1",
            "policy_id": str(policy_id),
            "account_id": str(account_id),
            "allocated_cents": 0,
            "cash_owned_cents": 0,
            "principal_owned_cents": 0,
            "position_ids": [],
        },
        CONTRIBUTION_SOURCE: {
            **common,
            "protocol": "goal-month-contribution-v1",
            "period": now.astimezone(zone).strftime("%Y-%m"),
            "contributed_cents": 0,
            "complete": True,
        },
    }
    for source, payload in payloads.items():
        session.add(
            EvidenceItem(
                id=uuid5(goal.id, source),
                user_id=user_id,
                evidence_level="BANK_CONFIRMED",
                source_type=source,
                source_ref=f"goal-initialization:{goal.id}",
                content=payload,
                content_hash=configuration_hash(payload),
                valid_from=now,
                observed_at=now,
                created_at=now,
                status="VALID",
            )
        )
    session.flush()
    from app.services.execution_bank import open_execution_anchors
    from app.services.execution_exposure import refresh_execution_exposure

    # Only this newly created, zero-owned goal receives an independent opening.
    # Existing positive projections return above; they can never repair bank truth here.
    open_execution_anchors(session, user_id, now, goal_balances={goal.id: (0, 0)})
    refresh_execution_exposure(session, user_id, now, goal.id)
    session.flush()
    from app.services.audit_recording import record_goal_initialized

    record_goal_initialized(session, goal, now)
    return GoalResponse(goal=GoalView.model_validate(goal))


def list_goals(session: Session, user_id: UUID) -> GoalList:
    with session.no_autoflush:
        rows = session.scalars(select(Goal).where(Goal.user_id == user_id).order_by(Goal.id))
        return GoalList(items=[GoalView.model_validate(goal) for goal in rows])
