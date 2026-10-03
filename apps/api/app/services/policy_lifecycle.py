"""Consent, immutable policy versions and conservative invalidation; no funds execution."""

import hmac
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from app.db.models import (
    Account,
    ActionPlan,
    ActionReceipt,
    DecisionConstraint,
    DecisionRun,
    EvidenceItem,
    Goal,
    Policy,
    PolicyProposal,
    PolicyVersion,
    User,
)
from app.domain.policy_configuration import configuration_hash, validate_configuration
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


class PolicyLifecycleError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LifecycleResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    policy_id: UUID
    current_version_id: UUID = Field(
        description="首次提交该命令后形成的版本；幂等重放返回历史回执，实时版本须 GET 查询"
    )
    previous_version_id: UUID | None = None
    status: str = Field(description="首次提交命令时的持久状态；幂等重放不代表当前实时状态")
    effective_status: str = Field(
        description="首次提交命令时计算的有效状态；当前授权必须重新 GET 并校验"
    )
    invalidated_action_ids: list[UUID]
    inflight_action_ids: list[UUID]
    requires_recompute: bool = True


class TimeRefreshResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    simulation: Literal[True] = True
    updated_policy_ids: list[UUID]
    invalidated_action_ids: list[UUID]
    inflight_action_ids: list[UUID]


def _now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PolicyLifecycleError("INVALID_CLOCK", "服务器时间必须带时区")
    return value.astimezone(UTC)


def _user(session: Session, user_id: UUID) -> User:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    return user


