"""Display facts; these never enter the financial v1 hash or trace."""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from app.domain.boundary_types import BlockingConstraint, BoundaryModel, BoundaryResult, SourceIssue
from app.domain.policy_configuration import MoneyCents
from pydantic import Field, StrictInt, model_validator

DisplayStatus = Literal["PROVEN", "NOT_PROVEN"]
NonNegativeTotal = Annotated[StrictInt, Field(ge=0)]
ProtectionReason = Literal["obligations", "living", "emergency", "goal_cash", "goal_minimum"]
TotalBasis = Literal["BILL_ACTUAL", "POLICY_EXACT", "POLICY_RANGE_MAX", "SETTLEMENT_FINAL"]
PaymentFact = Literal[
    "BILL_CONFIRMED",
    "SETTLEMENT_CONFIRMED",
    "NO_IMPORT_CURRENT_OR_FUTURE",
    "MISSING_HISTORICAL_IMPORT",
]


class ObligationOccurrence(BoundaryModel):
    occurrence_id: str
    kind: Literal["CREDIT_CARD_BILL", "RECURRING_ORDINARY"]
    bill_id: UUID | None
    account_id: UUID | None
    policy_id: UUID | None
    policy_version_id: UUID | None
    period: str | None
    payee_id: str | None
    due_date: date
    projection_payment_date: date
    overdue: bool
    protected_total_cents: MoneyCents
    remaining_protection_cents: MoneyCents
    total_basis: TotalBasis
    actual_final_total_cents: MoneyCents | None
    paid_cents: MoneyCents | None
    payment_fact: PaymentFact
    evidence_ids: list[UUID]


class NextObligations(BoundaryModel):
    status: DisplayStatus
    selection_scope: Literal["KNOWN_PROTECTION_COMMITMENTS_DUE_BY_WINDOW_END_INCLUDING_OVERDUE"] = (
        "KNOWN_PROTECTION_COMMITMENTS_DUE_BY_WINDOW_END_INCLUDING_OVERDUE"
    )
    next_due_date: date | None
    next_count: NonNegativeTotal | None
    next_remaining_protection_cents: NonNegativeTotal | None
    basis_summary: Literal["EXACT", "UPPER_BOUND", "MIXED"] | None
    items: Annotated[list[ObligationOccurrence], Field(max_length=20)]
    items_complete: bool

    @model_validator(mode="after")
    def proof_shape(self) -> Self:
        if self.status == "NOT_PROVEN":
            if (
                any(
                    value is not None
                    for value in (
                        self.next_due_date,
                        self.next_count,
                        self.next_remaining_protection_cents,
                        self.basis_summary,
                    )
                )
                or self.items
                or self.items_complete
            ):
                raise ValueError("An unproved next group cannot publish precise facts")
        elif self.next_count is None or self.next_remaining_protection_cents is None:
            raise ValueError("A proved next group must publish its full count and protection")
        elif self.next_count == 0:
            if (
                self.next_due_date is not None
                or self.next_remaining_protection_cents != 0
                or self.basis_summary is not None
                or self.items
                or not self.items_complete
            ):
                raise ValueError("A proved empty group has no date, amount or basis")
        elif (
            self.next_due_date is None
            or self.basis_summary is None
            or not self.items
            or len(self.items) != min(20, self.next_count)
            or self.items_complete != (self.next_count <= 20)
            or any(item.due_date != self.next_due_date for item in self.items)
        ):
            raise ValueError("Next group details do not match their full-group summary")
        return self


class ProtectionValue(BoundaryModel):
    date: date
    phase: Literal["BEFORE_PAYMENT"] = "BEFORE_PAYMENT"
    amounts_by_reason: dict[str, NonNegativeTotal]
    total_cents: NonNegativeTotal
    cash_cents: StrictInt
    margin_cents: StrictInt

    @model_validator(mode="after")
    def exact_current_point(self) -> Self:
        if set(self.amounts_by_reason) != {
            "obligations",
            "living",
            "emergency",
            "goal_cash",
            "goal_minimum",
        }:
            raise ValueError("Current protection requires the five financial v1 reasons")
        if self.total_cents != sum(self.amounts_by_reason.values()):
            raise ValueError("Current protection total differs from its reasons")
        if self.margin_cents != self.cash_cents - self.total_cents:
            raise ValueError("Current margin differs from the original trace point")
        return self


class CurrentProtection(BoundaryModel):
    status: DisplayStatus
    value: ProtectionValue | None

    @model_validator(mode="after")
    def proof_shape(self) -> Self:
        if (self.status == "PROVEN") != (self.value is not None):
            raise ValueError("Current protection must explicitly state whether it is proved")
        return self


class GoalOwnershipItem(BoundaryModel):
    goal_id: UUID
    policy_id: UUID
    account_id: UUID | None
    cash_owned_cents: MoneyCents
    principal_owned_cents: MoneyCents
    allocated_cents: MoneyCents
    evidence_ids: list[UUID]
    principal_position_ids: list[UUID]


class UnassignedGoalCashItem(BoundaryModel):
    account_id: UUID
    amount_cents: MoneyCents
    evidence_ids: list[UUID]


class CurrentGoalOwnership(BoundaryModel):
    status: DisplayStatus
    items: Annotated[list[GoalOwnershipItem], Field(max_length=100)]
    cash_owned_cents: NonNegativeTotal | None
    principal_owned_cents: NonNegativeTotal | None
    allocated_cents: NonNegativeTotal | None
    unassigned_goal_cash: Annotated[list[UnassignedGoalCashItem], Field(max_length=100)]
    unassigned_goal_cash_cents: NonNegativeTotal | None

    @model_validator(mode="after")
    def proof_shape(self) -> Self:
        amounts = (
            self.cash_owned_cents,
            self.principal_owned_cents,
            self.allocated_cents,
            self.unassigned_goal_cash_cents,
        )
        if self.status == "NOT_PROVEN":
            if (
                any(value is not None for value in amounts)
                or self.items
                or self.unassigned_goal_cash
            ):
                raise ValueError("Unproved ownership cannot publish a complete precise partition")
        elif (
            any(value is None for value in amounts)
            or self.cash_owned_cents != sum(item.cash_owned_cents for item in self.items)
            or self.principal_owned_cents != sum(item.principal_owned_cents for item in self.items)
            or self.allocated_cents != sum(item.allocated_cents for item in self.items)
            or self.unassigned_goal_cash_cents
            != sum(item.amount_cents for item in self.unassigned_goal_cash)
        ):
            raise ValueError("Ownership summaries must cover all adopted original facts")
        return self


class BoundaryDisplayDetails(BoundaryModel):
    schema_version: Literal["boundary-display-v1"] = "boundary-display-v1"
    simulation: Literal[True] = True
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    window_start: date
    window_end: date
    input_digest: str
    boundary_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    next_obligations: NextObligations
    current_protection: CurrentProtection
    current_goal_ownership: CurrentGoalOwnership
    blocking_constraints: list[BlockingConstraint]
    source_issues: list[SourceIssue]


class BoundaryComputation(BoundaryModel):
    boundary: BoundaryResult
    details: BoundaryDisplayDetails
