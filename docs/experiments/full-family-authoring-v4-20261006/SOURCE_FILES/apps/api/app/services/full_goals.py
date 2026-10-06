"""Explicit FULL goal extensions backed by original MVP version confirmation.

An append-only model evidence row describes FULL planning fields. It grants no
bank authority and claims no dedicated typed audit event. Existing execution
continues to verify the original goal_saving policy and action contracts.
"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4

from app.db.audit_guard import audit_command_guard, transaction_gate
from app.db.models import AuditEpoch, EvidenceItem, Goal, Policy, PolicyVersion, User
from app.domain.boundary_types import BoundaryModel
from app.domain.full_policy_configuration import LongTermGoalPolicy
from app.domain.policy_configuration import configuration_hash, validate_configuration
from app.services.audit_chain import current_audit_epoch
from app.services.policy_lifecycle import (
    LifecycleResult,
    PolicyLifecycleError,
    _now,
    _window,
    change_policy,
    effective_status,
    is_version_authorized,
)
from app.services.policy_preview import preview_change
from app.services.policy_preview_types import PolicyChangePreviewResponse
from pydantic import Field, StrictBool, model_validator
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MODEL_SOURCE = "FULL_GOAL_MODEL_V1"
MODEL_PROTOCOL: Literal["full-goal-model-v1"] = "full-goal-model-v1"
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ReviewedGoalHashes(BoundaryModel):
    full_hash: Hash
    base_hash: Hash


class FullGoalModelContent(BoundaryModel):
    protocol: Literal["full-goal-model-v1"] = MODEL_PROTOCOL
    user_id: UUID
    epoch_id: UUID
    goal_id: UUID
    policy_id: UUID
    base_policy_version_id: UUID
    expected_version_id: UUID
    full_configuration: dict[str, Any]
    full_hash: Hash
    base_hash: Hash
    reviewed: ReviewedGoalHashes
    accepted: StrictBool
    confirmed_at: datetime
    idempotency_key: Annotated[str, Field(min_length=1, max_length=150)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    request_hash: Hash

    @model_validator(mode="after")
    def original_confirmation(self) -> Self:
        if self.accepted is not True:
            raise ValueError("A FULL goal model requires actual explicit confirmation")
        if not self.idempotency_key.strip() or not self.reason.strip():
            raise ValueError("The original confirmation key and reason must not be blank")
        if self.reviewed.full_hash != self.full_hash or self.reviewed.base_hash != self.base_hash:
            raise ValueError("Both original reviewed hashes must match")
        return self


class FullGoalModelResponse(BoundaryModel):
    simulation: Literal[True] = True
    goal_id: UUID
    policy_id: UUID
    base_policy_version_id: UUID
    epoch_id: UUID
    status: Literal["VERIFIED", "MODEL_MISSING"]
    policy_effective_status: Literal["ACTIVE", "CONFIRMED"] | None = None
    evidence_id: UUID | None = None
    evidence_hash: Hash | None = None
    full_configuration: dict[str, Any] | None = None
    full_configuration_hash: Hash | None = None
    base_configuration_hash: Hash | None = None
    confirmed_at: datetime | None = None
    bank_authority: Literal[False] = False
    dedicated_audit_event: Literal[False] = False


class FullGoalPreviewResponse(BoundaryModel):
    simulation: Literal[True] = True
    preview_only: Literal[True] = True
    grants_authority: Literal[False] = False
    goal_id: UUID
    epoch_id: UUID
    expected_version_id: UUID
    full_configuration: dict[str, Any]
    full_configuration_hash: Hash
    base_configuration: dict[str, Any]
    base_configuration_hash: Hash
    base_policy_impact: PolicyChangePreviewResponse
    extra_fields_in_base_impact: Literal[False] = False
    notes: list[str]


class FullGoalConfirmationResponse(BoundaryModel):
    simulation: Literal[True] = True
    goal_id: UUID
    epoch_id: UUID
    lifecycle: LifecycleResult
    evidence_id: UUID
    evidence_hash: Hash
    full_configuration: dict[str, Any]
    full_configuration_hash: Hash
    base_configuration_hash: Hash
    confirmed_at: datetime
    idempotent_replay: bool
    receipt_is_current_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    dedicated_audit_event: Literal[False] = False


def canonical_goal_bridge(configuration: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate one FULL model and its exact conservative original MVP projection."""
    try:
        full = LongTermGoalPolicy.model_validate(configuration).model_dump(mode="json")
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError("INVALID_FULL_GOAL_CONFIGURATION", "完整目标配置无效") from error
    if full["cross_goal_reallocation_allowed"]:
        raise PolicyLifecycleError(
            "CROSS_GOAL_AUTHORITY_NOT_IMPLEMENTED",
            "跨目标回拨尚无已实现的独立确认服务，不能通过目标模型启用",
            409,
        )
    base = validate_configuration(
        {
            "type": "goal_saving",
            "name": full["name"],
            "valid_from": full["valid_from"],
            "valid_until": full["valid_until"],
            "target_cents": full["target_cents"],
            "deadline": full["deadline"],
            "monthly_contribution": full["monthly_contribution"],
            "priority": {
                "importance": full["importance"],
                "minimum_cents": full["minimum_guarantee_cents"],
                "reducible": full["allow_partial"],
                "deferrable": full["allow_deferral"],
            },
            "cross_goal_reallocation_allowed": False,
            "asset_policy_id": full["asset_policy_id"],
        }
    )
    return full, base


