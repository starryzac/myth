"""Durable simulated delivery metadata. Legacy financial rows keep their byte contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UTCDateTime
from app.db.models import OwnedMixin, owned_args, owned_reference


class CommandOutbox(OwnedMixin, Base):
    __tablename__ = "command_outbox"
    action_plan_id: Mapped[UUID]
    epoch_id: Mapped[UUID]
    protocol_version: Mapped[str] = mapped_column(String(40), server_default="action-delivery-v1")
    root_id: Mapped[UUID]
    request_hash: Mapped[str] = mapped_column(String(64))
    effect_hash: Mapped[str] = mapped_column(String(64))
    bank_idempotency_key: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = owned_args(
        # Actions are archived on a demo reset. Keep this immutable source reference,
        # and the retained epoch FK, rather than deleting durable delivery history.
        owned_reference("epoch_id", "audit_epochs"),
        UniqueConstraint("action_plan_id", name="uq_command_outbox_action"),
        CheckConstraint(
            "protocol_version = 'action-delivery-v1' AND root_id = action_plan_id", name="identity"
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND effect_hash ~ '^[0-9a-f]{64}$' "
            "AND payload_hash ~ '^[0-9a-f]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 1048576",
            name="payload",
        ),
        CheckConstraint(
            "length(bank_idempotency_key) BETWEEN 1 AND 160 AND attempt_count >= 0", name="attempts"
        ),
        CheckConstraint("state IN ('PENDING', 'DELIVERED', 'STOPPED')", name="state"),
        CheckConstraint(
            "updated_at >= created_at AND (published_at IS NULL OR published_at >= created_at) "
            "AND (acknowledged_at IS NULL OR acknowledged_at >= created_at)",
            name="time",
        ),
        Index("ix_command_outbox_pending", "user_id", "state", "created_at", "id"),
    )


class CommandInbox(OwnedMixin, Base):
    __tablename__ = "command_inbox"
    outbox_id: Mapped[UUID]
    consumer_ref: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    attempt_id: Mapped[UUID]
    state: Mapped[str] = mapped_column(String(32), server_default="RECEIVED")
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    source_action_status: Mapped[str | None] = mapped_column(String(24))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = owned_args(
        owned_reference("outbox_id", "command_outbox"),
        UniqueConstraint("outbox_id", "consumer_ref", name="uq_command_inbox_message_consumer"),
        CheckConstraint(
            "length(consumer_ref) BETWEEN 1 AND 80 AND payload_hash ~ '^[0-9a-f]{64}$' "
            "AND attempt_count >= 0",
            name="identity",
        ),
        CheckConstraint(
            "state IN ('RECEIVED', 'PROCESSING', 'WAITING_CONFIRMATION', 'UNRESOLVED', "
            "'SERVICE_RECEIPT_VERIFIED', 'STOPPED', 'FAILED')",
            name="state",
        ),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="result"),
        CheckConstraint(
            "received_at >= created_at AND updated_at >= received_at "
            "AND (started_at IS NULL OR started_at >= received_at) "
            "AND (finished_at IS NULL OR finished_at >= received_at)",
            name="time",
        ),
    )


class CommandDeliveryAttempt(OwnedMixin, Base):
    __tablename__ = "command_delivery_attempts"
    outbox_id: Mapped[UUID]
    inbox_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), server_default="RECEIVED")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source_action_status: Mapped[str | None] = mapped_column(String(24))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = owned_args(
        owned_reference("outbox_id", "command_outbox"),
        owned_reference("inbox_id", "command_inbox"),
        UniqueConstraint("inbox_id", "attempt_number", name="uq_command_delivery_attempt_number"),
        CheckConstraint("attempt_number > 0", name="number"),
        CheckConstraint(
            "state IN ('RECEIVED', 'PROCESSING', 'WAITING_CONFIRMATION', 'UNRESOLVED', "
            "'SERVICE_RECEIPT_VERIFIED', 'STOPPED', 'FAILED')",
            name="state",
        ),
        CheckConstraint(
            "started_at >= created_at AND (finished_at IS NULL OR finished_at >= started_at)",
            name="time",
        ),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="result"),
    )


class FullPolicy(OwnedMixin, Base):
    """Confirmed FULL planning scope, separate from legacy executable bank policies."""

    __tablename__ = "full_policies"
    epoch_id: Mapped[UUID]
    template_name: Mapped[str] = mapped_column(String(64))
    dsl_version: Mapped[str] = mapped_column(String(16), server_default="FULL_V1")
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), server_default="CONFIRMED")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("epoch_id", "audit_epochs"),
        UniqueConstraint("id", "epoch_id", "user_id", name="uq_full_policy_epoch_identity"),
        CheckConstraint(
            "dsl_version = 'FULL_V1' AND template_name IN ('DatedExpensePolicy', "
            "'PeriodicTransferPolicy', 'AssetAuthorizationPolicy', 'RecoveryPolicy', "
            "'GoalAllocationPolicy', 'CrossGoalReallocationPolicy', 'SeasonalReservePolicy', "
            "'InterventionPolicy')",
            name="template",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED')",
            name="status",
        ),
        CheckConstraint("length(name) BETWEEN 1 AND 120 AND updated_at >= created_at", name="time"),
        Index("ix_full_policy_epoch_status", "user_id", "epoch_id", "status", "id"),
    )


class FullPolicyVersion(OwnedMixin, Base):
    __tablename__ = "full_policy_versions"
    policy_id: Mapped[UUID]
    version_number: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    confirmation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    change_reason: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB)
    impact_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = owned_args(
        owned_reference("policy_id", "full_policies"),
        UniqueConstraint("policy_id", "version_number", name="uq_full_policy_version_number"),
        UniqueConstraint("id", "policy_id", "user_id", name="uq_full_policy_version_identity"),
        CheckConstraint("version_number > 0", name="number"),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$' "
            "AND (previous_hash IS NULL OR previous_hash ~ '^[0-9a-f]{64}$')",
            name="hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(configuration) = 'object' "
            "AND octet_length(configuration::text) <= 1048576 "
            "AND jsonb_typeof(confirmation) = 'object' "
            "AND jsonb_typeof(evidence_ids) = 'array' "
            "AND jsonb_typeof(impact_analysis) = 'object'",
            name="json",
        ),
        CheckConstraint(
            "confirmed_at >= created_at AND (valid_until IS NULL OR valid_until > valid_from)",
            name="time",
        ),
    )


class FullPolicyCommand(OwnedMixin, Base):
    __tablename__ = "full_policy_commands"
    epoch_id: Mapped[UUID]
    policy_id: Mapped[UUID]
    version_id: Mapped[UUID]
    command_number: Mapped[int] = mapped_column(Integer)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    previous_status: Mapped[str | None] = mapped_column(String(24))
    resulting_status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = owned_args(
        owned_reference("epoch_id", "audit_epochs"),
        ForeignKeyConstraint(
            ["policy_id", "epoch_id", "user_id"],
            ["full_policies.id", "full_policies.epoch_id", "full_policies.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["version_id", "policy_id", "user_id"],
            [
                "full_policy_versions.id",
                "full_policy_versions.policy_id",
                "full_policy_versions.user_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_full_policy_command_key"),
        UniqueConstraint("policy_id", "command_number", name="uq_full_policy_command_number"),
        CheckConstraint("command_number > 0", name="number"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND (previous_hash IS NULL OR previous_hash ~ '^[0-9a-f]{64}$')",
            name="hashes",
        ),
        CheckConstraint(
            "kind IN ('CREATE', 'CHANGE', 'SUSPEND', 'REVOKE', 'RESUME', 'REFRESH_TIME') "
            "AND length(idempotency_key) BETWEEN 1 AND 160",
            name="kind",
        ),
        CheckConstraint(
            "resulting_status IN ('ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED') "
            "AND (previous_status IS NULL OR previous_status IN "
            "('ACTIVE', 'CONFIRMED', 'SUSPENDED', 'EXPIRED', 'REVOKED'))",
            name="status",
        ),
        CheckConstraint(
            "jsonb_typeof(request) = 'object' AND octet_length(request::text) <= 1048576 "
            "AND jsonb_typeof(result) = 'object' AND octet_length(result::text) <= 1048576",
            name="json",
        ),
    )


class InterventionOutbox(OwnedMixin, Base):
    """Retained notifications; their payload cannot authorize a financial action."""

    __tablename__ = "intervention_outbox"
    epoch_id: Mapped[UUID]
    protocol_version: Mapped[str] = mapped_column(
        String(40), server_default="full-intervention-message-v1"
    )
    source_kind: Mapped[str] = mapped_column(String(32))
    source_run_id: Mapped[UUID]
    source_trace_hash: Mapped[str] = mapped_column(String(64))
    semantic_key: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[UUID | None]
    question_id: Mapped[UUID | None]
    question_revision: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), server_default="PENDING")
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = owned_args(
        owned_reference("epoch_id", "audit_epochs"),
        UniqueConstraint("user_id", "epoch_id", "semantic_key", name="uq_intervention_semantic"),
        CheckConstraint("protocol_version = 'full-intervention-message-v1'", name="protocol"),
        CheckConstraint(
            "source_trace_hash ~ '^[0-9a-f]{64}$' AND semantic_key ~ '^[0-9a-f]{64}$' "
            "AND payload_hash ~ '^[0-9a-f]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "(source_kind = 'QUESTION' AND session_id IS NOT NULL AND question_id IS NOT NULL "
            "AND question_revision IS NOT NULL AND question_revision >= 1) OR "
            "(source_kind IN ('SINGLE_ACTION_BOUNDARY', 'GLOBAL_ACTION_SET_BOUNDARY') "
            "AND session_id IS NULL "
            "AND question_id IS NULL AND question_revision IS NULL)",
            name="source",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 1048576",
            name="payload",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'ACKNOWLEDGED', 'INVALIDATED', 'RECORDED_ONLY', 'DEFERRED')",
            name="state",
        ),
        CheckConstraint("available_at >= created_at AND updated_at >= created_at", name="time"),
        Index("ix_intervention_pending", "user_id", "epoch_id", "state", "available_at", "id"),
    )


class InterventionInbox(OwnedMixin, Base):
    """One fixed consumer claim and immutable explicit reading acknowledgement."""

    __tablename__ = "intervention_inbox"
    outbox_id: Mapped[UUID]
    consumer_ref: Mapped[str] = mapped_column(String(80), server_default="intervention-center-v1")
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), server_default="RECEIVED")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledgment_key: Mapped[str | None] = mapped_column(String(160))
    acknowledgment_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    acknowledgment_request_hash: Mapped[str | None] = mapped_column(String(64))
    original_receipt: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __table_args__ = owned_args(
        owned_reference("outbox_id", "intervention_outbox"),
        UniqueConstraint("outbox_id", "consumer_ref", name="uq_intervention_consumer"),
        UniqueConstraint("user_id", "acknowledgment_key", name="uq_intervention_ack_key"),
        CheckConstraint(
            "consumer_ref = 'intervention-center-v1' AND payload_hash ~ '^[0-9a-f]{64}$'",
            name="identity",
        ),
        CheckConstraint("state IN ('RECEIVED', 'ACKNOWLEDGED', 'INVALIDATED')", name="state"),
        CheckConstraint(
            "(state = 'ACKNOWLEDGED' AND acknowledged_at IS NOT NULL "
            "AND acknowledgment_key IS NOT NULL AND length(acknowledgment_key) BETWEEN 1 AND 160 "
            "AND acknowledgment_request IS NOT NULL AND acknowledgment_request_hash IS NOT NULL "
            "AND original_receipt IS NOT NULL "
            "AND acknowledgment_request_hash ~ '^[0-9a-f]{64}$') OR "
            "(state <> 'ACKNOWLEDGED' AND acknowledged_at IS NULL "
            "AND acknowledgment_key IS NULL AND acknowledgment_request IS NULL "
            "AND acknowledgment_request_hash IS NULL AND original_receipt IS NULL)",
            name="acknowledgment",
        ),
        CheckConstraint(
            "(acknowledgment_request IS NULL OR (jsonb_typeof(acknowledgment_request) = 'object' "
            "AND octet_length(acknowledgment_request::text) <= 1048576)) AND "
            "(original_receipt IS NULL OR (jsonb_typeof(original_receipt) = 'object' "
            "AND octet_length(original_receipt::text) <= 1048576))",
            name="json",
        ),
        CheckConstraint(
            "received_at >= created_at AND updated_at >= received_at "
            "AND (acknowledged_at IS NULL OR acknowledged_at >= received_at)",
            name="time",
        ),
    )


class FullAssetExecutionPortfolio(OwnedMixin, Base):
    """Immutable server-computed whole portfolio; financial state comes from original bank rows."""

    __tablename__ = "full_asset_execution_portfolios"
    epoch_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    portfolio: Mapped[dict[str, Any]] = mapped_column(JSONB)
    portfolio_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("epoch_id", "audit_epochs"),
        UniqueConstraint("id", "user_id", "epoch_id", name="uq_full_asset_portfolio_owner_epoch"),
        UniqueConstraint(
            "user_id", "epoch_id", "idempotency_key", name="uq_full_asset_portfolio_key"
        ),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 160", name="key"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND portfolio_hash ~ '^[0-9a-f]{64}$'", name="hashes"
        ),
        CheckConstraint(
            "jsonb_typeof(request) = 'object' AND jsonb_typeof(portfolio) = 'object' "
            "AND octet_length(request::text) <= 1048576 "
            "AND octet_length(portfolio::text) <= 1048576",
            name="json",
        ),
        CheckConstraint("expires_at > created_at", name="time"),
    )


class FullAssetExecutionBatch(OwnedMixin, Base):
    """One immutable ordered child identity, retained before the child bank action exists."""

    __tablename__ = "full_asset_execution_batches"
    portfolio_id: Mapped[UUID]
    epoch_id: Mapped[UUID]
    batch_number: Mapped[int] = mapped_column(Integer)
    action_plan_id: Mapped[UUID]
    bank_idempotency_key: Mapped[str] = mapped_column(String(160))
    command: Mapped[dict[str, Any]] = mapped_column(JSONB)
    command_hash: Mapped[str] = mapped_column(String(64))
    catalogue_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_catalog_versions.id", ondelete="RESTRICT")
    )
    product_record_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = owned_args(
        ForeignKeyConstraint(
            ["portfolio_id", "user_id", "epoch_id"],
            [
                "full_asset_execution_portfolios.id",
                "full_asset_execution_portfolios.user_id",
                "full_asset_execution_portfolios.epoch_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("portfolio_id", "batch_number", name="uq_full_asset_batch_order"),
        UniqueConstraint("action_plan_id", name="uq_full_asset_batch_action"),
        UniqueConstraint("user_id", "bank_idempotency_key", name="uq_full_asset_batch_bank_key"),
        CheckConstraint(
            "batch_number BETWEEN 1 AND 4 AND length(bank_idempotency_key) BETWEEN 1 AND 160",
            name="identity",
        ),
        CheckConstraint(
            "command_hash ~ '^[0-9a-f]{64}$' AND product_record_hash ~ '^[0-9a-f]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(command) = 'object' AND octet_length(command::text) <= 1048576",
            name="json",
        ),
    )


class FullAssetExecutionConsent(OwnedMixin, Base):
    """Explicit consent to exactly one immutable portfolio and epoch."""

    __tablename__ = "full_asset_execution_consents"
    portfolio_id: Mapped[UUID]
    epoch_id: Mapped[UUID]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    portfolio_hash: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[UUID]
    evidence_hash: Mapped[str] = mapped_column(String(64))
    original_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = owned_args(
        ForeignKeyConstraint(
            ["portfolio_id", "user_id", "epoch_id"],
            [
                "full_asset_execution_portfolios.id",
                "full_asset_execution_portfolios.user_id",
                "full_asset_execution_portfolios.epoch_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("portfolio_id", name="uq_full_asset_consent_portfolio"),
        UniqueConstraint(
            "user_id", "epoch_id", "idempotency_key", name="uq_full_asset_consent_key"
        ),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 160", name="key"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$' AND portfolio_hash ~ '^[0-9a-f]{64}$' "
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
