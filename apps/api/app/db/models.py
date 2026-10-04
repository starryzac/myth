"""Application facts and independent simulated bank economic records."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import SchemaItem

from app.db.base import Base, IdentityMixin, MoneyCents, UTCDateTime

JsonObject = dict[str, Any]


def owned_args(*constraints: SchemaItem) -> tuple[SchemaItem, ...]:
    return (UniqueConstraint("id", "user_id"), *constraints)


def owned_reference(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [column, "user_id"], [f"{table}.id", f"{table}.user_id"], ondelete="RESTRICT"
    )


class OwnedMixin(IdentityMixin):
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)


class User(IdentityMixin, Base):
    __tablename__ = "users"
    external_ref: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Shanghai")
    is_simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    __table_args__ = (CheckConstraint("is_simulated", name="simulation_only"),)


class Account(OwnedMixin, Base):
    __tablename__ = "accounts"
    external_ref: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(120))
    bank_code: Mapped[str] = mapped_column(String(16), server_default="ICBC")
    account_type: Mapped[str] = mapped_column(String(24), server_default="CASH")
    currency: Mapped[str] = mapped_column(String(3), server_default="CNY")
    balance_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    __table_args__ = owned_args(
        UniqueConstraint("user_id", "external_ref", name="uq_accounts_user_external_ref"),
        CheckConstraint("balance_cents >= 0", name="nonnegative_balance"),
        CheckConstraint("bank_code = 'ICBC' AND currency = 'CNY'", name="icbc_cny_only"),
        CheckConstraint(
            "account_type IN ('CASH', 'GOAL', 'CREDIT_CARD', 'CASH_MANAGEMENT', 'FIXED_DEPOSIT')",
            name="account_type",
        ),
    )


class EvidenceItem(OwnedMixin, Base):
    __tablename__ = "evidence_items"
    evidence_level: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(48))
    source_ref: Mapped[str] = mapped_column(String(160))
    content: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime())
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    supersedes_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(24), server_default="VALID")
    __table_args__ = owned_args(
        owned_reference("supersedes_id", "evidence_items"),
        CheckConstraint(
            "evidence_level IN ('BANK_CONFIRMED', 'BANK_OBSERVED', 'USER_DECLARED', "
            "'MODEL_INFERRED', 'USER_CONFIRMED_POLICY', 'USER_CONFIRMED_ACTION')",
            name="evidence_level",
        ),
        CheckConstraint(
            "status IN ('VALID', 'CONFLICTED', 'UNKNOWN', 'SUPERSEDED')", name="status"
        ),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_window"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
        CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id", name="not_self_superseding"
        ),
        Index("ix_evidence_items_user_source", "user_id", "source_type", "source_ref"),
    )


class Transaction(OwnedMixin, Base):
    __tablename__ = "transactions"
    account_id: Mapped[UUID]
    evidence_id: Mapped[UUID | None]
    source_ref: Mapped[str] = mapped_column(String(160))
    direction: Mapped[str] = mapped_column(String(8))
    amount_cents: Mapped[int] = mapped_column(MoneyCents())
    balance_after_cents: Mapped[int | None] = mapped_column(MoneyCents())
    category: Mapped[str] = mapped_column(String(48))
    counterparty_ref: Mapped[str | None] = mapped_column(String(160))
    is_one_off: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    category_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    __table_args__ = owned_args(
        owned_reference("account_id", "accounts"),
        owned_reference("evidence_id", "evidence_items"),
        UniqueConstraint("account_id", "source_ref", name="uq_transactions_account_source"),
        CheckConstraint("amount_cents > 0", name="positive_amount"),
        CheckConstraint("balance_after_cents >= 0", name="nonnegative_balance_after"),
        CheckConstraint("direction IN ('CREDIT', 'DEBIT')", name="direction"),
        Index("ix_transactions_account_occurred", "account_id", "occurred_at"),
    )


class CreditCardBill(OwnedMixin, Base):
    __tablename__ = "credit_card_bills"
    account_id: Mapped[UUID]
    evidence_id: Mapped[UUID | None]
    source_ref: Mapped[str] = mapped_column(String(160))
    statement_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    total_cents: Mapped[int] = mapped_column(MoneyCents())
    minimum_due_cents: Mapped[int] = mapped_column(MoneyCents())
    paid_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    status: Mapped[str] = mapped_column(String(24), server_default="UNPAID")
    __table_args__ = owned_args(
        owned_reference("account_id", "accounts"),
        owned_reference("evidence_id", "evidence_items"),
        UniqueConstraint("account_id", "source_ref", name="uq_credit_card_bills_account_source"),
        CheckConstraint(
            "0 <= minimum_due_cents AND minimum_due_cents <= total_cents", name="amounts"
        ),
        CheckConstraint("0 <= paid_cents AND paid_cents <= total_cents", name="paid_amount"),
        CheckConstraint("due_date >= statement_date", name="due_date"),
        CheckConstraint("status IN ('UNPAID', 'PARTIALLY_PAID', 'PAID', 'OVERDUE')", name="status"),
    )


class AssetProduct(IdentityMixin, Base):
    __tablename__ = "asset_products"
    product_code: Mapped[str] = mapped_column(String(64))
    version_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    asset_class: Mapped[str] = mapped_column(String(32))
    risk_level: Mapped[int] = mapped_column(Integer, server_default="0")
    principal_fluctuation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    minimum_purchase_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    lock_days: Mapped[int] = mapped_column(Integer, server_default="0")
    redemption_delay_days: Mapped[int] = mapped_column(Integer, server_default="0")
    annual_yield_bps: Mapped[int] = mapped_column(Integer, server_default="0")
    early_withdrawal_loss_bps: Mapped[int] = mapped_column(Integer, server_default="0")
    maturity_rule: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    early_withdrawal_rule: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    auto_purchase_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    auto_redeem_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime())
    effective_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (
        UniqueConstraint("product_code", "version_number", name="uq_asset_products_code_version"),
        CheckConstraint("version_number > 0", name="positive_version"),
        CheckConstraint("risk_level BETWEEN 0 AND 5", name="risk_level"),
        CheckConstraint("minimum_purchase_cents >= 0", name="nonnegative_minimum"),
        CheckConstraint("lock_days >= 0 AND redemption_delay_days >= 0", name="durations"),
        CheckConstraint("annual_yield_bps BETWEEN 0 AND 10000", name="yield_range"),
        CheckConstraint("early_withdrawal_loss_bps BETWEEN 0 AND 10000", name="loss_range"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from", name="effective_window"
        ),
        CheckConstraint(
            "asset_class IN ('CASH', 'CASH_MGMT_T0', 'CASH_MGMT_T1', 'FIXED_DEPOSIT', "
            "'FIXED_DEPOSIT_7D', 'FIXED_DEPOSIT_30D', 'FIXED_DEPOSIT_90D', 'LOW_RISK_TERM')",
            name="asset_class",
        ),
    )


class Policy(OwnedMixin, Base):
    __tablename__ = "policies"
    name: Mapped[str] = mapped_column(String(120))
    policy_type: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), server_default="PROPOSED")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    __table_args__ = owned_args(
        CheckConstraint(
            "status IN ('DISCOVERED', 'PROPOSED', 'CONFIRMED', 'ACTIVE', 'MODIFIED', "
            "'SUSPENDED', 'EXPIRED', 'REVOKED')",
            name="status",
        )
    )


class PolicyVersion(OwnedMixin, Base):
    __tablename__ = "policy_versions"
    policy_id: Mapped[UUID]
    version_number: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[JsonObject] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text, server_default="")
    confirmation: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime())
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    change_reason: Mapped[str] = mapped_column(Text, server_default="")
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    impact_analysis: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = owned_args(
        owned_reference("policy_id", "policies"),
        UniqueConstraint("policy_id", "version_number", name="uq_policy_versions_policy_version"),
        UniqueConstraint("id", "policy_id", "user_id", name="uq_policy_versions_id_policy_user"),
        CheckConstraint("version_number > 0", name="positive_version"),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="valid_window",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
        CheckConstraint(
            "previous_hash IS NULL OR previous_hash ~ '^[0-9a-f]{64}$'", name="previous_hash"
        ),
        CheckConstraint("jsonb_typeof(configuration) = 'object'", name="configuration_object"),
        CheckConstraint("jsonb_typeof(evidence_ids) = 'array'", name="evidence_array"),
    )


class Goal(OwnedMixin, Base):
    __tablename__ = "goals"
    policy_id: Mapped[UUID]
    policy_version_id: Mapped[UUID]
    account_id: Mapped[UUID | None]
    name: Mapped[str] = mapped_column(String(120))
    target_cents: Mapped[int] = mapped_column(MoneyCents())
    allocated_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    deadline: Mapped[date] = mapped_column(Date)
    monthly_min_cents: Mapped[int] = mapped_column(MoneyCents())
    monthly_target_cents: Mapped[int] = mapped_column(MoneyCents())
    monthly_max_cents: Mapped[int] = mapped_column(MoneyCents())
    importance: Mapped[int] = mapped_column(Integer, server_default="50")
    minimum_protection_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    reducible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deferrable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cross_goal_reallocation_allowed: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    asset_policy_id: Mapped[UUID | None]
    __table_args__ = owned_args(
        owned_reference("policy_id", "policies"),
        owned_reference("account_id", "accounts"),
        owned_reference("asset_policy_id", "policies"),
        ForeignKeyConstraint(
            ["policy_version_id", "policy_id", "user_id"],
            ["policy_versions.id", "policy_versions.policy_id", "policy_versions.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("policy_id", name="uq_goals_policy"),
        CheckConstraint("target_cents > 0 AND allocated_cents >= 0", name="target_amounts"),
        CheckConstraint(
            "0 <= monthly_min_cents AND monthly_min_cents <= monthly_target_cents "
            "AND monthly_target_cents <= monthly_max_cents",
            name="monthly_range",
        ),
        CheckConstraint("minimum_protection_cents >= 0", name="minimum_protection"),
        CheckConstraint("importance BETWEEN 0 AND 100", name="importance"),
    )


class AssetPosition(OwnedMixin, Base):
    __tablename__ = "asset_positions"
    account_id: Mapped[UUID]
    product_id: Mapped[UUID] = mapped_column(ForeignKey("asset_products.id", ondelete="RESTRICT"))
    goal_id: Mapped[UUID | None]
    policy_version_id: Mapped[UUID | None]
    principal_cents: Mapped[int] = mapped_column(MoneyCents())
    accrued_yield_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    purchased_at: Mapped[datetime] = mapped_column(UTCDateTime())
    maturity_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(24), server_default="HELD")
    __table_args__ = owned_args(
        owned_reference("account_id", "accounts"),
        owned_reference("goal_id", "goals"),
        owned_reference("policy_version_id", "policy_versions"),
        CheckConstraint("principal_cents >= 0 AND accrued_yield_cents >= 0", name="amounts"),
        CheckConstraint("maturity_at IS NULL OR maturity_at >= purchased_at", name="maturity"),
        CheckConstraint(
            "available_at IS NULL OR available_at >= purchased_at", name="availability"
        ),
        CheckConstraint(
            "status IN ('HELD', 'REDEEMING', 'REDEEMED', 'MATURED', 'UNKNOWN')", name="status"
        ),
    )


class PolicyProposal(OwnedMixin, Base):
    __tablename__ = "policy_proposals"
    source_type: Mapped[str] = mapped_column(String(48))
    source_text: Mapped[str] = mapped_column(Text, server_default="")
    compiler_version: Mapped[str] = mapped_column(String(64))
    proposed_configuration: Mapped[JsonObject] = mapped_column(JSONB)
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(24), server_default="PROPOSED")
    confirmed_policy_id: Mapped[UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    __table_args__ = owned_args(
        owned_reference("confirmed_policy_id", "policies"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_policy_proposals_user_idempotency"),
        CheckConstraint(
            "status IN ('PROPOSED', 'CONFIRMED', 'REJECTED', 'EXPIRED')", name="status"
        ),
        CheckConstraint(
            "jsonb_typeof(proposed_configuration) = 'object'", name="configuration_object"
        ),
        CheckConstraint("jsonb_typeof(evidence_ids) = 'array'", name="evidence_array"),
    )


class DecisionRun(OwnedMixin, Base):
    __tablename__ = "decision_runs"
    parent_run_id: Mapped[UUID | None]
    subject_action_plan_id: Mapped[UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    trigger_type: Mapped[str] = mapped_column(String(48))
    algorithm_version: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime())
    input_snapshot: Mapped[JsonObject] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    policy_version_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    result: Mapped[JsonObject] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(24), server_default="PENDING")
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("parent_run_id", "decision_runs"),
        ForeignKeyConstraint(
            ["subject_action_plan_id", "user_id"],
            ["action_plans.id", "action_plans.user_id"],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_decision_runs_subject_action_plan_id_action_plans",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_decision_runs_user_idempotency"),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="snapshot_hash"),
        CheckConstraint("status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'BLOCKED')", name="status"),
        Index("ix_decision_runs_user_as_of", "user_id", "as_of"),
        Index("ix_decision_runs_user_subject", "user_id", "subject_action_plan_id", "as_of", "id"),
        Index("ix_decision_runs_user_parent", "user_id", "parent_run_id", "as_of", "id"),
    )


class DecisionConstraint(OwnedMixin, Base):
    __tablename__ = "decision_constraints"
    decision_run_id: Mapped[UUID]
    policy_version_id: Mapped[UUID | None]
    constraint_key: Mapped[str] = mapped_column(String(120))
    is_hard: Mapped[bool] = mapped_column(Boolean)
    satisfied: Mapped[bool | None] = mapped_column(Boolean)
    required_cents: Mapped[int | None] = mapped_column(MoneyCents())
    available_cents: Mapped[int | None] = mapped_column(MoneyCents())
    due_date: Mapped[date | None] = mapped_column(Date)
    calculation: Mapped[JsonObject] = mapped_column(JSONB)
    reason_code: Mapped[str] = mapped_column(String(80))
    __table_args__ = owned_args(
        owned_reference("decision_run_id", "decision_runs"),
        owned_reference("policy_version_id", "policy_versions"),
        UniqueConstraint(
            "decision_run_id", "constraint_key", name="uq_decision_constraints_run_key"
        ),
        CheckConstraint("required_cents >= 0", name="nonnegative_required"),
    )


class ActionPlan(OwnedMixin, Base):
    __tablename__ = "action_plans"
    decision_run_id: Mapped[UUID]
    policy_version_id: Mapped[UUID | None]
    source_account_id: Mapped[UUID]
    destination_account_id: Mapped[UUID | None]
    goal_id: Mapped[UUID | None]
    product_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("asset_products.id", ondelete="RESTRICT")
    )
    position_id: Mapped[UUID | None]
    action_type: Mapped[str] = mapped_column(String(48))
    amount_cents: Mapped[int] = mapped_column(MoneyCents())
    autonomy_level: Mapped[str] = mapped_column(String(24), server_default="BLOCKED")
    status: Mapped[str] = mapped_column(String(24), server_default="PLANNED")
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[JsonObject] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    authorized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("decision_run_id", "decision_runs"),
        owned_reference("policy_version_id", "policy_versions"),
        owned_reference("source_account_id", "accounts"),
        owned_reference("destination_account_id", "accounts"),
        owned_reference("goal_id", "goals"),
        owned_reference("position_id", "asset_positions"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_action_plans_user_idempotency"),
        CheckConstraint("amount_cents > 0", name="positive_amount"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
        CheckConstraint(
            "autonomy_level IN ('AUTO_EXECUTE', 'ASK_ONCE', 'ADVISE_ONLY', 'BLOCKED')",
            name="autonomy_level",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'AUTHORIZED', 'SUBMITTED', 'SUCCEEDED', 'FAILED', 'UNKNOWN', "
            "'INVALIDATED', 'CANCELLED', 'RECONCILED')",
            name="status",
        ),
        CheckConstraint(
            "destination_account_id IS NULL OR destination_account_id <> source_account_id",
            name="distinct_accounts",
        ),
        Index("ix_action_plans_user_status", "user_id", "status"),
    )


class ActionReceipt(OwnedMixin, Base):
    __tablename__ = "action_receipts"
    action_plan_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    receipt_ref: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24))
    executed_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    fee_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    loss_cents: Mapped[int] = mapped_column(MoneyCents(), server_default="0")
    response: Mapped[JsonObject] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("action_plan_id", "action_plans"),
        UniqueConstraint(
            "action_plan_id", "attempt_number", name="uq_action_receipts_action_attempt"
        ),
        UniqueConstraint("user_id", "receipt_ref", name="uq_action_receipts_user_ref"),
        CheckConstraint("attempt_number > 0", name="positive_attempt"),
        CheckConstraint(
            "executed_cents >= 0 AND fee_cents >= 0 AND loss_cents >= 0", name="amounts"
        ),
        CheckConstraint("status IN ('SUCCEEDED', 'FAILED', 'UNKNOWN')", name="status"),
    )


class AuditEvent(OwnedMixin, Base):
    __tablename__ = "audit_events"
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    aggregate_type: Mapped[str] = mapped_column(String(48))
    aggregate_id: Mapped[UUID]
    correlation_id: Mapped[UUID]
    causation_id: Mapped[UUID | None]
    decision_run_id: Mapped[UUID | None]
    action_plan_id: Mapped[UUID | None]
    action_receipt_id: Mapped[UUID | None]
    idempotency_key: Mapped[str] = mapped_column(String(160))
    payload_version: Mapped[int] = mapped_column(Integer, server_default="1")
    payload: Mapped[JsonObject] = mapped_column(JSONB)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    __table_args__ = owned_args(
        owned_reference("decision_run_id", "decision_runs"),
        owned_reference("action_plan_id", "action_plans"),
        owned_reference("action_receipt_id", "action_receipts"),
        owned_reference("causation_id", "audit_events"),
        UniqueConstraint("user_id", "sequence_number", name="uq_audit_events_user_sequence"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_audit_events_user_idempotency"),
        UniqueConstraint("user_id", "event_hash", name="uq_audit_events_user_hash"),
        CheckConstraint(
            "sequence_number > 0 AND payload_version > 0", name="positive_sequence_version"
        ),
        CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="event_hash"),
        CheckConstraint(
            "previous_hash IS NULL OR previous_hash ~ '^[0-9a-f]{64}$'", name="previous_hash"
        ),
        Index("ix_audit_events_user_occurred", "user_id", "occurred_at"),
    )


class SimulatedBankRedemption(OwnedMixin, Base):
    __tablename__ = "simulated_bank_redemptions"
    action_plan_id: Mapped[UUID]
    position_id: Mapped[UUID]
    destination_account_id: Mapped[UUID]
    product_id: Mapped[UUID] = mapped_column(ForeignKey("asset_products.id", ondelete="RESTRICT"))
    goal_id: Mapped[UUID | None]
    principal_cents: Mapped[int] = mapped_column(MoneyCents())
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[JsonObject] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(24), server_default="ACCEPTED")
    __table_args__ = owned_args(
        owned_reference("action_plan_id", "action_plans"),
        owned_reference("position_id", "asset_positions"),
        owned_reference("destination_account_id", "accounts"),
        owned_reference("goal_id", "goals"),
        UniqueConstraint("action_plan_id", name="uq_simulated_bank_redemptions_action"),
        UniqueConstraint("position_id", name="uq_simulated_bank_redemptions_position"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_simulated_bank_redemptions_key"),
        CheckConstraint("principal_cents > 0", name="principal"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint("available_at >= requested_at", name="availability"),
        CheckConstraint("status IN ('ACCEPTED', 'SETTLED', 'UNKNOWN')", name="status"),
        CheckConstraint(
            "(status = 'SETTLED' AND settled_at IS NOT NULL AND settled_at >= available_at) "
            "OR (status <> 'SETTLED' AND settled_at IS NULL)",
            name="settlement",
        ),
    )


class SimulatedBankPosting(OwnedMixin, Base):
    __tablename__ = "simulated_bank_postings"
    ledger_key: Mapped[str] = mapped_column(String(160))
    ledger_dimension: Mapped[str] = mapped_column(String(32), server_default="ECONOMIC")
    ledger_metadata: Mapped[JsonObject] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    account_id: Mapped[UUID | None]
    position_id: Mapped[UUID | None]
    redemption_id: Mapped[UUID | None]
    operation_id: Mapped[UUID | None]
    leg_ref: Mapped[str | None] = mapped_column(String(160))
    previous_posting_id: Mapped[UUID | None]
    sequence_number: Mapped[int] = mapped_column(Integer)
    entry_kind: Mapped[str] = mapped_column(String(32))
    balance_before_cents: Mapped[int] = mapped_column(MoneyCents())
    delta_cents: Mapped[int] = mapped_column(MoneyCents())
    balance_after_cents: Mapped[int] = mapped_column(MoneyCents())
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("account_id", "accounts"),
        owned_reference("redemption_id", "simulated_bank_redemptions"),
        owned_reference("operation_id", "bank_operations"),
        owned_reference("previous_posting_id", "simulated_bank_postings"),
        UniqueConstraint(
            "user_id", "ledger_key", "sequence_number", name="uq_bank_posting_sequence"
        ),
        UniqueConstraint("redemption_id", "entry_kind", name="uq_bank_posting_redemption_leg"),
        UniqueConstraint("operation_id", "leg_ref", name="uq_bank_posting_operation_leg"),
        CheckConstraint("sequence_number > 0", name="sequence"),
        CheckConstraint(
            "balance_before_cents >= 0 AND balance_after_cents >= 0 "
            "AND balance_after_cents = balance_before_cents + delta_cents",
            name="conservation",
        ),
        CheckConstraint(
            "(ledger_dimension = 'ECONOMIC' AND ((account_id IS NOT NULL "
            "AND position_id IS NULL AND ledger_key = 'CASH:' || account_id::text) OR "
            "(account_id IS NULL AND position_id IS NOT NULL "
            "AND ledger_key = 'POSITION:' || position_id::text) OR "
            "(account_id IS NULL AND position_id IS NULL AND "
            "(ledger_key LIKE 'PAYEE:%' OR ledger_key LIKE 'FEE:%' OR ledger_key LIKE 'LOSS:%')))) "
            "OR (ledger_dimension IN ('GOAL_OWNERSHIP', 'INCOME_LOCATION', 'LIABILITY') "
            "AND position_id IS NULL)",
            name="ledger_identity",
        ),
        CheckConstraint(
            "(entry_kind = 'OPENING' AND redemption_id IS NULL AND operation_id IS NULL "
            "AND leg_ref IS NULL AND sequence_number = 1 "
            "AND previous_posting_id IS NULL AND balance_before_cents = 0 AND delta_cents >= 0) "
            "OR (entry_kind <> 'OPENING' AND operation_id IS NOT NULL AND leg_ref IS NOT NULL "
            "AND previous_posting_id IS NOT NULL AND sequence_number > 1)",
            name="entry",
        ),
        CheckConstraint("jsonb_typeof(ledger_metadata) = 'object'", name="ledger_metadata"),
    )


class BankOperation(OwnedMixin, Base):
    __tablename__ = "bank_operations"
    action_plan_id: Mapped[UUID]
    legacy_redemption_id: Mapped[UUID | None]
    closing_position_id: Mapped[UUID | None]
    operation_type: Mapped[str] = mapped_column(String(48))
    business_key: Mapped[str] = mapped_column(String(160))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request: Mapped[JsonObject] = mapped_column(JSONB)
    request_hash: Mapped[str] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime] = mapped_column(UTCDateTime())
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(24), server_default="ACCEPTED")
    __table_args__ = owned_args(
        owned_reference("action_plan_id", "action_plans"),
        owned_reference("legacy_redemption_id", "simulated_bank_redemptions"),
        UniqueConstraint("action_plan_id", name="uq_bank_operations_action"),
        UniqueConstraint("legacy_redemption_id", name="uq_bank_operations_legacy"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_bank_operations_key"),
        Index(
            "uq_bank_operations_business",
            "user_id",
            "business_key",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
        ),
        Index(
            "uq_bank_operations_closing_position",
            "closing_position_id",
            unique=True,
            postgresql_where=text("status <> 'REJECTED'"),
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash"),
        CheckConstraint("jsonb_typeof(request) = 'object'", name="request_object"),
        CheckConstraint("available_at >= requested_at", name="availability"),
        CheckConstraint("status IN ('ACCEPTED', 'SETTLED', 'UNKNOWN', 'REJECTED')", name="status"),
        CheckConstraint(
            "(status = 'SETTLED' AND settled_at IS NOT NULL AND "
            "settled_at >= available_at) OR (status <> 'SETTLED' AND settled_at IS NULL)",
            name="settlement",
        ),
    )


class ActionResourceReservation(OwnedMixin, Base):
    __tablename__ = "action_resource_reservations"
    action_plan_id: Mapped[UUID]
    resource_kind: Mapped[str] = mapped_column(String(32))
    resource_key: Mapped[str] = mapped_column(String(160))
    amount_cents: Mapped[int] = mapped_column(MoneyCents())
    status: Mapped[str] = mapped_column(String(24), server_default="RESERVED")
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = owned_args(
        owned_reference("action_plan_id", "action_plans"),
        UniqueConstraint(
            "action_plan_id", "resource_kind", "resource_key", name="uq_action_resource"
        ),
        CheckConstraint("amount_cents > 0", name="positive_amount"),
        CheckConstraint(
            "resource_kind IN ('CASH', 'INCOME', 'GOAL_CASH', 'MANAGED', 'POSITION', "
            "'OBLIGATION', 'BUSINESS')",
            name="resource_kind",
        ),
        CheckConstraint("status IN ('RESERVED', 'CONSUMED', 'RELEASED')", name="status"),
        CheckConstraint(
            "(status = 'RESERVED' AND resolved_at IS NULL) OR "
            "(status <> 'RESERVED' AND resolved_at IS NOT NULL)",
            name="resolution",
        ),
        Index("ix_action_resource_active", "user_id", "resource_kind", "resource_key", "status"),
    )
