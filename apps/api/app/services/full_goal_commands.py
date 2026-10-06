"""Read an original FULL goal confirmation without replaying or granting authority."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from app.db.models import AuditEpoch, EvidenceItem, Goal, Policy, PolicyVersion, User
from app.domain.boundary_types import BoundaryModel
from app.services.audit_chain import verify_audit_chain
from app.services.full_goals import (
    MODEL_SOURCE,
    FullGoalConfirmationResponse,
    FullGoalModelContent,
    _model_lineage,
    _original_content,
    _read_snapshot,
    _receipt,
    _version_receipt,
)
from app.services.policy_lifecycle import PolicyLifecycleError, _now
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

MAX_ORIGINAL_MODELS = 1000
Key = Annotated[str, Field(min_length=1, max_length=150)]


class FullGoalOriginalRequest(BoundaryModel):
    expected_version_id: UUID
    expected_epoch_id: UUID
    configuration: dict[str, Any]
    reviewed_full_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reviewed_base_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    accepted: Literal[True] = True
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    idempotency_key: Key


class FullGoalOriginalRecord(BoundaryModel):
    original_request: FullGoalOriginalRequest
    request_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    receipt: FullGoalConfirmationResponse
    original_verified: Literal[True] = True
    audit_chain_verified: Literal[True] = True
    configuration_is_server_canonical: Literal[True] = True
    epoch_archive_verified: Literal[False] = False


class FullGoalCommandLookup(BoundaryModel):
    simulation: Literal[True] = True
    goal_id: UUID
    idempotency_key: Key
    status: Literal["NOT_FOUND", "RECORDED"]
    record: FullGoalOriginalRecord | None = None
    not_found_is_final: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False
    bank_authority: Literal[False] = False
    dedicated_audit_event: Literal[False] = False


def original_request(content: FullGoalModelContent) -> FullGoalOriginalRequest:
    return FullGoalOriginalRequest(
        expected_version_id=content.expected_version_id,
        expected_epoch_id=content.epoch_id,
        configuration=content.full_configuration,
        reviewed_full_hash=content.reviewed.full_hash,
        reviewed_base_hash=content.reviewed.base_hash,
        reason=content.reason,
        idempotency_key=content.idempotency_key,
    )


def lookup_full_goal_command(
    session: Session, user_id: UUID, goal_id: UUID, key: str, now: datetime
) -> FullGoalCommandLookup:
    _read_snapshot(session)
    now = _now(now)
    if not key.strip() or len(key) > 150:
        raise PolicyLifecycleError("INVALID_COMMAND_KEY", "完整目标原键为空或超过长度限制")
    with session.no_autoflush:
        user = session.get(User, user_id)
        goal = session.scalar(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
        if user is None or not user.is_simulated or goal is None:
            raise PolicyLifecycleError("NOT_FOUND", "模拟用户或目标不存在", 404)
        policy = session.scalar(
            select(Policy).where(Policy.id == goal.policy_id, Policy.user_id == user_id)
        )
        if policy is None or policy.policy_type != "goal_saving":
            raise PolicyLifecycleError("INVALID_GOAL_SOURCE", "缺少目标原策略", 409)
        rows = list(
            session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.user_id == user_id,
                    EvidenceItem.source_type == MODEL_SOURCE,
                    EvidenceItem.source_ref == str(goal_id),
                )
                .order_by(EvidenceItem.created_at, EvidenceItem.id)
                .limit(MAX_ORIGINAL_MODELS + 1)
                .execution_options(populate_existing=True)
            )
        )
        if len(rows) > MAX_ORIGINAL_MODELS:
            raise PolicyLifecycleError("ORIGINAL_LOOKUP_CAPACITY", "原模型数量超出查询容量", 409)
        _model_lineage(rows)
        matches = [(row, _original_content(row)) for row in rows]
        matches = [(row, content) for row, content in matches if content.idempotency_key == key]
        if not matches:
            return FullGoalCommandLookup(goal_id=goal_id, idempotency_key=key, status="NOT_FOUND")
        if len(matches) != 1:
            raise PolicyLifecycleError("INVALID_FULL_GOAL_MODEL", "原确认键没有唯一模型", 409)
        row, content = matches[0]
        epoch = session.get(AuditEpoch, content.epoch_id)
        version = session.get(PolicyVersion, content.base_policy_version_id)
        previous = session.get(PolicyVersion, content.expected_version_id)
        if (
            content.user_id != user_id
            or content.goal_id != goal.id
            or content.policy_id != policy.id
            or content.confirmed_at > now
            or epoch is None
            or epoch.user_id != user_id
            or epoch.status not in {"OPEN", "SEALED"}
            or (
                epoch.status == "SEALED"
                and (
                    epoch.sealed_at is None
                    or epoch.sealed_at < epoch.opened_at
                )
            )
            or version is None
            or previous is None
            or previous.user_id != user_id
            or previous.policy_id != policy.id
            or previous.version_number + 1 != version.version_number
            or previous.created_at > content.confirmed_at
        ):
            raise PolicyLifecycleError(
                "INVALID_FULL_GOAL_MODEL", "原确认的用户、轮次或版本已变化", 409
            )
        # Audit opened/sealed timestamps use append time, while confirmations use
        # the trusted simulated business clock. Validate each in its own domain.
        audit = verify_audit_chain(session, user_id, content.epoch_id)
        if audit.status != "VALID" or audit.errors:
            raise PolicyLifecycleError("AUDIT_NOT_VERIFIED", "原确认所在审计链无法验证", 409)
        lifecycle = _version_receipt(content, row, version)
        return FullGoalCommandLookup(
            goal_id=goal_id,
            idempotency_key=key,
            status="RECORDED",
            record=FullGoalOriginalRecord(
                original_request=original_request(content),
                request_hash=content.request_hash,
                receipt=_receipt(row, content, lifecycle, replay=True),
            ),
        )
