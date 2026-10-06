"""Trusted external simulator facts, separate from the five Agent bank commands."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveCents = Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
NonnegativeCents = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
BoundedReference = Annotated[str, Field(min_length=1, max_length=160)]


class ExternalModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ExternalFactRequest(ExternalModel):
    protocol_version: Literal["bank-external-fact-v1"] = "bank-external-fact-v1"
    simulation: Literal[True] = True
    user_id: UUID
    idempotency_key: BoundedReference
    source_id: Literal["bounded-funds-external-v1"] = "bounded-funds-external-v1"
    external_ref: BoundedReference
    kind: Literal["INCOME", "CONSUMPTION"]
    account_id: UUID
    amount_cents: PositiveCents
    currency: Literal["CNY"] = "CNY"
    counterparty_ref: Annotated[str, Field(min_length=1, max_length=96)]
    occurred_at: AwareDatetime

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ExternalCashAttribution(ExternalModel):
    """Server-proven before state; none of these amounts is browser input."""

    account_id: UUID
    bank_balance_before_cents: NonnegativeCents
    goal_cash_owned_cents: NonnegativeCents
    non_income_claim_cents: NonnegativeCents
    complete: Literal[True] = True


class ExternalIncomeUse(ExternalModel):
    fragment_id: UUID
    origin_transaction_id: UUID
    account_id: UUID
    amount_cents: PositiveCents


class ExternalConsumptionPlan(ExternalModel):
    status: Literal["READY", "UNRECONCILED"]
    reason_code: BoundedReference
    uses: tuple[ExternalIncomeUse, ...] = ()
    untracked_spent_cents: NonnegativeCents = 0

    @model_validator(mode="after")
    def no_partial_plan(self) -> Self:
        if self.status != "READY" and (self.uses or self.untracked_spent_cents):
            raise ValueError("An unreconciled debit cannot expose a partial projection")
        return self


class ExternalSettlementResult(ExternalModel):
    protocol_version: Literal["bank-external-settlement-v1"] = "bank-external-settlement-v1"
    simulation: Literal[True] = True
    user_id: UUID
    external_fact_id: UUID
    request_hash: Digest
    settled_at: AwareDatetime
    economic_posting_ids: tuple[UUID, UUID]
    posting_digest: Digest


class ExternalProjectionResult(ExternalModel):
    protocol_version: Literal["bank-external-projection-v1"] = "bank-external-projection-v1"
    simulation: Literal[True] = True
    user_id: UUID
    external_fact_id: UUID
    request_hash: Digest
    bank_result_hash: Digest
    projected_at: AwareDatetime
    transaction_id: UUID
    transaction_evidence_id: UUID
    balance_evidence_id: UUID
    income_evidence_id: UUID
    exposure_evidence_id: UUID
    proof_successor_ids: tuple[UUID, ...]
    memo_posting_ids: tuple[UUID, ...]
    memo_posting_digest: Digest
    income_uses: tuple[ExternalIncomeUse, ...] = ()
    untracked_spent_cents: NonnegativeCents = 0

    @model_validator(mode="after")
    def unique_originals(self) -> Self:
        for identities in (self.proof_successor_ids, self.memo_posting_ids):
            if len(identities) != len(set(identities)):
                raise ValueError("Projection originals must be unique")
        if len({use.fragment_id for use in self.income_uses}) != len(self.income_uses):
            raise ValueError("Income fragments must not be consumed twice")
        if not {
            self.balance_evidence_id,
            self.income_evidence_id,
            self.exposure_evidence_id,
        }.issubset(self.proof_successor_ids):
            raise ValueError("Projection successors must include every financial proof")
        return self


class ExternalFactResult(ExternalModel):
    simulation: Literal[True] = True
    external_fact_id: UUID
    bank_status: Literal["ACCEPTED", "SETTLED", "UNKNOWN", "REJECTED"]
    projection_status: Literal["PENDING", "UNKNOWN", "PROJECTED"]
    economic_posting_ids: tuple[UUID, ...] = ()
    transaction_id: UUID | None = None
    projection_error: BoundedReference | None = None

    @model_validator(mode="after")
    def economic_result(self) -> Self:
        if len(self.economic_posting_ids) != (2 if self.bank_status == "SETTLED" else 0):
            raise ValueError("Only a complete settlement may expose its two economic legs")
        if (self.transaction_id is not None) != (self.projection_status == "PROJECTED"):
            raise ValueError("Only a complete projection may expose a transaction")
        return self