def _read_snapshot(session: Session) -> None:
    if (
        session.new
        or session.dirty
        or session.deleted
        or session.connection().get_isolation_level() != "REPEATABLE READ"
        or session.scalar(text("SHOW transaction_read_only")) != "on"
    ):
        raise PolicyLifecycleError(
            "INVALID_READ_SNAPSHOT", "完整目标读取及预览需要干净的只读可重复读事务", 409
        )


def _binding(
    session: Session, user_id: UUID, goal_id: UUID, *, lock: bool = False
) -> tuple[Goal, Policy, PolicyVersion, AuditEpoch]:
    if lock:
        transaction_gate(session, user_id)
    user_query = select(User).where(User.id == user_id)
    if lock:
        user_query = user_query.with_for_update()
    user = session.scalar(user_query.execution_options(populate_existing=True))
    if user is None or not user.is_simulated:
        raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
    goal_query = select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    if lock:
        goal_query = goal_query.with_for_update()
    goal = session.scalar(goal_query.execution_options(populate_existing=True))
    if goal is None:
        raise PolicyLifecycleError("NOT_FOUND", "目标不存在", 404)
    policy = session.scalar(
        select(Policy)
        .where(Policy.id == goal.policy_id, Policy.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    version = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.policy_id == goal.policy_id, PolicyVersion.user_id == user_id)
        .order_by(PolicyVersion.version_number.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )
    epoch = current_audit_epoch(session, user_id)
    if policy is None or version is None or epoch is None or epoch.status != "OPEN":
        raise PolicyLifecycleError("INVALID_GOAL_SOURCE", "目标缺少当前原策略或审计轮次", 409)
    _base_projection(goal, policy, version)
    return goal, policy, version, epoch


def _base_projection(goal: Goal, policy: Policy, version: PolicyVersion) -> dict[str, Any]:
    try:
        base = validate_configuration(version.configuration)
        priority, monthly = base["priority"], base["monthly_contribution"]
        if (
            policy.policy_type != "goal_saving"
            or base["type"] != "goal_saving"
            or (goal.user_id, policy.user_id, version.user_id) != (goal.user_id,) * 3
            or goal.policy_id != policy.id
            or version.policy_id != policy.id
            or goal.policy_version_id != version.id
            or goal.name != (base.get("name") or policy.name)
            or configuration_hash(base) != version.content_hash
            or goal.target_cents != base["target_cents"]
            or goal.deadline.isoformat() != base["deadline"]
            or (goal.monthly_min_cents, goal.monthly_target_cents, goal.monthly_max_cents)
            != (monthly["min_cents"], monthly["target_cents"], monthly["max_cents"])
            or (goal.importance, goal.minimum_protection_cents, goal.reducible, goal.deferrable)
            != (
                priority["importance"],
                priority["minimum_cents"],
                priority["reducible"],
                priority["deferrable"],
            )
            or goal.cross_goal_reallocation_allowed != base["cross_goal_reallocation_allowed"]
            or (str(goal.asset_policy_id) if goal.asset_policy_id else None)
            != base["asset_policy_id"]
        ):
            raise ValueError("The original current goal projection differs from its policy")
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        raise PolicyLifecycleError(
            "INVALID_GOAL_SOURCE", "当前目标与原策略配置不一致", 409
        ) from error
    return base


def _original_content(row: EvidenceItem) -> FullGoalModelContent:
    try:
        content = FullGoalModelContent.model_validate_json(
            json.dumps(row.content, ensure_ascii=False, allow_nan=False)
        )
        full, base = canonical_goal_bridge(content.full_configuration)
        if (
            row.evidence_level != "USER_CONFIRMED_POLICY"
            or row.source_type != MODEL_SOURCE
            or row.source_ref != str(content.goal_id)
            or row.user_id != content.user_id
            or row.status != "VALID"
            or row.content_hash != configuration_hash(row.content)
            or content.full_configuration != full
            or content.full_hash != configuration_hash(full)
            or content.base_hash != configuration_hash(base)
            or row.created_at != content.confirmed_at
            or row.observed_at != content.confirmed_at
            or row.valid_from != content.confirmed_at
            or content.request_hash
            != _request_hash(
                content.user_id,
                content.epoch_id,
                content.goal_id,
                content.expected_version_id,
                full,
                content.reason,
                content.idempotency_key,
            )
        ):
            raise ValueError("FULL goal original metadata or hashes differ")
        return content
    except (TypeError, ValueError) as error:
        raise PolicyLifecycleError(
            "INVALID_FULL_GOAL_MODEL", "完整目标原确认模型或摘要不一致", 409
        ) from error


def _model_rows(session: Session, user_id: UUID, goal_id: UUID) -> list[EvidenceItem]:
    rows = list(
        session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.user_id == user_id,
                EvidenceItem.source_type == MODEL_SOURCE,
                EvidenceItem.source_ref == str(goal_id),
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
            .execution_options(populate_existing=True)
        )
    )
    _model_lineage(rows)
    return rows


