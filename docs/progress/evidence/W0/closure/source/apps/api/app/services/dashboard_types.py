"""One same-snapshot homepage; financial facts do not grant execution authority."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from app.api.v1.accounts import AccountSummary
from app.domain.autonomy_types import AutonomyDecision
from app.domain.boundary_details_types import NextObligations
from app.domain.boundary_types import BlockingConstraint
from app.services.boundary import BoundarySourceIssue
from pydantic import BaseModel, ConfigDict, Field

ProofState = Literal["PROVEN", "NOT_PROVEN", "INCOMPLETE"]


class DashboardModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AccountFactsCard(DashboardModel):
    state: ProofState
    facts: AccountSummary
    bank_projection_state: Literal["MATCHED", "NOT_PROVEN"]
    issues: list[BoundarySourceIssue] = Field(default_factory=list)


class FinancialBoundaryCard(DashboardModel):
    state: ProofState
    financial_only: Literal[True] = True
    status: Literal["READY", "LIQUIDITY_RISK", "INSUFFICIENT_EVIDENCE"]
    safe_idle_cents: int | None
    minimum_margin_cents: int | None
    deficit_cents: int | None
    protected_cents_by_reason: dict[str, int] | None
    current_protected_cents: int | None
    current_protected_cents_by_reason: dict[str, int] | None
    current_margin_cents: int | None
    constraining_date: date | None
    window_start: date
    window_end: date
    input_digest: str
    boundary_hash: str
    blocking_constraints: list[BlockingConstraint]
    calculation_notes: list[str]
    issues: list[BoundarySourceIssue] = Field(default_factory=list)


class GoalOwnershipItem(DashboardModel):
    goal_id: UUID
    policy_id: UUID
    account_id: UUID | None
    name: str
    cash_owned_cents: int
    principal_owned_cents: int
    allocated_cents: int
    evidence_ids: list[UUID]


class GoalOwnershipCard(DashboardModel):
    state: ProofState
    cash_owned_cents: int | None
    principal_owned_cents: int | None
    allocated_cents: int | None
    unassigned_goal_cash_cents: int | None
    items: list[GoalOwnershipItem]
    issues: list[BoundarySourceIssue] = Field(default_factory=list)


class ManagedGoalAmount(DashboardModel):
    goal_id: UUID
    principal_cents: int
    pending_purchase_cents: int


class ManagedAssetsCard(DashboardModel):
    state: ProofState
    managed_current_principal_cents: int | None
    general_principal_cents: int | None
    held_or_matured_cents: int | None
    redeeming_cents: int | None
    pending_purchase_cents: int | None
    by_goal: list[ManagedGoalAmount]
    excluded_manual_count: int
    unknown_position_count: int
    evidence_ids: list[UUID]
    issues: list[BoundarySourceIssue] = Field(default_factory=list)


class PendingActionItem(DashboardModel):
    action_id: UUID
    decision_run_id: UUID
    action_type: str
    amount_cents: int | None
    status: str
    prepared_level: str
    prepared_at: datetime
    current_decision: AutonomyDecision | None
    effect_hash: str | None
    fee_cents: int | None
    loss_cents: int | None
    bank_operation_id: UUID | None
    bank_status: str | None
    bank_state_proven: bool
    receipt_id: UUID | None
    receipt_status: str | None
    receipt_verified: bool
    audit_status: str
    reason_codes: list[str]


class PendingActionsCard(DashboardModel):
    state: ProofState
    total: int
    items: list[PendingActionItem]
    list_complete: bool
    has_more: bool


class RecoveryProposalItem(DashboardModel):
    run_id: UUID
    as_of: datetime
    status: str
    original_status: str
    fee_cents: int | None
    loss_cents: int | None
    audit_status: str
    reason_codes: list[str]


class RecoveryProposalsCard(DashboardModel):
    state: ProofState
    total: int
    items: list[RecoveryProposalItem]
    list_complete: bool
    has_more: bool


class InterventionCard(DashboardModel):
    status: Literal[
        "NONE", "CONFIRMATION_REQUIRED", "REVIEW_REQUIRED", "RECONCILIATION_REQUIRED", "NOT_PROVEN"
    ]
    known_required_count: int
    complete: bool
    reason_codes: list[str]


class DashboardAuditCard(DashboardModel):
    scope: Literal["CURRENT_LIVE_EPOCH"] = "CURRENT_LIVE_EPOCH"
    epoch_id: UUID | None
    status: str
    anchored_run_statuses: dict[UUID, str]
    complete: bool


class DashboardResponse(DashboardModel):
    schema_version: Literal["dashboard-v1"] = "dashboard-v1"
    simulation: Literal[True] = True
    user_id: UUID
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    account_facts: AccountFactsCard
    boundary: FinancialBoundaryCard
    goal_ownership: GoalOwnershipCard
    managed_assets: ManagedAssetsCard
    next_obligations: NextObligations
    pending_actions: PendingActionsCard
    recovery_proposals: RecoveryProposalsCard
    intervention: InterventionCard
    audit: DashboardAuditCard
    source_evidence_ids: list[UUID]
