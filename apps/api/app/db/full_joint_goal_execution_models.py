"""Separate immutable joint-goal identities; registration/migration is explicit.

Current Goal/MVP/Action/Evidence projections may be archived and removed by a
demo epoch reset. Retain their identifiers and exact original JSON rather than
adding foreign keys that would force deletion of this execution history.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime
from app.db.models import OwnedMixin, owned_args, owned_reference


def _parent_reference() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["plan_id", "user_id", "epoch_id"],
        [
            "full_joint_goal_execution_plans.id",
            "full_joint_goal_execution_plans.user_id",
            "full_joint_goal_execution_plans.epoch_id",
        ],
        ondelete="RESTRICT",
    )


class FullJointGoalExecutionPlan(OwnedMixin, Base):
    __tablename__ = "full_joint_goal_execution_plans"
    epoch_id: Mapped[UUID]
    full_policy_id: Mapped[UUID]
    full_policy_version_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB)
    plan_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("epoch_id", "audit_epochs"),
        ForeignKeyConstraint(
            ["full_policy_id", "epoch_id", "user_id"],
            ["full_policies.id", "full_policies.epoch_id", "full_policies.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["full_policy_version_id", "full_policy_id", "user_id"],
            [
                "full_policy_versions.id",
                "full_policy_versions.policy_id",
                "full_policy_versions.user_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", "epoch_id", name="uq_full_joint_plan_owner_epoch"),
        UniqueConstraint("user_id", "epoch_id", "idempotency_key", name="uq_full_joint_plan_key"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 160", name="key"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND plan_hash ~ '^[0-9a-f]{64}$'", name="hashes"
        ),
        CheckConstraint(
            "jsonb_typeof(request) = 'object' AND jsonb_typeof(plan) = 'object' "
            "AND octet_length(request::text) <= 1048576 "
            "AND octet_length(plan::text) <= 10485760",
            name="json",
        ),
        CheckConstraint("expires_at > created_at", name="time"),
    )


class FullJointGoalExecutionChild(OwnedMixin, Base):
    __tablename__ = "full_joint_goal_execution_children"
    plan_id: Mapped[UUID]
    epoch_id: Mapped[UUID]
    child_number: Mapped[int] = mapped_column(Integer)
    goal_id: Mapped[UUID]
    original_mvp_version_id: Mapped[UUID]
    action_plan_id: Mapped[UUID]
    bank_idempotency_key: Mapped[str] = mapped_column(String(160))
    command: Mapped[dict[str, Any]] = mapped_column(JSONB)
    command_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = owned_args(
        _parent_reference(),
        UniqueConstraint("plan_id", "child_number", name="uq_full_joint_child_order"),
        UniqueConstraint("action_plan_id", name="uq_full_joint_child_action"),
        UniqueConstraint("user_id", "bank_idempotency_key", name="uq_full_joint_child_bank_key"),
        CheckConstraint(
            "child_number BETWEEN 1 AND 8 AND length(bank_idempotency_key) BETWEEN 1 AND 160",
            name="identity",
        ),
        CheckConstraint("command_hash ~ '^[0-9a-f]{64}$'", name="hashes"),
        CheckConstraint(
            "jsonb_typeof(command) = 'object' AND octet_length(command::text) <= 1048576",
            name="json",
        ),
    )


class FullJointGoalExecutionConsent(OwnedMixin, Base):
    __tablename__ = "full_joint_goal_execution_consents"
    plan_id: Mapped[UUID]
    epoch_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[UUID]
    evidence_hash: Mapped[str] = mapped_column(String(64))
    original_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = owned_args(
        _parent_reference(),
        UniqueConstraint("plan_id", name="uq_full_joint_consent_plan"),
        UniqueConstraint(
            "user_id", "epoch_id", "idempotency_key", name="uq_full_joint_consent_key"
        ),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 160", name="key"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND plan_hash ~ '^[0-9a-f]{64}$' "
            "AND evidence_hash ~ '^[0-9a-f]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(request) = 'object' AND jsonb_typeof(original_evidence) = 'object' "
            "AND octet_length(request::text) <= 1048576 "
            "AND octet_length(original_evidence::text) <= 1048576",
            name="json",
        ),
    )