def _model_lineage(rows: list[EvidenceItem]) -> None:
    originals = {row.id: (row, _original_content(row)) for row in rows}
    for row, content in originals.values():
        if row.supersedes_id is None:
            continue
        previous = originals.get(row.supersedes_id)
        if previous is None:
            raise PolicyLifecycleError(
                "INVALID_FULL_GOAL_MODEL", "完整目标模型缺少原先模型来源", 409
            )
        before, original = previous
        if (
            before.id == row.id
            or before.user_id != row.user_id
            or original.goal_id != content.goal_id
            or original.epoch_id != content.epoch_id
            or original.policy_id != content.policy_id
            or before.created_at > row.created_at
            or original.base_policy_version_id == content.base_policy_version_id
        ):
            raise PolicyLifecycleError(
                "INVALID_FULL_GOAL_MODEL", "完整目标模型不能替代非原先模型", 409
            )
    done: set[UUID] = set()
    for row in rows:
        current: UUID | None = row.id
        path: set[UUID] = set()
        while current is not None and current not in done:
            if current in path:
                raise PolicyLifecycleError(
                    "INVALID_FULL_GOAL_MODEL", "完整目标模型来源不能成环", 409
                )
            path.add(current)
            current = originals[current][0].supersedes_id
        done.update(path)


