"""Explicit monthly repetition assumptions, isolated from executable money facts."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.boundary_types import BoundaryModel
from app.domain.income_ledger import IncomeOrigin
from app.domain.policy_configuration import (
    CalendarDate,
    MoneyCents,
    UUIDReference,
    configuration_hash,
)
from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]


class FutureIncomeCandidateRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    origin_transaction_id: UUIDReference
    expected_origin_hash: Hash
    idempotency_key: Key


class FutureIncomeConfirmationRequest(BoundaryModel):
    expected_epoch_id: UUIDReference
    candidate_id: UUIDReference
    reviewed_candidate_hash: Hash
    accepted: StrictBool
    idempotency_key: Key

    @field_validator("accepted")
    @classmethod
    def explicit_acceptance(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit acceptance of this conditional assumption is required")
        return value


class PlanningIncomeSource(BoundaryModel):
    user_id: UUID
    origin: IncomeOrigin
    origin_hash: Hash
    ledger_evidence_id: UUID
    ledger_evidence_hash: Hash
    qualification: Literal["COMPLETE_ORIGINAL_INCOME_LEDGER"] = "COMPLETE_ORIGINAL_INCOME_LEDGER"
    implies_recurring_salary: Literal[False] = False
    bank_promises_future_payment: Literal[False] = False

    @model_validator(mode="after")
    def original_origin_hash(self) -> "PlanningIncomeSource":
        if self.origin_hash != source_hash(self.origin):
            raise ValueError("Original income source hash does not match")
        return self


class FutureIncomeAssumption(BoundaryModel):
    protocol: Literal["future-income-monthly-assumption-v1"] = "future-income-monthly-assumption-v1"
    user_id: UUID
    epoch_id: UUID
    origin_transaction_id: UUID
    source_account_id: UUID
    origin_hash: Hash
    original_bank_evidence_id: UUID
    original_bank_evidence_hash: Hash
    conditional_amount_cents: Annotated[MoneyCents, Field(gt=0)]
    timezone: Literal["Asia/Shanghai", "UTC"]
    monthly_local_day: Annotated[StrictInt, Field(ge=1, le=31)]
    valid_from: CalendarDate
    valid_until: CalendarDate
    basis: Literal["USER_DECLARED_HYPOTHETICAL_MONTHLY_REPETITION"] = (
        "USER_DECLARED_HYPOTHETICAL_MONTHLY_REPETITION"
    )
    short_month_rule: Literal["CLAMP_TO_LAST_CALENDAR_DAY"] = "CLAMP_TO_LAST_CALENDAR_DAY"
    arrival_time_within_day_known: Literal[False] = False
    income_is_settled_cash: Literal[False] = False
    included_in_current_cash_cents: Literal[0] = 0
    included_in_execution_cents: Literal[0] = 0
    grants_authority: Literal[False] = False

    @model_validator(mode="after")
    def finite_window(self) -> "FutureIncomeAssumption":
        if not 0 <= (self.valid_until - self.valid_from).days <= 364:
            raise ValueError("A conditional registration covers at most 365 calendar dates")
        return self


class FutureIncomeCandidate(BoundaryModel):
    candidate_id: UUID
    user_id: UUID
    epoch_id: UUID
    admitted_at: datetime
    confirmation_deadline: datetime
    source: PlanningIncomeSource
    assumption: FutureIncomeAssumption
    candidate_hash: Hash
    original_request: FutureIncomeCandidateRequest
    request_hash: Hash
    original_evidence: dict[str, Any]
    evidence_hash: Hash
    state: Literal["REQUIRES_EXPLICIT_CONFIRMATION", "EXPIRED", "USER_CONFIRMED", "UNKNOWN"]
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False


class FutureIncomeConfirmation(BoundaryModel):
    confirmation_id: UUID
    user_id: UUID
    epoch_id: UUID
    candidate_id: UUID
    candidate_hash: Hash
    confirmed_at: datetime
    original_request: FutureIncomeConfirmationRequest
    request_hash: Hash
    original_evidence: dict[str, Any]
    evidence_hash: Hash
    simulation: Literal[True] = True
    grants_authority: Literal[False] = False
    confirms_financial_action: Literal[False] = False
    dedicated_audit_event_recorded: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False


class FutureIncomeSourceInventory(BoundaryModel):
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID | None
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    status: Literal["VERIFIED_ORIGINAL_INCOME_SOURCES", "UNKNOWN"]
    original_origin_count: StrictInt | None
    captured_origin_count: StrictInt
    complete: bool
    sources: list[PlanningIncomeSource]
    issues: list[str]
    grants_authority: Literal[False] = False
    included_in_current_cash_cents: Literal[0] = 0
    included_in_execution_cents: Literal[0] = 0


class FutureIncomePlanState(BoundaryModel):
    candidate_id: UUID
    confirmation_id: UUID | None
    state: Literal[
        "USER_CONFIRMED_CONDITION",
        "REQUIRES_EXPLICIT_CONFIRMATION",
        "EXPIRED",
        "UNKNOWN",
        "CONFLICTED",
    ]
    candidate: FutureIncomeCandidate | None
    confirmation: FutureIncomeConfirmation | None
    original_metadata: list[dict[str, Any]]
    issues: list[str]


class ConditionalIncomeDay(BoundaryModel):
    day: Annotated[StrictInt, Field(ge=1, le=365)]
    date: date
    conditional_income_cents: MoneyCents | None
    candidate_ids: list[UUID]
    is_settled_cash: Literal[False] = False
    availability_within_day_known: Literal[False] = False


class FutureIncomePlanningResponse(BoundaryModel):
    protocol: Literal["future-income-conditional-planning-v1"] = (
        "future-income-conditional-planning-v1"
    )
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID | None
    as_of: datetime
    timezone: Literal["Asia/Shanghai", "UTC"]
    horizon_days: Literal[365] = 365
    status: Literal["CONDITIONAL_PLANNING", "NO_CONFIRMED_REGISTERED_SOURCE", "UNKNOWN"]
    sources: FutureIncomeSourceInventory
    plans: list[FutureIncomePlanState]
    total_conditional_income_cents: MoneyCents | None
    daily_schedule: Annotated[list[ConditionalIncomeDay], Field(min_length=365, max_length=365)]
    input_hash: Hash
    issues: list[str]
    included_in_current_cash_cents: Literal[0] = 0
    included_in_execution_cents: Literal[0] = 0
    writes_financial_facts: Literal[False] = False
    grants_authority: Literal[False] = False
    bank_promises_future_payment: Literal[False] = False
    original_execution_view_changed: Literal[False] = False


class FutureIncomeCommandLookup(BoundaryModel):
    protocol: Literal["future-income-command-lookup-v1"] = "future-income-command-lookup-v1"
    simulation: Literal[True] = True
    user_id: UUID
    epoch_id: UUID
    idempotency_key: Key
    status: Literal["RECORDED", "NOT_FOUND_NOT_FINAL"]
    command_kind: Literal["CANDIDATE", "CONFIRM"] | None
    original_request: FutureIncomeCandidateRequest | FutureIncomeConfirmationRequest | None
    request_hash: Hash | None
    candidate: FutureIncomeCandidate | None
    confirmation: FutureIncomeConfirmation | None
    not_found_is_final: Literal[False] = False
    replacement_allowed: Literal[False] = False
    grants_authority: Literal[False] = False
    receipt_is_current_authority: Literal[False] = False


def assumption_for_source(
    source: PlanningIncomeSource,
    epoch_id: UUID,
    now: datetime,
    timezone: Literal["Asia/Shanghai", "UTC"],
) -> FutureIncomeAssumption:
    local = now.astimezone(ZoneInfo(timezone)).date()
    origin_day = source.origin.occurred_at.astimezone(ZoneInfo(timezone)).day
    return FutureIncomeAssumption(
        user_id=source.user_id,
        epoch_id=epoch_id,
        origin_transaction_id=source.origin.origin_transaction_id,
        source_account_id=source.origin.origin_account_id,
        origin_hash=source.origin_hash,
        original_bank_evidence_id=source.origin.bank_evidence_id,
        original_bank_evidence_hash=source.origin.bank_evidence_hash,
        conditional_amount_cents=source.origin.amount_cents,
        timezone=timezone,
        monthly_local_day=origin_day,
        valid_from=local + timedelta(days=1),
        valid_until=local + timedelta(days=365),
    )


def conditional_schedule(
    as_of: datetime,
    timezone: Literal["Asia/Shanghai", "UTC"],
    assumptions: list[tuple[UUID, FutureIncomeAssumption]] | None,
) -> list[ConditionalIncomeDay]:
    """Return a date-only scenario schedule, never a BoundarySnapshot or cash fact."""
    first = as_of.astimezone(ZoneInfo(timezone)).date()
    if assumptions is not None and any(item.timezone != timezone for _, item in assumptions):
        raise ValueError("Registered source timezone must match this planning view")
    rows = []
    for day in range(1, 366):
        current = first + timedelta(days=day)
        matches = (
            []
            if assumptions is None
            else [
                (identity, item)
                for identity, item in assumptions
                if item.timezone == timezone
                and item.valid_from <= current <= item.valid_until
                and current.day
                == min(item.monthly_local_day, monthrange(current.year, current.month)[1])
            ]
        )
        amount = (
            None
            if assumptions is None
            else sum(item.conditional_amount_cents for _, item in matches)
        )
        rows.append(
            ConditionalIncomeDay(
                day=day,
                date=current,
                conditional_income_cents=amount,
                candidate_ids=sorted(identity for identity, _ in matches),
            )
        )
    return rows


def source_hash(origin: IncomeOrigin) -> str:
    return configuration_hash(origin.model_dump(mode="json"))