def _policy(session: Session, user_id: UUID, policy_id: UUID) -> Policy:
    policy = session.scalar(
        select(Policy)
        .where(
            Policy.id == policy_id,
            Policy.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if policy is None:
        raise PolicyLifecycleError("NOT_FOUND", "策略不存在", 404)
    return policy


def _latest(session: Session, policy: Policy) -> PolicyVersion | None:
    return session.scalar(
        select(PolicyVersion)
        .where(
            PolicyVersion.policy_id == policy.id,
            PolicyVersion.user_id == policy.user_id,
        )
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
    )


def effective_status(policy: Policy, version: PolicyVersion | None, now: datetime) -> str:
    """An ACTIVE column alone is never sufficient authority, including before confirmation."""
    now = _now(now)
    if policy.status == "REVOKED":
        return "REVOKED"
    if version is None or version.policy_id != policy.id or version.user_id != policy.user_id:
        return "PROPOSED"
    if (
        version.confirmed_at is None
        or version.confirmation.get("accepted") is not True
        or version.confirmation.get("reviewed_hash") != version.content_hash
        or version.confirmed_at > now
    ):
        return "PROPOSED"
    expected_binding = {
        "user_id": str(policy.user_id),
        "policy_id": str(policy.id),
        "version_id": str(version.id),
        "confirmed_at": version.confirmed_at.isoformat(),
    }
    if any(version.confirmation.get(key) != value for key, value in expected_binding.items()):
        return "PROPOSED"
    try:
        if configuration_hash(version.configuration) != version.content_hash:
            return "PROPOSED"
    except (TypeError, ValueError):
        return "PROPOSED"
    if policy.status == "EXPIRED" or (
        version.valid_until is not None and now >= version.valid_until
    ):
        return "EXPIRED"
    if policy.status == "SUSPENDED":
        return "SUSPENDED"
    if policy.status not in {"ACTIVE", "CONFIRMED"}:
        return policy.status
    if version.valid_from is not None and now < version.valid_from:
        return "CONFIRMED"
    return "ACTIVE"


def _reviewed(configuration: dict[str, Any], reviewed_hash: str, accepted: bool) -> dict[str, Any]:
    if accepted is not True:
        raise PolicyLifecycleError("CONFIRMATION_REQUIRED", "请明确确认结构化策略")
    try:
        canonical = validate_configuration(configuration)
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_CONFIGURATION", "策略配置不完整或无效") from error
    digest = configuration_hash(canonical)
    if (
        not isinstance(reviewed_hash, str)
        or len(reviewed_hash) != 64
        or any(character not in "0123456789abcdef" for character in reviewed_hash)
        or not hmac.compare_digest(digest, reviewed_hash)
    ):
        raise PolicyLifecycleError("REVIEW_MISMATCH", "策略已变化，请重新查看并确认", 409)
    return canonical


def _evidence(
    session: Session, user_id: UUID, identifiers: list[str], now: datetime, *, lock: bool = True
) -> list[EvidenceItem]:
    try:
        ids = list(dict.fromkeys(UUID(identifier) for identifier in identifiers))
    except (TypeError, ValueError, AttributeError) as error:
        raise PolicyLifecycleError("INVALID_EVIDENCE", "证据引用无效") from error
    if not ids:
        return []
    query = (
        select(EvidenceItem)
        .where(
            EvidenceItem.id.in_(ids),
            EvidenceItem.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    rows = session.scalars(query.with_for_update() if lock else query).all()
    if len(rows) != len(ids) or any(
        item.status != "VALID"
        or item.observed_at > now
        or item.valid_from > now
        or (item.valid_to is not None and now >= item.valid_to)
        for item in rows
    ):
        raise PolicyLifecycleError("INVALID_EVIDENCE", "策略证据缺失、冲突、失效或不属于当前用户")
    try:
        if any(configuration_hash(item.content) != item.content_hash for item in rows):
            raise ValueError("Evidence digest mismatch")
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_EVIDENCE", "策略证据内容与记录摘要不一致") from error
    verified = {str(item.id): item for item in rows}
    for observation in rows:
        if observation.source_type != "DETERMINISTIC_POLICY_DISCOVERY":
            continue
        sources = observation.content.get("sources")
        if (
            observation.evidence_level != "BANK_OBSERVED"
            or not isinstance(sources, list)
            or not sources
        ):
            raise PolicyLifecycleError("INVALID_EVIDENCE", "发现候选的原始证据快照缺失或无效")
        seen: set[str] = set()
        for snapshot in sources:
            identifier = snapshot.get("evidence_id") if isinstance(snapshot, dict) else None
            source = verified.get(identifier) if isinstance(identifier, str) else None
            if (
                source is None
                or source.id == observation.id
                or identifier in seen
                or source.evidence_level != "BANK_CONFIRMED"
                or source.content_hash != snapshot.get("evidence_hash")
                or source.source_type != snapshot.get("evidence_source_type")
                or source.source_ref != snapshot.get("evidence_source_ref")
                or source.observed_at.isoformat() != snapshot.get("evidence_observed_at")
            ):
                raise PolicyLifecycleError(
                    "INVALID_EVIDENCE", "发现候选的原始证据已经变化，请重新发现并审核"
                )
            seen.add(str(source.id))
    return list(rows)


def _window(
    user: User, configuration: dict[str, Any], now: datetime
) -> tuple[datetime, datetime | None]:
    if user.timezone == "Asia/Shanghai":
        zone = timezone(timedelta(hours=8))
    elif user.timezone == "UTC":
        zone = UTC
    else:
        raise PolicyLifecycleError(
            "UNSUPPORTED_TIMEZONE", "当前原型仅支持 Asia/Shanghai 或 UTC 日界"
        )
    start_text, end_text = configuration.get("valid_from"), configuration.get("valid_until")
    try:
        start = (
            datetime.combine(date.fromisoformat(start_text), time.min, zone).astimezone(UTC)
            if start_text
            else now
        )
        end = (
            datetime.combine(
                date.fromisoformat(end_text) + timedelta(days=1), time.min, zone
            ).astimezone(UTC)
            if end_text
            else None
        )
    except (ValueError, OverflowError) as error:
        raise PolicyLifecycleError("INVALID_WINDOW", "策略日期无法表示有效时间区间") from error
    if end is not None and start > end:
        raise PolicyLifecycleError("INVALID_WINDOW", "策略结束时间早于确认后的默认生效时间")
    return start, end


def _goal_asset_reference(session: Session, user_id: UUID, configuration: dict[str, Any]) -> None:
    identifier = configuration.get("asset_policy_id")
    if identifier is not None:
        policy = session.scalar(
            select(Policy).where(
                Policy.id == UUID(identifier),
                Policy.user_id == user_id,
                Policy.policy_type == "asset_authorization",
            )
        )
        if policy is None:
            raise PolicyLifecycleError("INVALID_ASSET_POLICY", "目标资产策略不存在或不属于当前用户")
    if configuration["type"] == "asset_authorization" and configuration["scope"] == "goal":
        goal = session.scalar(
            select(Goal).where(
                Goal.id == UUID(configuration["goal_id"]),
                Goal.user_id == user_id,
            )
        )
        if goal is None:
            raise PolicyLifecycleError("INVALID_GOAL", "授权目标不存在或不属于当前用户")
    amount_rule = configuration.get("amount_rule", {})
    if amount_rule.get("kind") == "bill_balance":
        account = session.scalar(
            select(Account).where(
                Account.id == UUID(amount_rule["account_id"]),
                Account.user_id == user_id,
                Account.account_type == "CREDIT_CARD",
            )
        )
        if account is None:
            raise PolicyLifecycleError("INVALID_BILL_ACCOUNT", "账单账户不存在或不是当前用户信用卡")


def _invalidation(
    session: Session, user_id: UUID, version_ids: list[UUID]
) -> tuple[list[UUID], list[UUID]]:
    if not version_ids:
        return [], []
    dependent_runs = set(
        session.scalars(
            select(DecisionRun.id).where(
                DecisionRun.user_id == user_id,
                or_(
                    *(
                        DecisionRun.policy_version_ids.contains([str(identifier)])
                        for identifier in version_ids
                    )
                ),
            )
        )
    )
    dependent_runs.update(
        session.scalars(
            select(DecisionConstraint.decision_run_id).where(
                DecisionConstraint.user_id == user_id,
                DecisionConstraint.policy_version_id.in_(version_ids),
            )
        )
    )
    plans = session.scalars(
        select(ActionPlan)
        .where(
            ActionPlan.user_id == user_id,
            or_(
                ActionPlan.policy_version_id.in_(version_ids),
                ActionPlan.decision_run_id.in_(dependent_runs),
            ),
        )
        .order_by(ActionPlan.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    receipt_rows = session.scalars(
        select(ActionReceipt)
        .where(
            ActionReceipt.user_id == user_id,
            ActionReceipt.action_plan_id.in_([plan.id for plan in plans]),
        )
        .order_by(ActionReceipt.attempt_number.desc(), ActionReceipt.id)
    ).all()
    latest_receipts: dict[UUID, ActionReceipt] = {}
    for recorded_receipt in receipt_rows:
        latest_receipts.setdefault(recorded_receipt.action_plan_id, recorded_receipt)
    receipts = set(latest_receipts)
    invalidated, inflight = [], []
    for plan in plans:
        receipt = latest_receipts.get(plan.id)
        failed_with_uncertain_effect = (
            plan.status == "FAILED"
            and receipt is not None
            and (receipt.status == "UNKNOWN" or receipt.executed_cents > 0)
        )
        if (
            plan.status in {"SUBMITTED", "UNKNOWN"}
            or (plan.id in receipts and plan.status not in {"SUCCEEDED", "FAILED", "RECONCILED"})
            or failed_with_uncertain_effect
        ):
            inflight.append(plan.id)
        elif plan.status in {"PLANNED", "AUTHORIZED"} and plan.id not in receipts:
            plan.status = "INVALIDATED"
            invalidated.append(plan.id)
        elif plan.status == "INVALIDATED" and plan.id not in receipts:
            invalidated.append(plan.id)
    return invalidated, inflight


def _version_ids(session: Session, policy: Policy) -> list[UUID]:
    return list(
        session.scalars(
            select(PolicyVersion.id).where(
                PolicyVersion.policy_id == policy.id,
                PolicyVersion.user_id == policy.user_id,
            )
        )
    )


def _result(
    policy: Policy,
    version: PolicyVersion,
    now: datetime,
    previous: UUID | None,
    invalidated: list[UUID],
    inflight: list[UUID],
) -> LifecycleResult:
    return LifecycleResult(
        policy_id=policy.id,
        current_version_id=version.id,
        previous_version_id=previous,
        status=policy.status,
        effective_status=effective_status(policy, version, now),
        invalidated_action_ids=invalidated,
        inflight_action_ids=inflight,
    )


def _refresh_one(
    session: Session, policy: Policy, now: datetime
) -> tuple[bool, list[UUID], list[UUID]]:
    version = _latest(session, policy)
    status = effective_status(policy, version, now)
    if status == policy.status or status not in {"ACTIVE", "CONFIRMED", "EXPIRED"}:
        return False, [], []
    policy.status = status
    policy.updated_at = now
    invalidated, inflight = (
        _invalidation(session, policy.user_id, _version_ids(session, policy))
        if status == "EXPIRED"
        else ([], [])
    )
    session.flush()
    return True, invalidated, inflight


def _append_version(
    session: Session,
    user: User,
    policy: Policy,
    configuration: dict[str, Any],
    evidence: list[EvidenceItem],
    now: datetime,
    previous: PolicyVersion | None,
    reason: str,
    request_key: str,
    request_hash: str,
    invalidated: list[UUID],
    inflight: list[UUID],
) -> tuple[PolicyVersion, LifecycleResult]:
    digest = configuration_hash(configuration)
    start, end = _window(user, configuration, now)
    identifier, confirmation_id = uuid4(), uuid4()
    confirmation_content = {
        "accepted": True,
        "user_id": str(user.id),
        "policy_id": str(policy.id),
        "version_id": str(identifier),
        "reviewed_hash": digest,
        "confirmed_at": now.isoformat(),
        "request_key": request_key,
        "request_hash": request_hash,
        "effective_from": start.isoformat(),
        "effective_until": end.isoformat() if end else None,
    }
    session.add(
        EvidenceItem(
            id=confirmation_id,
            user_id=user.id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type="POLICY_CONFIRMATION",
            source_ref=str(identifier),
            content=confirmation_content,
            content_hash=configuration_hash(confirmation_content),
            valid_from=now,
            observed_at=now,
        )
    )
    session.flush()
    if policy.status != "SUSPENDED":
        policy.status = (
            "EXPIRED"
            if end is not None and now >= end
            else "CONFIRMED"
            if start > now
            else "ACTIVE"
        )
    policy.updated_at = now
    version = PolicyVersion(
        id=identifier,
        user_id=user.id,
        created_at=now,
        policy_id=policy.id,
        version_number=previous.version_number + 1 if previous else 1,
        configuration=configuration,
        summary=configuration.get("name") or policy.name,
        confirmation=confirmation_content,
        confirmed_at=now,
        valid_from=start,
        valid_until=end,
        change_reason=reason,
        evidence_ids=[str(item.id) for item in evidence] + [str(confirmation_id)],
        content_hash=digest,
        previous_hash=previous.content_hash if previous else None,
    )
    result = _result(policy, version, now, previous.id if previous else None, invalidated, inflight)
    version.impact_analysis = {"lifecycle_result": result.model_dump(mode="json")}
    session.add(version)
    session.flush()
    _project_goal(session, policy, version)
    return version, result


def _project_goal(session: Session, policy: Policy, version: PolicyVersion) -> None:
    goal = session.scalar(
        select(Goal)
        .where(Goal.policy_id == policy.id, Goal.user_id == policy.user_id)
        .with_for_update()
    )
    if goal is None:
        return
    config = version.configuration
    if config["type"] != "goal_saving":
        raise PolicyLifecycleError("GOAL_POLICY_MISMATCH", "已有目标的策略类型不一致", 409)
    monthly, priority = config["monthly_contribution"], config["priority"]
    goal.policy_version_id = version.id
    goal.name = config.get("name") or policy.name
    goal.target_cents = config["target_cents"]
    goal.deadline = date.fromisoformat(config["deadline"])
    goal.monthly_min_cents = monthly["min_cents"]
    goal.monthly_target_cents = monthly["target_cents"]
    goal.monthly_max_cents = monthly["max_cents"]
    goal.importance = priority["importance"]
    goal.minimum_protection_cents = priority["minimum_cents"]
    goal.reducible = priority["reducible"]
    goal.deferrable = priority["deferrable"]
    goal.cross_goal_reallocation_allowed = config["cross_goal_reallocation_allowed"]
    goal.asset_policy_id = (
        UUID(config["asset_policy_id"]) if config.get("asset_policy_id") else None
    )


def confirm_proposal(
    session: Session,
    user_id: UUID,
    proposal_id: UUID,
    reviewed_hash: str,
    accepted: bool,
    now: datetime,
) -> LifecycleResult:
    now = _now(now)
    with session.begin_nested():
        user = _user(session, user_id)
        proposal = session.scalar(
            select(PolicyProposal)
            .where(
                PolicyProposal.id == proposal_id,
                PolicyProposal.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if proposal is None:
            raise PolicyLifecycleError("NOT_FOUND", "候选策略不存在", 404)
        canonical = _reviewed(proposal.proposed_configuration, reviewed_hash, accepted)
        if proposal.status == "CONFIRMED" and proposal.confirmed_policy_id is not None:
            policy = _policy(session, user_id, proposal.confirmed_policy_id)
            original = session.scalar(
                select(PolicyVersion).where(
                    PolicyVersion.policy_id == policy.id,
                    PolicyVersion.version_number == 1,
                    PolicyVersion.user_id == user_id,
                )
            )
            if original is None or original.content_hash != reviewed_hash:
                raise PolicyLifecycleError("CONFIRMATION_CONFLICT", "候选确认记录不一致", 409)
            return LifecycleResult.model_validate(original.impact_analysis["lifecycle_result"])
        if proposal.status != "PROPOSED":
            raise PolicyLifecycleError("INVALID_POLICY_STATE", "候选当前不可确认", 409)
        evidence = _evidence(session, user_id, proposal.evidence_ids, now)
        _goal_asset_reference(session, user_id, canonical)
        policy = Policy(
            id=uuid4(),
            user_id=user_id,
            created_at=now,
            name=canonical.get("name") or canonical["type"],
            policy_type=canonical["type"],
            status="CONFIRMED",
            updated_at=now,
        )
        session.add(policy)
        session.flush()
        _, result = _append_version(
            session,
            user,
            policy,
            canonical,
            evidence,
            now,
            None,
            "首次用户确认",
            f"proposal:{proposal_id}",
            reviewed_hash,
            [],
            [],
        )
        proposal.status = "CONFIRMED"
        proposal.confirmed_policy_id = policy.id
        session.flush()
        return result


def change_policy(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    expected_version_id: UUID,
    configuration: dict[str, Any],
    reviewed_hash: str,
    accepted: bool,
    reason: str,
    idempotency_key: str,
    now: datetime,
) -> LifecycleResult:
    now = _now(now)
    canonical = _reviewed(configuration, reviewed_hash, accepted)
    if (
        not reason.strip()
        or len(reason) > 1000
        or not idempotency_key.strip()
        or len(idempotency_key) > 160
    ):
        raise PolicyLifecycleError(
            "INVALID_CHANGE_REQUEST", "修改原因和幂等键不能为空且须在长度范围内"
        )
    request_hash = configuration_hash(
        {
            "user_id": str(user_id),
            "policy_id": str(policy_id),
            "expected_version_id": str(expected_version_id),
            "configuration": canonical,
            "reason": reason,
            "accepted": True,
        }
    )
    with session.begin_nested():
        user = _user(session, user_id)
        policy = _policy(session, user_id, policy_id)
        versions = session.scalars(
            select(PolicyVersion)
            .where(
                PolicyVersion.policy_id == policy_id,
                PolicyVersion.user_id == user_id,
            )
            .order_by(PolicyVersion.version_number)
        ).all()
        for version in versions:
            if version.confirmation.get("request_key") == f"change:{idempotency_key}":
                if version.confirmation.get("request_hash") != request_hash:
                    raise PolicyLifecycleError(
                        "IDEMPOTENCY_CONFLICT", "幂等键已用于不同的策略修改", 409
                    )
                return LifecycleResult.model_validate(version.impact_analysis["lifecycle_result"])
        _refresh_one(session, policy, now)
        previous = versions[-1] if versions else None
        if previous is None or previous.id != expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "策略版本已变化，请重新查看", 409)
        if policy.status not in {"ACTIVE", "CONFIRMED", "SUSPENDED"}:
            raise PolicyLifecycleError("INVALID_POLICY_STATE", "当前策略不能修改或重新激活", 409)
        if canonical["type"] != policy.policy_type:
            raise PolicyLifecycleError("POLICY_TYPE_CHANGE", "已有策略不得更换类型", 409)
        evidence = _evidence(session, user_id, previous.evidence_ids, now)
        evidence = [item for item in evidence if item.source_type != "POLICY_CONFIRMATION"]
        _goal_asset_reference(session, user_id, canonical)
        invalidated, inflight = _invalidation(
            session, user_id, [version.id for version in versions]
        )
        policy.name = canonical.get("name") or policy.name
        _, result = _append_version(
            session,
            user,
            policy,
            canonical,
            evidence,
            now,
            previous,
            reason,
            f"change:{idempotency_key}",
            request_hash,
            invalidated,
            inflight,
        )
        session.flush()
        return result


def _stop_policy(
    session: Session,
    user_id: UUID,
    policy_id: UUID,
    expected_version_id: UUID,
    now: datetime,
    target: Literal["SUSPENDED", "REVOKED"],
) -> LifecycleResult:
    now = _now(now)
    with session.begin_nested():
        _user(session, user_id)
        policy = _policy(session, user_id, policy_id)
        _refresh_one(session, policy, now)
        version = _latest(session, policy)
        if version is None or version.id != expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "策略版本已变化，请重新查看", 409)
        if target == "SUSPENDED" and policy.status in {"REVOKED", "EXPIRED"}:
            raise PolicyLifecycleError("INVALID_POLICY_STATE", "已终止策略不能暂停或复活", 409)
        if policy.status != target:
            policy.status = target
            policy.updated_at = now
        invalidated, inflight = _invalidation(session, user_id, _version_ids(session, policy))
        session.flush()
        return _result(policy, version, now, None, invalidated, inflight)


def suspend_policy(
    session: Session, user_id: UUID, policy_id: UUID, expected_version_id: UUID, now: datetime
) -> LifecycleResult:
    return _stop_policy(session, user_id, policy_id, expected_version_id, now, "SUSPENDED")


def revoke_policy(
    session: Session, user_id: UUID, policy_id: UUID, expected_version_id: UUID, now: datetime
) -> LifecycleResult:
    return _stop_policy(session, user_id, policy_id, expected_version_id, now, "REVOKED")


def refresh_time_states(session: Session, user_id: UUID, now: datetime) -> TimeRefreshResult:
    now = _now(now)
    with session.begin_nested():
        _user(session, user_id)
        policies = session.scalars(
            select(Policy)
            .where(Policy.user_id == user_id)
            .order_by(Policy.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        updated, invalidated, inflight = [], set(), set()
        for policy in policies:
            changed, expired_ids, pending_ids = _refresh_one(session, policy, now)
            if changed:
                updated.append(policy.id)
                invalidated.update(expired_ids)
                inflight.update(pending_ids)
        return TimeRefreshResult(
            updated_policy_ids=updated,
            invalidated_action_ids=sorted(invalidated),
            inflight_action_ids=sorted(inflight),
        )


def is_version_authorized(session: Session, user_id: UUID, version_id: UUID, now: datetime) -> bool:
    now = _now(now)
    version = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.id == version_id,
            PolicyVersion.user_id == user_id,
        )
    )
    if version is None:
        return False
    policy = session.scalar(
        select(Policy)
        .where(Policy.id == version.policy_id, Policy.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if policy is None:
        return False
    current = _latest(session, policy)
    if (
        current is None
        or current.id != version_id
        or effective_status(policy, current, now) != "ACTIVE"
    ):
        return False
    try:
        evidence = _evidence(session, user_id, current.evidence_ids, now, lock=False)
    except PolicyLifecycleError:
        return False
    return any(
        item.evidence_level == "USER_CONFIRMED_POLICY"
        and item.source_type == "POLICY_CONFIRMATION"
        and item.source_ref == str(version_id)
        and item.content == current.confirmation
        and item.content.get("accepted") is True
        for item in evidence
    )