def verify_full_goal_model_original(
    row: EvidenceItem,
    goal: Goal,
    policy: Policy,
    version: PolicyVersion,
    epoch: AuditEpoch,
    now: datetime,
) -> FullGoalModelContent:
    """Verify metadata and both exact models; current base authorization is checked separately."""
    content = _original_content(row)
    base = _base_projection(goal, policy, version)
    _, mapped = canonical_goal_bridge(content.full_configuration)
    _version_receipt(content, row, version)
    if (
        content.goal_id != goal.id
        or content.user_id != goal.user_id
        or content.policy_id != policy.id
        or content.epoch_id != epoch.id
        or epoch.user_id != goal.user_id
        or epoch.status != "OPEN"
        or content.base_policy_version_id != version.id
        or content.base_hash != version.content_hash
        or mapped != base
        or version.confirmed_at != content.confirmed_at
        or content.confirmed_at > now
        or version.confirmation.get("accepted") is not True
        or version.confirmation.get("reviewed_hash") != content.reviewed.base_hash
        or version.confirmation.get("request_key") != f"change:full-goal:{content.idempotency_key}"
        or row.valid_to != version.valid_until
    ):
        raise PolicyLifecycleError("INVALID_FULL_GOAL_MODEL", "完整目标未绑定当前原确认版本", 409)
    return content


def read_full_goal_model(
    session: Session, user_id: UUID, goal_id: UUID, now: datetime
) -> FullGoalModelResponse:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        goal, policy, version, epoch = _binding(session, user_id, goal_id)
        if not is_version_authorized(session, user_id, version.id, now):
            raise PolicyLifecycleError("POLICY_NOT_AUTHORIZED", "目标原版本当前没有有效授权", 409)
        matching = []
        for row in _model_rows(session, user_id, goal_id):
            content = _original_content(row)
            if content.base_policy_version_id == version.id:
                matching.append(row)
        if len(matching) > 1:
            raise PolicyLifecycleError(
                "INVALID_FULL_GOAL_MODEL", "当前版本的完整模型不是唯一原件", 409
            )
        if not matching:
            return FullGoalModelResponse(
                goal_id=goal.id,
                policy_id=policy.id,
                base_policy_version_id=version.id,
                epoch_id=epoch.id,
                status="MODEL_MISSING",
            )
        row = matching[0]
        content = verify_full_goal_model_original(row, goal, policy, version, epoch, now)
        actual_status = effective_status(policy, version, now)
        if actual_status == "ACTIVE":
            model_status: Literal["ACTIVE", "CONFIRMED"] = "ACTIVE"
        elif actual_status == "CONFIRMED":
            model_status = "CONFIRMED"
        else:
            raise PolicyLifecycleError("POLICY_NOT_AUTHORIZED", "原目标授权状态已变化", 409)
        return FullGoalModelResponse(
            goal_id=goal.id,
            policy_id=policy.id,
            base_policy_version_id=version.id,
            epoch_id=epoch.id,
            status="VERIFIED",
            evidence_id=row.id,
            policy_effective_status=model_status,
            evidence_hash=row.content_hash,
            full_configuration=content.full_configuration,
            full_configuration_hash=content.full_hash,
            base_configuration_hash=content.base_hash,
            confirmed_at=content.confirmed_at,
        )


def preview_full_goal_model(
    session: Session,
    user_id: UUID,
    goal_id: UUID,
    expected_version_id: UUID,
    configuration: dict[str, Any],
    now: datetime,
) -> FullGoalPreviewResponse:
    _read_snapshot(session)
    now = _now(now)
    with session.no_autoflush:
        goal, _, version, epoch = _binding(session, user_id, goal_id)
        if version.id != expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "原目标策略已变化，请重新预览", 409)
        full, base = canonical_goal_bridge(configuration)
        impact = preview_change(session, user_id, goal.policy_id, expected_version_id, base, now)
        return FullGoalPreviewResponse(
            goal_id=goal_id,
            epoch_id=epoch.id,
            expected_version_id=expected_version_id,
            full_configuration=full,
            full_configuration_hash=configuration_hash(full),
            base_configuration=base,
            base_configuration_hash=configuration_hash(base),
            base_policy_impact=impact,
            notes=[
                "原现金影响由实际 MVP 策略预览计算。",
                "允许部分完成、延期成本等额外模型字段不扩大原银行权限；完整联合计划单独计算。",
            ],
        )


