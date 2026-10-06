"""Strict financial facts for the MVP boundary; provenance is checked by its adapter."""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from app.domain.policy_configuration import MoneyCents
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

CalendarDate = date


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="after")
    def aware_timestamps(self) -> Self:
        for value in self.__dict__.values():
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("Boundary timestamps must be timezone-aware")
        return self


class SourcedFact(BoundaryModel):
    evidence_ids: list[UUID] = Field(default_factory=list)


class SourceIssue(BoundaryModel):
    code: str
    entity_type: str
    entity_id: str | None = None


class CashFact(SourcedFact):
    account_id: UUID
    account_type: Literal["CASH", "GOAL", "CREDIT_CARD", "CASH_MANAGEMENT", "FIXED_DEPOSIT"]
    balance_cents: MoneyCents
    observed_at: datetime


class BillFact(SourcedFact):
    bill_id: UUID
    account_id: UUID
    statement_date: date
    due_date: date
    total_cents: MoneyCents
    paid_cents: MoneyCents
    status: Literal["UNPAID", "PARTIALLY_PAID", "PAID", "OVERDUE"]


class SettlementFact(SourcedFact):
    policy_id: UUID
    period: Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
    paid_cents: MoneyCents
    settled_at: datetime
    final_total_cents: MoneyCents | None = None

    @model_validator(mode="after")
    def paid_within_final_total(self) -> Self:
        if self.final_total_cents is not None and self.paid_cents > self.final_total_cents:
            raise ValueError("Paid amount exceeds the confirmed final occurrence total")
        return self


class GoalOwnership(SourcedFact):
    goal_id: UUID
    policy_id: UUID
    account_id: UUID | None = None
    cash_owned_cents: MoneyCents
    principal_owned_cents: MoneyCents
    allocated_cents: MoneyCents

    @model_validator(mode="after")
    def allocation_is_partitioned(self) -> Self:
        if self.cash_owned_cents + self.principal_owned_cents != self.allocated_cents:
            raise ValueError("Goal allocated amount must equal owned cash plus principal")
        return self


class GoalMonthFact(SourcedFact):
    goal_id: UUID
    period: Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]
    contributed_cents: MoneyCents


class LivingReserveFact(BoundaryModel):
    policy_version_id: UUID
    amount_cents: MoneyCents
    estimation_input_digest: str


class UnassignedGoalCash(SourcedFact):
    account_id: UUID
    amount_cents: MoneyCents


class BoundarySnapshot(BoundaryModel):
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    horizon_days: Annotated[StrictInt, Field(ge=1, le=365)] = 90
    cash_accounts: Annotated[list[CashFact], Field(max_length=100)]
    bills: Annotated[list[BillFact], Field(max_length=10000)] = Field(default_factory=list)
    occurrence_settlements: Annotated[list[SettlementFact], Field(max_length=10000)] = Field(
        default_factory=list
    )
    goals: Annotated[list[GoalOwnership], Field(max_length=100)] = Field(default_factory=list)
    goal_month_contributions: Annotated[list[GoalMonthFact], Field(max_length=1000)] = Field(
        default_factory=list
    )
    living_reserves: Annotated[list[LivingReserveFact], Field(max_length=100)] = Field(
        default_factory=list
    )
    unassigned_goal_cash: Annotated[list[UnassignedGoalCash], Field(max_length=100)] = Field(
        default_factory=list
    )
    source_issues: Annotated[list[SourceIssue], Field(max_length=1000)] = Field(
        default_factory=list
    )
    source_digest: str = ""


class BoundaryPolicyVersion(SourcedFact):
    policy_id: UUID
    version_id: UUID
    configuration: dict[str, Any]
    content_hash: str
    confirmed_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None


class BoundaryPosition(SourcedFact):
    position_id: UUID
    goal_id: UUID | None = None
    principal_cents: MoneyCents
    status: Literal["HELD", "REDEEMING", "MATURED", "REDEEMED", "UNKNOWN"]
    principal_available_at: datetime | None = None
    availability_evidence_ids: list[UUID] = Field(default_factory=list)


class FixedReturnTerms(BoundaryModel):
    term_days: Annotated[StrictInt, Field(ge=0, le=3660)]
    settlement_delay_days: Annotated[StrictInt, Field(ge=0, le=3660)]
    principal_return_bps: Literal[10000] = 10000
    rollover: Literal[False] = False


class BoundaryProduct(BoundaryModel):
    product_id: UUID
    version_number: Annotated[StrictInt, Field(gt=0)]
    asset_class: str
    minimum_purchase_cents: MoneyCents = 0
    fixed_return: FixedReturnTerms | None = None
    terms_digest: str


class BlockingConstraint(BoundaryModel):
    code: str
    entity_id: str | None = None
    date: CalendarDate | None = None
    required_cents: MoneyCents | None = None
    available_cents: StrictInt | None = None


class BoundaryPoint(BoundaryModel):
    day: int
    date: CalendarDate
    phase: Literal["BEFORE_PAYMENT", "AFTER_PAYMENT", "AFTER_PRINCIPAL"]
    cash_cents: StrictInt
    protected_cents_by_reason: dict[str, int]
    margin_cents: StrictInt
    obligation_occurrence_ids: list[str]
    principal_position_ids: list[UUID]


class BoundaryResult(BoundaryModel):
    algorithm_version: str
    status: Literal["READY", "LIQUIDITY_RISK", "INSUFFICIENT_EVIDENCE"]
    financial_only: Literal[True] = True
    safe_idle_cents: MoneyCents | None
    minimum_margin_cents: StrictInt | None
    deficit_cents: MoneyCents | None
    protected_cents_by_reason: dict[str, int]
    max_allocatable_by_product: dict[str, int | None]
    blocking_constraints: list[BlockingConstraint]
    calculation_trace: list[BoundaryPoint]
    boundary_hash: str
    calculation_notes: list[str] = Field(default_factory=list)