def _request_hash(
    user_id: UUID,
    epoch_id: UUID,
    goal_id: UUID,
    expected_version_id: UUID,
    full: dict[str, Any],
    reason: str,
    key: str,
) -> str:
    return configuration_hash(
        {
            "protocol": MODEL_PROTOCOL,
            "user_id": str(user_id),
            "epoch_id": str(epoch_id),
            "goal_id": str(goal_id),
            "expected_version_id": str(expected_version_id),
            "full_configuration": full,
            "reason": reason,
            "idempotency_key": key,
            "accepted": True,
        }
    )


def _confirmation_window(user: User, base: dict[str, Any], now: datetime) -> None:
    # Evidence begins at actual consent time and cannot end before that time.
    # Future-start policies remain valid confirmations without current authority.
    _, end = _window(user, base, now)
    if end is not None and end <= now:
        raise PolicyLifecycleError(
            "EXPIRED_FULL_GOAL_WINDOW", "完整目标的原有效期已结束，请先复核新的有效期", 422
        )


def _receipt(
    row: EvidenceItem, content: FullGoalModelContent, lifecycle: LifecycleResult, *, replay: bool
) -> FullGoalConfirmationResponse:
    return FullGoalConfirmationResponse(
        goal_id=content.goal_id,
        epoch_id=content.epoch_id,
        lifecycle=lifecycle,
        evidence_id=row.id,
        evidence_hash=row.content_hash,
        full_configuration=content.full_configuration,
        full_configuration_hash=content.full_hash,
        base_configuration_hash=content.base_hash,
        confirmed_at=content.confirmed_at,
        idempotent_replay=replay,
    )


def _version_receipt(
    content: FullGoalModelContent, row: EvidenceItem, version: PolicyVersion
) -> LifecycleResult:
    """Historical replays must bind the same original canonical policy receipt."""
    try:
        _, base = canonical_goal_bridge(content.full_configuration)
        expected = {
            "accepted": True,
            "user_id": str(content.user_id),
            "policy_id": str(content.policy_id),
            "version_id": str(version.id),
            "reviewed_hash": content.base_hash,
            "confirmed_at": content.confirmed_at.isoformat(),
            "request_key": f"change:full-goal:{content.idempotency_key}",
            "request_hash": configuration_hash(
                {
                    "user_id": str(content.user_id),
                    "policy_id": str(content.policy_id),
                    "expected_version_id": str(content.expected_version_id),
                    "configuration": base,
                    "reason": content.reason,
                    "accepted": True,
                }
            ),
        }
        lifecycle = LifecycleResult.model_validate(version.impact_analysis["lifecycle_result"])
        if (
            version.user_id != content.user_id
            or version.policy_id != content.policy_id
            or version.id != content.base_policy_version_id
            or version.configuration != base
            or version.content_hash != content.base_hash
            or configuration_hash(version.configuration) != content.base_hash
            or version.confirmed_at != content.confirmed_at
            or row.valid_to != version.valid_until
            or any(version.confirmation.get(key) != value for key, value in expected.items())
            or version.confirmation.get("accepted") is not True
            or lifecycle.current_version_id != version.id
            or lifecycle.policy_id != content.policy_id
            or lifecycle.previous_version_id != content.expected_version_id
        ):
            raise ValueError("Historical FULL receipt differs from the original base version")
        return lifecycle
    except (TypeError, ValueError, KeyError) as error:
        raise PolicyLifecycleError(
            "INVALID_FULL_GOAL_MODEL", "完整目标的原策略确认回执已变化", 409
        ) from error


def confirm_full_goal_model(
    session: Session,
    user_id: UUID,
    goal_id: UUID,
    expected_version_id: UUID,
    expected_epoch_id: UUID,
    configuration: dict[str, Any],
    reviewed_full_hash: str,
    reviewed_base_hash: str,
    accepted: bool,
    reason: str,
    idempotency_key: str,
    now: datetime,
) -> FullGoalConfirmationResponse:
    if session.new or session.dirty or session.deleted:
        raise PolicyLifecycleError(
            "INVALID_WRITE_CONTEXT", "完整目标确认不能混入其他未提交变更", 409
        )
    now = _now(now)
    full, base = canonical_goal_bridge(configuration)
    if (
        accepted is not True
        or reviewed_full_hash != configuration_hash(full)
        or reviewed_base_hash != configuration_hash(base)
    ):
        raise PolicyLifecycleError(
            "CONFIRMATION_REQUIRED", "请明确复核并确认完整和原执行模型两份摘要", 409
        )
    if (
        not reason.strip()
        or len(reason) > 1000
        or not idempotency_key.strip()
        or len(idempotency_key) > 150
    ):
        # The ten-character prefix must also fit the original 160-character key.
        raise PolicyLifecycleError(
            "INVALID_CHANGE_REQUEST", "原因和幂等键不能为空且须满足原长度限制"
        )
    request_hash = _request_hash(
        user_id, expected_epoch_id, goal_id, expected_version_id, full, reason, idempotency_key
    )
    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    with audit_command_guard(engine, user_id), session.begin_nested():
        goal, policy, version, epoch = _binding(session, user_id, goal_id, lock=True)
        if epoch.id != expected_epoch_id:
            raise PolicyLifecycleError("STALE_DEMO_EPOCH", "目标确认所在模拟轮次已变化", 409)
        originals = _model_rows(session, user_id, goal_id)
        prior: EvidenceItem | None = None
        for row in originals:
            content = _original_content(row)
            if content.epoch_id == epoch.id:
                prior = row
            if content.idempotency_key != idempotency_key:
                continue
            if content.request_hash != request_hash or content.epoch_id != epoch.id:
                raise PolicyLifecycleError(
                    "IDEMPOTENCY_CONFLICT", "原幂等键已用于不同的完整目标确认", 409
                )
            original_version = session.get(PolicyVersion, content.base_policy_version_id)
            if original_version is None:
                raise PolicyLifecycleError(
                    "INVALID_FULL_GOAL_MODEL", "原确认回执所绑定版本已变化", 409
                )
            lifecycle = _version_receipt(content, row, original_version)
            return _receipt(row, content, lifecycle, replay=True)
        if version.id != expected_version_id:
            raise PolicyLifecycleError("STALE_POLICY_VERSION", "原目标策略已变化，请重新预览", 409)
        user = session.get(User, user_id)
        if user is None:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户不存在", 404)
        _confirmation_window(user, base, now)
        lifecycle = change_policy(
            session,
            user_id,
            policy.id,
            expected_version_id,
            base,
            reviewed_base_hash,
            accepted,
            reason,
            f"full-goal:{idempotency_key}",
            now,
        )
        changed = session.get(PolicyVersion, lifecycle.current_version_id)
        if changed is None or changed.confirmed_at != now:
            raise PolicyLifecycleError(
                "INVALID_FULL_GOAL_MODEL", "原策略变更未返回本次完整确认版本", 409
            )
        content = FullGoalModelContent(
            user_id=user_id,
            epoch_id=epoch.id,
            goal_id=goal_id,
            policy_id=policy.id,
            base_policy_version_id=changed.id,
            expected_version_id=expected_version_id,
            full_configuration=full,
            full_hash=reviewed_full_hash,
            base_hash=reviewed_base_hash,
            reviewed=ReviewedGoalHashes(full_hash=reviewed_full_hash, base_hash=reviewed_base_hash),
            accepted=True,
            confirmed_at=now,
            idempotency_key=idempotency_key,
            reason=reason,
            request_hash=request_hash,
        )
        raw = content.model_dump(mode="json")
        row = EvidenceItem(
            id=uuid4(),
            user_id=user_id,
            created_at=now,
            evidence_level="USER_CONFIRMED_POLICY",
            source_type=MODEL_SOURCE,
            source_ref=str(goal_id),
            content=raw,
            content_hash=configuration_hash(raw),
            observed_at=now,
            valid_from=now,
            valid_to=changed.valid_until,
            status="VALID",
            supersedes_id=prior.id if prior is not None else None,
        )
        session.add(row)
        session.flush()
        return _receipt(row, content, lifecycle, replay=False)
